from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from sqlalchemy import text

from .db import db, one, rows
from .config import settings
from .engine import as_json_list, as_json_object, engine_client
from .fresh_core import from_connector
from .media_tools import optimize_image_bytes, optimize_video_path, resolve_upload_path, validate_image_bytes, thumbnail_path, file_mime, UPLOAD_ROOT, VIDEO_EXTS, require_pillow
from .repositories import log_ai_event, queue_job
from .workflows import workflow_by_key, conversion_workflows, post_formats
from .legacy_features import (
    check_destination, connector_preflight, hold_publication, media_public_url,
    notify, publish_post, recommended_time, select_links, tracking_url, regenerate_post_link,
    wordpress_categories, suggest_wordpress_category,
)

SUPPORTED_JOB_TYPES = {
    "project_profile",
    "ai_activity",
    "article_generate",
    "social_derivatives",
    "wordpress_sync",
    "wp_publish_blog",
    "wp_publish_post",
    "social_publish_post",
    "analytics_sync",
    "image_generate_post",
    "image_generate_blog",
    "carousel_slide",
    "image_generate_carousel_slide",
    "free_image_generate",
    "batch_group",
    "sitemap_async",
    "brand_analyze",
    "linda_chat",
    "rewrite_post_text",
    "media_maintenance",
    "proactive_review",
    "manual_post_generate",
}

TASK_ROUTING = {
    # Router 2.x uses generic capability in task_type and the concrete Fresh app
    # action in operation. App-specific task_type values are not router capabilities.
    "project_profile": ("analysis", "analysis.project_profile", "balanced"),
    "ai_activity": ("marketing", "marketing.campaign.plan", "balanced"),
    "article_generate": ("marketing", "marketing.article.create", "balanced"),
    "social_derivatives": ("marketing", "marketing.social.create", "economy"),
    "linda_chat": ("chat", "chat.conversation", "balanced"),
}



MANUAL_POST_SCHEMA = {
    "type":"object",
    "additionalProperties":False,
    "properties":{
        "title":{"type":"string"},
        "body":{"type":"string"},
        "hashtags":{"type":"string"},
        "image_prompt":{"type":"string"},
        "destination_query":{"type":"string"},
    },
    "required":["title","body","hashtags","image_prompt","destination_query"],
}

POST_REWRITE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "hashtags": {"type": "string"},
    },
    "required": ["title", "body", "hashtags"],
}

PROJECT_PROFILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "business_summary": {"type": "string"},
        "main_offer": {"type": "string"},
        "target_audience": {"type": "string"},
        "tone_of_voice": {"type": "string"},
        "marketing_goal": {"type": "string"},
        "primary_cta": {"type": "string"},
        "secondary_cta": {"type": "string"},
        "content_pillars": {"type": "array", "items": {"type": "string"}},
        "products_services": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                    "benefit": {"type": "string"},
                },
                "required": ["name", "url", "benefit"],
            },
        },
        "trust_signals": {"type": "array", "items": {"type": "string"}},
        "avoid_claims": {"type": "array", "items": {"type": "string"}},
        "recommended_topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "business_summary", "main_offer", "target_audience", "tone_of_voice",
        "marketing_goal", "primary_cta", "secondary_cta", "content_pillars",
        "products_services", "trust_signals", "avoid_claims", "recommended_topics",
    ],
}

ACTIVITY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "goal": {"type": "string"},
        "strategy_summary": {"type": "string"},
        "recommended_steps": {"type": "array", "items": {"type": "string"}},
        "missing_data": {"type": "array", "items": {"type": "string"}},
        "articles": {"type": "array", "items": {"type": "object"}},
        "posts": {"type": "array", "items": {"type": "object"}},
        "diagnostics": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["title", "goal", "strategy_summary", "recommended_steps", "missing_data", "articles", "posts", "diagnostics"],
}

CHAT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reply": {"type": "string"},
        "recommended_workflow": {"type": "string"},
        "needs_user_input": {"type": "boolean"},
        "suggested_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reply", "recommended_workflow", "needs_user_input", "suggested_actions"],
}

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "slug": {"type": "string"},
        "excerpt": {"type": "string"},
        "body_html": {"type": "string"},
        "seo_title": {"type": "string"},
        "seo_description": {"type": "string"},
        "social_teaser": {"type": "string"},
        "image_prompt": {"type": "string"},
        "internal_links": {"type": "array", "items": {"type": "object"}},
        "primary_cta_url": {"type": "string"},
        "category_name": {"type": "string"},
    },
    "required": ["title", "slug", "excerpt", "body_html", "seo_title", "seo_description", "social_teaser", "image_prompt", "internal_links", "primary_cta_url", "category_name"],
}

SOCIAL_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "platform": {"type": "string"},
            "post_type": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "image_prompt": {"type": "string"},
            "carousel_slides": {"type": "array"},
            "hashtags": {"type": "array"},
            "date": {"type": "string"},
            "time": {"type": "string"},
        },
        "required": ["platform", "post_type", "title", "body", "image_prompt", "carousel_slides", "hashtags", "date", "time"],
    },
}


def _payload(job: dict[str, Any]) -> dict[str, Any]:
    raw = job.get("payload")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _project(project_id: int) -> dict[str, Any]:
    item = one("SELECT * FROM projects WHERE id=:id LIMIT 1", {"id": project_id})
    if not item:
        raise RuntimeError("Projekt nebyl nalezen.")
    return item


def _knowledge(project_id: int, limit: int = 90) -> str:
    items = rows(
        "SELECT source_type,title,url,summary,content_body,meta_json FROM project_knowledge "
        "WHERE project_id=:pid AND status='active' ORDER BY traffic DESC,modified_at DESC,id DESC LIMIT :limit",
        {"pid": project_id, "limit": limit},
    )
    out: list[str] = []
    for item in items:
        body = str(item.get("content_body") or item.get("summary") or "")
        if len(body) > 5000:
            body = body[:5000]
        commerce = ""
        if str(item.get("source_type") or "") == "product" and item.get("meta_json"):
            try:
                meta = json.loads(str(item.get("meta_json") or "{}"))
                c = meta.get("commerce") if isinstance(meta, dict) else {}
                if isinstance(c, dict) and c:
                    commerce = (
                        f"\nProdukt: cena={c.get('price') or ''} {c.get('currency') or ''}; "
                        f"běžná={c.get('regular_price') or ''}; akce={c.get('sale_price') or ''}; "
                        f"sklad={c.get('stock_status') or ''}; featured={bool(c.get('featured'))}"
                    )
            except Exception:
                commerce = ""
        out.append(
            f"[{item.get('source_type')}] {item.get('title') or ''}\nURL: {item.get('url') or ''}{commerce}\n{body}"
        )
    return "\n\n".join(out)


def _knowledge_for_chat(project_id: int, message: str, limit: int = 12) -> str:
    """Small relevance-first knowledge slice for interactive chat.

    Chat must stay responsive and inexpensive even on large websites; campaign/article
    workers can still use the wider `_knowledge` context.
    """
    items = rows(
        "SELECT source_type,title,url,summary,content_body,meta_json,traffic FROM project_knowledge "
        "WHERE project_id=:pid AND status='active' ORDER BY traffic DESC,modified_at DESC,id DESC LIMIT 80",
        {"pid": project_id},
    )
    query_terms = {x for x in re.findall(r"[a-zá-ž0-9]{4,}", str(message or "").lower()) if x not in {"který","která","které","jsem","jsme","chci","potřebuji","linda","lindo","tento","tahle","máme","udělat"}}
    ranked = []
    for idx, item in enumerate(items):
        hay = (str(item.get("title") or "") + " " + str(item.get("summary") or "") + " " + str(item.get("content_body") or "")[:2500]).lower()
        score = sum(5 if term in str(item.get("title") or "").lower() else 1 for term in query_terms if term in hay)
        score += min(3, int(item.get("traffic") or 0) // 100)
        ranked.append((score, -idx, item))
    ranked.sort(reverse=True, key=lambda x: (x[0], x[1]))
    chosen = [x[2] for x in ranked[:max(4, min(limit, 16))]]
    out=[]
    for item in chosen:
        body = str(item.get("summary") or item.get("content_body") or "")
        if len(body) > 1400:
            body = body[:1400] + "…"
        commerce = ""
        if str(item.get("source_type") or "") == "product" and item.get("meta_json"):
            try:
                meta=json.loads(str(item.get("meta_json") or "{}")); c=meta.get("commerce") if isinstance(meta,dict) else {}
                if isinstance(c,dict) and c:
                    commerce=f"\nProdukt: cena={c.get('price') or ''} {c.get('currency') or ''}; sklad={c.get('stock_status') or ''}"
            except Exception:
                pass
        out.append(f"[{item.get('source_type')}] {item.get('title') or ''}\nURL: {item.get('url') or ''}{commerce}\n{body}")
    return "\n\n".join(out)


def _html_word_count(value: str) -> int:
    clean = re.sub(r"<[^>]+>", " ", str(value or ""))
    clean = re.sub(r"&[a-zA-Z0-9#]+;", " ", clean)
    return len(re.findall(r"\b[\wÀ-ž0-9][\wÀ-ž0-9'’-]*\b", clean, flags=re.UNICODE))


def _article_link_candidates(project_id: int, topic: str, project: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    context = f"{topic} {_profile(project)}"
    return select_links(project_id, context, [], limit, 0)


def _article_links_prompt(links: list[dict[str, Any]]) -> str:
    if not links:
        return "Nejsou dostupné ověřené interní odkazy. Nevymýšlej žádné URL."
    return "\n".join(
        f"- {x.get('title') or x.get('anchor') or 'Odkaz'} | {x.get('type') or 'page'} | {x.get('url') or ''}"
        for x in links
    )


def _ensure_article_links(body: str, links: list[dict[str, Any]], primary_cta_url: str = "") -> str:
    """Deterministically keep useful internal links even if the model omits them.

    Fresh Core/project_knowledge remains the source of truth for URLs. We never invent
    a URL here; the model may choose anchors, while this fallback adds a compact
    contextual section and CTA from verified project links.
    """
    html_body = str(body or "").strip()
    if not links:
        return html_body
    html_body = re.sub(r'<aside class=["\']linda-related-links["\']>.*?</aside>', '', html_body, flags=re.I | re.S).rstrip()
    used=[]
    for x in links[:5]:
        url=str(x.get('url') or '').strip()
        if not url or url in used:
            continue
        used.append(url)
    if not used:
        return html_body
    rows_html=[]
    for x in links:
        url=str(x.get('url') or '').strip()
        if url not in used:
            continue
        label=str(x.get('anchor') or x.get('title') or url).strip()
        rows_html.append(f'<li><a href="{url}">{label}</a></li>')
    related=f'<aside class="linda-related-links"><h2>Související informace a další kroky</h2><ul>{"".join(rows_html)}</ul></aside>'
    cta=str(primary_cta_url or '').strip()
    valid_urls={str(x.get('url') or '').strip() for x in links}
    if cta not in valid_urls:
        preferred=next((x for x in links if str(x.get('type') or '') in {'product','page'}), links[0])
        cta=str(preferred.get('url') or '').strip()
    cta_html=f'<p class="linda-article-cta"><a href="{cta}">Zjistit více na webu</a></p>' if cta else ''
    return html_body + "\n" + related + "\n" + cta_html


def _engine_image_payload(value: Any) -> dict[str, str]:
    """Normalize image outputs from Fresh AI Engine/provider adapters.

    Engine image adapters may return a direct URL/base64 value, OpenAI-style
    ``data`` arrays, ``images``/``artifacts`` collections, or a MIME-described
    binary payload. Keep this deliberately tolerant so an Engine upgrade does
    not silently turn a completed image run into a failed Linda job.
    """
    import re

    def looks_like_base64(raw: str) -> bool:
        clean = raw.strip()
        if len(clean) < 256:
            return False
        # Long provider image payloads are normally standard/url-safe base64.
        return re.fullmatch(r"[A-Za-z0-9+/=_\-\r\n]+", clean) is not None

    def walk(v: Any, depth: int = 0, parent: dict[str, Any] | None = None) -> dict[str, str] | None:
        if depth > 10:
            return None
        if isinstance(v, (bytes, bytearray)):
            return {'base64': base64.b64encode(bytes(v)).decode('ascii')}
        if isinstance(v, str):
            raw=v.strip()
            if raw.startswith('data:image/'):
                return {'base64': raw}
            if raw.startswith(('http://','https://')):
                return {'url': raw}
            if raw.startswith(('{','[')):
                try:
                    return walk(json.loads(raw), depth+1)
                except Exception:
                    pass
            mime = str((parent or {}).get('mime_type') or (parent or {}).get('mime') or (parent or {}).get('content_type') or '').lower()
            if mime.startswith('image/') and looks_like_base64(raw):
                return {'base64': raw, 'mime_type': mime}
            if looks_like_base64(raw):
                return {'base64': raw}
            return None
        if isinstance(v, list):
            for item in v:
                found=walk(item, depth+1, parent)
                if found:
                    return found
            return None
        if not isinstance(v, dict):
            return None

        mime = str(v.get('mime_type') or v.get('mime') or v.get('content_type') or v.get('media_type') or '').lower()
        for key in ('base64','b64_json','image_base64','data_url','b64','content_base64','binary_base64'):
            raw=v.get(key)
            if isinstance(raw,str) and raw.strip():
                return {'base64': raw.strip(), **({'mime_type': mime} if mime else {})}
        for key in ('url','image_url','download_url','file_url','signed_url'):
            raw=v.get(key)
            if isinstance(raw,str) and raw.strip().startswith(('http://','https://')):
                return {'url': raw.strip(), **({'mime_type': mime} if mime else {})}

        # Some adapters return {type:'image', mime_type:'image/png', data:'...'}
        # or the same payload in content/bytes. Treat it as binary before walking
        # generic wrappers, otherwise the long base64 string looks like plain text.
        imageish = mime.startswith('image/') or str(v.get('type') or v.get('kind') or '').lower() in {'image','image_base64','generated_image'}
        if imageish:
            for key in ('data','content','bytes','value','body'):
                raw=v.get(key)
                if isinstance(raw,str) and raw.strip():
                    found=walk(raw, depth+1, v)
                    if found:
                        return found

        for key in ('images','artifacts','media','files','attachments','data','result','output','outputs','content','image','response','payload','value'):
            if key in v:
                found=walk(v.get(key), depth+1, v)
                if found:
                    return found
        for nested in v.values():
            if isinstance(nested,(dict,list)):
                found=walk(nested, depth+1, v)
                if found:
                    return found
        return None
    return walk(value) or {}


def _history(project_id: int, limit: int = 40) -> str:
    items = rows(
        "SELECT platform,title,body,status,scheduled_at,published_at,destination_url FROM posts "
        "WHERE project_id=:pid ORDER BY id DESC LIMIT :limit",
        {"pid": project_id, "limit": limit},
    )
    return "\n".join(
        f"{x.get('platform')} | {x.get('title')} | {str(x.get('body') or '')[:500]} | {x.get('destination_url') or ''}"
        for x in items
    )


def _brand(project: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Tone of voice: {project.get('brand_tone') or ''}",
            f"Visual style: {project.get('visual_style') or ''}",
            f"Style DNA: {project.get('brand_style_dna') or ''}",
            f"Overrides: {project.get('brand_style_overrides') or ''}",
        ]
    )


def _profile(project: dict[str, Any]) -> str:
    if project.get("ai_profile"):
        return str(project["ai_profile"])
    return "\n".join(
        [
            f"Projekt: {project.get('name') or ''}",
            f"Web: {project.get('website_url') or ''}",
            f"Nabídka: {project.get('main_offer') or ''}",
            f"Cílovka: {project.get('target_audience') or ''}",
            f"Cíle: {project.get('content_goals') or ''}",
        ]
    )


def _workflow_context(key: str) -> str:
    item = workflow_by_key(key) if key else None
    if item and item.get("enabled", True):
        return (
            f"Zvolená konverzní cesta: {item.get('label') or key}\n"
            f"Popis: {item.get('description') or ''}\n"
            f"Pravidla: {item.get('rules') or ''}"
        )
    enabled = [x for x in conversion_workflows() if x.get("enabled", True)]
    return "Dostupné konverzní cesty:\n" + "\n".join(
        f"- {x.get('key')}: {x.get('label')} — {x.get('description')}" for x in enabled
    )


def _post_formats_context() -> str:
    enabled = [x for x in post_formats() if x.get("enabled", True)]
    return "\n".join(
        f"- {x.get('key')}: {x.get('label')} | platformy: {', '.join(x.get('platforms') or [])} | asset: {x.get('asset')}"
        for x in enabled
    )


def _user_media_context(project_id: int, limit: int = 20) -> str:
    items = rows(
        "SELECT id,title,kind,file_path,status FROM media_assets WHERE project_id=:pid "
        "AND provider='upload' AND file_path IS NOT NULL AND file_path<>'' ORDER BY id DESC LIMIT :limit",
        {"pid": project_id, "limit": limit},
    )
    if not items:
        return "Žádná uživatelská média."
    return "\n".join(
        f"- asset_id={x.get('id')} | {x.get('kind') or 'media'} | {x.get('title') or x.get('file_path')} | {x.get('status')}"
        for x in items
    )


def _connector_platforms(user_id: int, project_id: int) -> list[str]:
    items = rows(
        "SELECT provider FROM social_connectors WHERE user_id=:uid AND project_id=:pid "
        "AND status='connected' ORDER BY provider",
        {"uid": user_id, "pid": project_id},
    )
    return [str(x["provider"]) for x in items if x.get("provider") != "wordpress"]


def _connector_status_context(user_id: int, project_id: int) -> str:
    items = rows(
        "SELECT provider,status,account_name,expires_at,last_test_at,last_test_message,last_preflight_status,last_preflight_message "
        "FROM social_connectors WHERE user_id=:uid AND project_id=:pid ORDER BY provider,id DESC",
        {"uid": user_id, "pid": project_id},
    )
    if not items:
        return "Žádný konektor zatím není uložený."
    clean=[]
    seen=set()
    for item in items:
        provider=str(item.get("provider") or "")
        if provider in seen: continue
        seen.add(provider)
        clean.append({
            "provider":provider,
            "status":str(item.get("status") or ""),
            "account":str(item.get("account_name") or ""),
            "expires_at":str(item.get("expires_at") or ""),
            "last_test":str(item.get("last_test_message") or ""),
            "preflight":str(item.get("last_preflight_message") or ""),
        })
    return json.dumps(clean,ensure_ascii=False,default=str)


def _mark_progress(job_id: int, progress: int, message: str) -> None:
    with db() as conn:
        conn.execute(
            text("UPDATE task_queue SET progress=:progress,message=:message,updated_at=NOW() WHERE id=:id"),
            {"progress": max(0, min(100, progress)), "message": message, "id": job_id},
        )


def _finish(job_id: int, result: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute(
            text(
                "UPDATE task_queue SET status='done',progress=100,message=:message,"
                "result_json=:result,updated_at=NOW(),finished_at=NOW(),locked_at=NULL WHERE id=:id"
            ),
            {
                "message": str(result.get("message") or "Hotovo.")[:2000],
                "result": json.dumps(result, ensure_ascii=False, default=str),
                "id": job_id,
            },
        )


def _job_action_url(job: dict[str, Any]) -> str:
    """Best deep-link for the content affected by a background job."""
    pid = int(job.get("project_id") or 0)
    typ = str(job.get("type") or "")
    payload = _payload(job)
    post_id = int(payload.get("post_id") or 0) if isinstance(payload, dict) else 0
    blog_id = int(payload.get("blog_id") or 0) if isinstance(payload, dict) else 0
    asset_id = int(payload.get("asset_id") or 0) if isinstance(payload, dict) else 0
    if post_id:
        return f"?page=content&project_id={pid}#post-{post_id}"
    if blog_id:
        return f"?page=content&project_id={pid}&view=articles#blog-{blog_id}"
    if asset_id:
        return f"?page=media&project_id={pid}"
    if typ in {"wordpress_sync","project_profile","brand_analyze","proactive_review"}:
        return f"?page=settings&project_id={pid}" if pid else "?page=settings"
    if typ == "analytics_sync":
        return f"?page=analytics&project_id={pid}" if pid else "?page=analytics"
    return "?page=queue"


def _friendly_failure(job: dict[str, Any], raw_message: str) -> dict[str, str]:
    """Translate technical worker errors into a user-facing Czech explanation.

    The raw exception is preserved in result_json for diagnostics/admins, while
    task_queue.message stays understandable for ordinary users.
    """
    msg = str(raw_message or "").strip()
    typ = str(job.get("type") or "")
    pid = int(job.get("project_id") or 0)
    action = _job_action_url(job)
    title = "Úlohu se nepodařilo dokončit."
    friendly = "Linda narazila na technický problém. Data zůstala zachovaná a úlohu můžeš po opravě spustit znovu."

    if "AI Engine vrátil text místo obrázku" in msg or "vrátil text místo obrazového URL/base64" in msg:
        title = "AI Engine vrátil text místo obrázku."
        friendly = "Generátor nedodal obrazový soubor. Obsah zůstal zachovaný; po opravě AI Enginu spusť úlohu znovu."
    elif "AI Engine nevytvořil obrázek" in msg or "chybí obrazová data nebo URL" in msg or "Fresh AI Engine nemá aktivní generování obrázků" in msg:
        title = "AI Engine nevytvořil obrázek."
        friendly = "Linda nedostala použitelný obrázek. Text, termín ani ostatní data se nezměnily; úlohu můžeš po opravě zopakovat."
    elif "nepodařilo se vytvořit náhled" in msg:
        title = "Obrázek se uložil, ale chybí náhled."
        friendly = "Linda obrázek nepovažuje za dokončený, protože se nepodařilo vytvořit náhled. Původní obsah zůstal zachovaný."
    elif "Vygenerovaný soubor není čitelný obrázek" in msg or "neplatný MIME typ" in msg or "poškozený data URL" in msg or "nečitelná obrazová data" in msg:
        title = "Vygenerovaný obrázek není použitelný."
        friendly = "AI Engine sice odpověděl, ale Linda obrazová data nedokázala bezpečně ověřit. Úloha se neuložila jako hotová."
    elif "expired" in msg.lower() and ("token" in msg.lower() or "access" in msg.lower()):
        title = "Přístup k sociální síti vypršel."
        friendly = "Příspěvek zůstal uložený. Znovu připoj příslušný účet v konektorech a potom publikaci zopakuj."
        action = f"?page=connectors&project_id={pid}" if pid else "?page=connectors"
    elif "nedokázala uložit" in msg or "nepodařilo uložit" in msg:
        title = "Vygenerovaný obrázek se nepodařilo uložit."
        friendly = "AI obrázek vznikl, ale Linda ho nedokázala bezpečně uložit do knihovny médií. Obsah zůstal zachovaný."
    elif "nepodařilo se připojit" in msg and "obrázek" in msg.lower():
        title = "Obrázek se nepodařilo připojit k obsahu."
        friendly = "Vizuál byl vytvořený, ale vazba na příspěvek nebo článek se nedokončila. Úloha zůstala jako problémová a lze ji zopakovat."
    elif ("Engine nevrátil očekávaný JSON objekt" in msg or "Engine nevrátil očekávané JSON pole" in msg
          or "structured_output_invalid" in msg or "neplatný strukturovaný výstup" in msg):
        title = "AI Engine vrátil neplatný strukturovaný výstup."
        friendly = "Linda odpověď bezpečně neuložila, protože AI Engine nedodal validní strukturovaná data. Původní obsah zůstal zachovaný. Úloha se nebude opakovat dokola; po opravě AI Engine ji spusť znovu."
        action = f"?page=queue"
    elif "input_data" in msg and "valid dictionary" in msg:
        title = "Starší úloha používá neplatné zadání."
        friendly = "Tato úloha vznikla před opravou komunikace s AI Engine. Vytvoř ji prosím znovu; nově vytvořené úlohy už posílají správná data."
        action = f"?page=dashboard&project_id={pid}" if pid else "?page=dashboard"
    elif "too large" in msg or "max 500000 JSON characters" in msg:
        title = "Podklady jsou příliš velké pro jeden AI požadavek."
        friendly = "Linda se pokusila poslat příliš mnoho dat najednou. Obsah zůstal zachovaný; úlohu je potřeba zopakovat s menším výběrem podkladů."
        action = f"?page=project_setup&project_id={pid}" if pid else "?page=projects"
    elif "Znalostní báze je prázdná" in msg:
        title = "Linda zatím nezná web projektu."
        friendly = "Nejdřív synchronizuj WordPress nebo načti web do znalostní báze. Potom může Linda profil a obsah připravit."
        action = f"?page=knowledge&project_id={pid}" if pid else "?page=projects"
    elif "WordPress / Fresh Core konektor" in msg or "Pro projekt není uložen WordPress" in msg:
        title = "Chybí připojení WordPressu."
        friendly = "Linda nemůže pokračovat, dokud projekt nemá funkční Fresh Core / WordPress konektor. Obsah nebyl ztracen."
        action = f"?page=connectors&project_id={pid}&provider=wordpress" if pid else "?page=connectors"
    elif "Brand kit je prázdný" in msg:
        title = "Brand Kit nemá dost podkladů."
        friendly = "Nahraj alespoň jeden vizuální vzor nebo doplň vizuální styl projektu. Potom Linda dokáže Brand Kit znovu analyzovat."
        action = f"?page=references&project_id={pid}" if pid else "?page=projects"
    elif "Chybí access token" in msg or "K projektu není připojen" in msg:
        title = "Publikační účet není připojený."
        friendly = "Příspěvek zůstal uložený, ale Linda ho nemůže publikovat, dokud nepřipojíš příslušnou síť."
        action = f"?page=connectors&project_id={pid}" if pid else "?page=connectors"
    elif "Fresh Core publish" in msg or "Fresh Core odmítl publikaci" in msg:
        title = "WordPress publikaci odmítl."
        friendly = "Linda článek nepovažuje za publikovaný. Zkontroluj Fresh Core konektor a detail obsahu; po opravě můžeš publikaci zopakovat."
        action = f"?page=connectors&project_id={pid}&provider=wordpress" if pid else "?page=connectors"

    return {"title": title, "message": friendly, "action_url": action, "technical": msg}


def _hold_failed_publication(job: dict[str, Any], friendly: dict[str, str]) -> None:
    """Stop the scheduler from recreating a permanently failed publication every 2 minutes."""
    if str(job.get("type") or "") not in {"wp_publish_post", "social_publish_post"}:
        return
    payload = _payload(job)
    post_id = int(payload.get("post_id") or 0)
    if not post_id:
        return
    with db() as conn:
        conn.execute(text(
            "UPDATE posts SET publication_hold='worker_error',publication_hold_at=NOW(),publication_hold_message=:message "
            "WHERE id=:id"
        ), {"message": friendly.get("message", "Publikace vyžaduje kontrolu.")[:500], "id": post_id})


def _fail(job: dict[str, Any], exc: Exception) -> dict[str, Any]:
    attempts = int(job.get("attempts") or 0) + 1
    max_attempts = int(job.get("max_attempts") or 3)
    technical = str(exc)
    permanent_markers = (
        "Znalostní báze je prázdná",
        "Chybí WordPress / Fresh Core konektor",
        "Pro projekt není uložen WordPress / Fresh Core konektor",
        "Brand kit je prázdný",
        "Fresh AI Engine nemá aktivní generování obrázků",
        "AI Engine vrátil text místo obrázku",
        "AI Engine nevytvořil obrázek",
        "chybí obrazová data nebo URL",
        "neplatný MIME typ",
        "Vygenerovaný soubor není podporovaný rastrový obrázek",
        "na serveru chybí Pillow",
        "Nainstaluj requirements.txt",
        "structured_output_invalid",
        "neplatný strukturovaný výstup",
        "Engine nevrátil očekávaný JSON objekt",
        "Engine nevrátil očekávané JSON pole",
        "K projektu není připojen",
        "Chybí access token",
    )
    permanent = any(marker in technical for marker in permanent_markers)
    retry = (attempts < max_attempts) and not permanent
    friendly = _friendly_failure(job, technical)
    result_payload = {
        "ok": False,
        "friendly_title": friendly["title"],
        "friendly_message": friendly["message"],
        "action_url": friendly["action_url"],
        "technical_error": technical,
        "attempt": attempts,
        "will_retry": retry,
    }
    with db() as conn:
        conn.execute(
            text(
                "UPDATE task_queue SET status=:status,attempts=:attempts,message=:message,result_json=:result,"
                "updated_at=NOW(),finished_at=IF(:retry=1,NULL,NOW()),locked_at=NULL,"
                "available_at=IF(:retry=1,DATE_ADD(NOW(),INTERVAL 5 MINUTE),available_at) "
                "WHERE id=:id"
            ),
            {
                "status": "queued" if retry else "failed",
                "attempts": attempts,
                "message": (("Zkusím znovu: " if retry else "") + friendly["message"])[:4000],
                "result": json.dumps(result_payload, ensure_ascii=False, default=str),
                "retry": 1 if retry else 0,
                "id": int(job["id"]),
            },
        )
    if not retry:
        _hold_failed_publication(job, friendly)
    return {**result_payload, "retry": retry}


def _engine_or_raise(**kwargs: Any):
    res = engine_client.run(**kwargs)
    if not res.ok:
        raise RuntimeError(res.error or "Fresh AI Engine selhal.")
    return res


def worker_project_profile(job: dict[str, Any]) -> dict[str, Any]:
    pid = int(job["project_id"])
    project = _project(pid)
    knowledge = _knowledge(pid)
    if not knowledge:
        raise RuntimeError("Znalostní báze je prázdná. Nejdřív synchronizuj WordPress.")
    prompt = (
        "Jsi Linda, seniorní český marketingový stratég. Z načteného webu sestav praktické "
        "zadání projektu tak, aby uživatel příště zadával hlavně témata. Nevymýšlej nic, co "
        "ve zdrojích není. Piš stručně a srozumitelně.\n\n"
        f"Projekt: {project.get('name')}\nWeb: {project.get('website_url')}\n"
        f"Současná nabídka: {project.get('main_offer') or ''}\n"
        f"Současná cílovka: {project.get('target_audience') or ''}\n\n"
        f"NAČTENÁ DATA WEBU:\n{knowledge}"
    )
    res = _engine_or_raise(
        project_id=pid,
        task_type="analysis",
        operation="analysis.project_profile",
        quality="balanced",
        prompt=prompt,
        context={"project": _profile(project)},
        output_contract=PROJECT_PROFILE_SCHEMA,
    )
    profile = as_json_object(res.result)
    with db() as conn:
        conn.execute(
            text(
                "UPDATE projects SET ai_profile=:profile,ai_profile_updated_at=NOW(),"
                "main_offer=IF(main_offer IS NULL OR main_offer='',:offer,main_offer),"
                "target_audience=IF(target_audience IS NULL OR target_audience='',:aud,target_audience),"
                "brand_tone=IF(brand_tone IS NULL OR brand_tone='',:tone,brand_tone),"
                "content_goals=IF(content_goals IS NULL OR content_goals='',:goal,content_goals) "
                "WHERE id=:pid"
            ),
            {
                "profile": json.dumps(profile, ensure_ascii=False, indent=2),
                "offer": str(profile.get("main_offer") or ""),
                "aud": str(profile.get("target_audience") or ""),
                "tone": str(profile.get("tone_of_voice") or ""),
                "goal": str(profile.get("marketing_goal") or ""),
                "pid": pid,
            },
        )
    log_ai_event(int(job["user_id"]), pid, "project_profile", "Linda sestavila profil projektu přes Fresh AI Engine", res.audit())
    return {"message": "Profil projektu připraven přes Fresh AI Engine.", "engine": res.audit(), "result": profile}


def _chat_prompt(uid: int, pid: int, project: dict[str, Any], message: str, workflow_key: str) -> str:
    history_rows = rows(
        "SELECT event,message,created_at FROM ai_events WHERE user_id=:uid AND project_id=:pid "
        "AND event IN ('chat_user','chat_linda') ORDER BY id DESC LIMIT 18",
        {"uid": uid, "pid": pid},
    )
    history_rows.reverse()
    if history_rows and history_rows[-1].get("event") == "chat_user" and str(history_rows[-1].get("message") or "").strip() == message:
        history_rows = history_rows[:-1]
    conversation = "\n".join(
        ("Uživatel" if x.get("event") == "chat_user" else "Linda") + ": " + str(x.get("message") or "")
        for x in history_rows
    )
    return (
        "Jsi Linda, konkrétní AI Social Media Specialist a marketingová kolegyně uvnitř pracovního projektu. "
        "Mluv přirozeně, stručně a česky. Nejsi obecný chatbot ani formulář. Tvým úkolem je vést pracovní dialog: "
        "pochopit obchodní problém, dohledat relevantní produkt/službu/článek ve znalostech projektu a navrhnout další krok, "
        "který vede ke konverzi. Primární metrika je objednávka, poptávka nebo registrace; návštěvnost a engagement jsou mezikroky. "
        "Když uživatel jen pozdraví nebo položí jednoduchou otázku, normálně odpověz a nenutíš ho hned do kampaně. "
        "Když je potřeba upřesnění, polož nejvýše dvě konkrétní otázky. Nevymýšlej URL ani produkty, které nejsou ve zdrojích. "
        "Nenavrhuj samoúčelné posty. Preferuj cestu: produkt/služba → social → web; nový článek s interními odkazy a CTA → social → článek → produkt; "
        "existující článek → social → konverze; nebo uživatelské video → platformový post → CTA. "
        "Jsi zároveň průvodce aplikací. Když uživatel neví, kam kliknout nebo co nastavit, vysvětli mu to jednoduše a použij konkrétní interní odkazy. "
        "Umíš uživatele provést od založení projektu přes načtení webu, Brand kit, konektory, plánování, schválení, publikaci a výsledky. "
        "Znáš své vlastní možnosti: umíš synchronizovat web přes Fresh Core, obnovit AI profil, analyzovat Brand Kit, připravit plán, články, posty a obrázky, "
        "přeplánovat nepublikovaný obsah, hlídat frontu a po připojení kanálu publikovat. Samotné OAuth přihlášení a výběr cizího účtu musí vždy potvrdit uživatel. "
        "Když můžeš něco připravit sama, řekni to jasně; když je nutný krok uživatele (OAuth, heslo, výběr účtu), vysvětli přesně proč. "
        "Tón je partnerský, přátelský a praktický: klidně začni 'Ahoj, jsem Linda a ráda tě provedu celým procesem. Nejsi na to sám 🙂'. "
        "Nepoužívej technický žargon, pokud není potřeba. Pokud je vhodné vytvořit skutečnou aktivitu, doporuč ji až na konci jako další krok.\n\n"
        f"INTERNÍ ODKAZY A NÁPOVĚDA:\n"
        f"- Konektory: [?page=connectors&project_id={pid}]\n"
        f"- Kalendář: [?page=calendar&project_id={pid}]\n"
        f"- Obsah a schvalování: [?page=content&project_id={pid}]\n"
        f"- Média: [?page=media&project_id={pid}]\n"
        f"- Nastavení projektu: [?page=project_setup&project_id={pid}]\n"
        f"- Znalostní báze: [?page=knowledge&project_id={pid}]\n"
        f"- Brand kit: [?page=references&project_id={pid}]\n"
        f"- Výsledky: [?page=analytics&project_id={pid}]\n"
        f"- Fronta úloh: [?page=queue]\n"
        "Když odkaz doporučíš, piš ho jako markdown, např. [Připojit sociální sítě](?page=connectors&project_id=123).\n\n"
        f"PROJEKT:\n{_profile(project)}\n\nBRAND:\n{_brand(project)}\n\n"
        f"KONVERZNÍ CESTY:\n{_workflow_context(workflow_key)}\n\n"
        f"STAV KONEKTORŮ (bez tajných klíčů):\n{_connector_status_context(uid,pid)}\n\n"
        f"DOSTUPNÉ FORMÁTY:\n{_post_formats_context()}\n\n"
        f"UŽIVATELSKÁ MÉDIA (můžeš doporučit jejich asset_id):\n{_user_media_context(pid)}\n\n"
        f"WEBOVÉ ZNALOSTI (relevantní výběr):\n{_knowledge_for_chat(pid,message,12)}\n\n"
        f"POSLEDNÍ DIALOG:\n{conversation}\n\nNOVÁ ZPRÁVA UŽIVATELE:\n{message}"
    )


def _chat_action_suggestions(uid: int, pid: int, message: str, reply: str) -> list[dict[str, str]]:
    hay = (str(message or "") + " " + str(reply or "")).lower()
    out: list[dict[str, str]] = []
    platforms = _connector_platforms(uid, pid)
    if not platforms and any(k in hay for k in ("publik", "síť", "facebook", "instagram", "linkedin", "google")):
        out.append({"label":"Připojit kanály","href":f"?page=connectors&project_id={pid}"})
    if any(k in hay for k in ("14 dní", "14den", "plán", "naplán")):
        out.append({"label":"Připravit 14denní plán","action":"plan_14_days"})
    if any(k in hay for k in ("přeplán", "staré příspěvky", "starších", "nepublikovan")):
        out.append({"label":"Přeplánovat starší obsah","action":"reschedule_overdue"})
    if any(k in hay for k in ("synchron", "načíst web", "znalost", "webové zdroje")):
        out.append({"label":"Synchronizovat web","action":"sync_web"})
    if any(k in hay for k in ("ai profil", "profil projektu", "obnov profil")):
        out.append({"label":"Obnovit AI profil","action":"refresh_profile"})
    if any(k in hay for k in ("brand", "style dna")):
        out.append({"label":"Analyzovat Brand kit","action":"analyze_brand"})
        out.append({"label":"Otevřít Brand kit","href":f"?page=references&project_id={pid}"})
    if any(k in hay for k in ("zkontroluj projekt", "kontrola projektu", "co chybí", "co je potřeba nastavit")):
        out.append({"label":"Zkontrolovat projekt","action":"project_check"})
    if any(k in hay for k in ("kalend", "termín", "kdy publik")):
        out.append({"label":"Otevřít kalendář","href":f"?page=calendar&project_id={pid}"})
    # Deduplicate and keep the UI deliberately small.
    seen=set(); clean=[]
    for item in out:
        key=item.get("action") or item.get("href") or item.get("label")
        if key in seen: continue
        seen.add(key); clean.append(item)
    return clean[:3]

def run_linda_chat_direct(uid: int, pid: int, message: str, workflow_key: str = "") -> dict[str, Any]:
    """Run the interactive Linda chat immediately through Fresh AI Engine.

    Conversational turns are synchronous and never depend on cron. We prefer a
    dedicated linda_chat route, but keep two compatibility routes so an older
    Engine router cannot silently make the coworker unusable.
    """
    project = _project(pid)
    message = str(message or "").strip()
    workflow_key = str(workflow_key or "").strip()
    if not message:
        raise RuntimeError("Chybí zpráva pro Lindu.")
    prompt = _chat_prompt(uid, pid, project, message, workflow_key)
    context = {
        "agent": "Linda",
        "app": "Fresh AI Social",
        "mode": "interactive_chat",
        "profile": _profile(project),
        "workflow": workflow_key,
    }
    input_data = {"message": message, "workflow_key": workflow_key, "interactive": True}

    routes = [
        # Fresh AI Engine Router 2.x: generic capability + concrete operation.
        ("chat", "chat.conversation", "balanced"),
        ("general", "chat.conversation", "balanced"),
    ]
    attempts: list[dict[str, Any]] = []
    res = None
    used_route = routes[0]
    for index, (task_type, operation, quality) in enumerate(routes):
        used_route = (task_type, operation, quality)
        res = engine_client.run(
            project_id=pid,
            task_type=task_type,
            operation=operation,
            quality=quality,
            prompt=prompt,
            context={**context, "compatibility_attempt": index + 1},
            input_data=input_data,
            # Chat is intentionally plain text. Structured JSON is unnecessary
            # here and used to make the conversational route brittle.
            output_contract=None,
        )
        attempts.append({
            "task_type": task_type,
            "operation": operation,
            "ok": res.ok,
            "http_status": res.http_status,
            "run_id": res.run_id,
            "error": str(res.error or "")[:1200],
            "raw": (res.raw if isinstance(res.raw, dict) else {}) if not res.ok else {},
        })
        if res.ok:
            break
        # Authentication/rate-limit failures will not improve by changing route.
        if res.http_status in {401, 403, 429}:
            break
    if res is None or not res.ok:
        err = (res.error if res else None) or "Fresh AI Engine selhal."
        log_ai_event(uid, pid, "chat_engine_error", "AI Engine nedokončil chat.", {"attempts": attempts, "error": str(err)[:1200]})
        raise RuntimeError(err)

    try:
        data = as_json_object(res.result)
    except Exception:
        data = {}
    if isinstance(data, dict) and not data.get("reply"):
        candidate = None
        if isinstance(res.result, dict):
            for key in ("text", "answer", "message", "output_text", "summary", "recommendation", "reply"):
                if isinstance(res.result.get(key), str) and str(res.result.get(key)).strip():
                    candidate = str(res.result.get(key)).strip(); break
            if candidate is None and isinstance(res.result.get("content"), list):
                texts = []
                for block in res.result.get("content") or []:
                    if isinstance(block, dict) and str(block.get("type") or "") in {"text", "output_text"} and block.get("text"):
                        texts.append(str(block.get("text")))
                if texts:
                    candidate = "\n".join(texts).strip()
        elif isinstance(res.result, str) and res.result.strip():
            candidate = res.result.strip()
        if candidate:
            data = {"reply": candidate, "needs_user_input": False}
    if not isinstance(data, dict):
        data = {}
    reply = str(data.get("reply") or "").strip()
    if not reply:
        log_ai_event(uid, pid, "chat_engine_error", "AI Engine vrátil výstup bez odpovědi Lindy.", {"attempts": attempts, "engine": res.audit()})
        raise RuntimeError("Fresh AI Engine nevrátil odpověď Lindy.")

    audit = res.audit()
    audit.update({
        "task_type": used_route[0],
        "operation": used_route[1],
        "fallback_used": used_route != routes[0],
        "attempts": attempts,
    })
    actions = _chat_action_suggestions(uid, pid, message, reply)
    log_ai_event(uid, pid, "chat_linda", reply, {"engine": audit, "needs_user_input": bool(data.get("needs_user_input")), "actions": actions})
    return {"reply": reply, "engine": audit, "needs_user_input": bool(data.get("needs_user_input")), "actions": actions}


def worker_linda_chat(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    payload = _payload(job)
    result = run_linda_chat_direct(
        uid,
        pid,
        str(payload.get("message") or ""),
        str(payload.get("workflow_key") or ""),
    )
    return {"message": "Linda odpověděla v pracovním chatu.", **result}


def worker_ai_activity(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    project = _project(pid)
    payload = _payload(job)
    brief = str(payload.get("brief") or "").strip()
    mode = str(payload.get("mode") or "campaign")
    workflow_key = str(payload.get("workflow_key") or "").strip()
    if not brief:
        raise RuntimeError("Chybí zadání marketingové aktivity.")
    platforms = _connector_platforms(uid, pid)
    prompt = (
        "Jsi Linda, autonomní český AI content & social media specialist. Každá aktivita musí mít jasnou obchodní konverzi. "
        "Neplánuj sociální obsah samoúčelně. Nejdřív urč cílovou konverzi a konkrétní URL nebo obsahový krok, který k ní vede. "
        "Povolené hlavní cesty jsou: (A) existující produkt/služba → social → cílová stránka; "
        "(B) nový článek → interní odkazy/CTA na produkt/službu → social distribuce; "
        "(C) existující článek → social → článek → konverze; (D) nahrané video → post → CTA. "
        "Pokud vytváříš článek, musí jeho interní odkazy podporovat obchodní cestu. Nevymýšlej URL.\n\n"
        f"PROFIL PROJEKTU:\n{_profile(project)}\n\nBRAND:\n{_brand(project)}\n\n"
        f"KONVERZNÍ CESTA:\n{_workflow_context(workflow_key)}\n\n"
        f"REŽIM: {mode}\nZADÁNÍ: {brief}\n\nZNALOSTI WEBU / PRODUKTY / ČLÁNKY:\n{_knowledge(pid,75)}\n\n"
        f"HISTORIE:\n{_history(pid)}\n\nPŘIPOJENÉ SÍTĚ: {', '.join(platforms)}\n\nDOSTUPNÉ FORMÁTY:\n{_post_formats_context()}\n\n"
        f"UŽIVATELSKÁ MÉDIA: {_user_media_context(pid)}\nPokud chceš použít nahrané video/fotku, vrať u postu media_asset_id."
    )
    res = _engine_or_raise(
        project_id=pid,
        task_type="marketing",
        operation="marketing.campaign.plan",
        quality="balanced",
        prompt=prompt,
        context={"profile": _profile(project), "brand": _brand(project), "platforms": platforms, "workflow": workflow_key},
        input_data={"brief": brief, "mode": mode, "workflow_key": workflow_key},
        output_contract=ACTIVITY_SCHEMA,
    )
    plan = as_json_object(res.result)
    title = str(plan.get("title") or "Marketingová aktivita")
    goal = str(plan.get("goal") or "objednávky")
    article_first = 0 if mode == "quick_post" else 1
    with db() as conn:
        result = conn.execute(
            text(
                "INSERT INTO marketing_activities(user_id,project_id,title,goal,status,brief,payload,workflow_mode,article_first) "
                "VALUES(:uid,:pid,:title,:goal,'planned',:brief,:payload,:mode,:article_first)"
            ),
            {
                "uid": uid, "pid": pid, "title": title[:190], "goal": goal[:80],
                "brief": brief, "payload": json.dumps(plan, ensure_ascii=False),
                "mode": mode[:40], "article_first": article_first,
            },
        )
        activity_id = int(result.lastrowid)

    article_jobs = 0
    if mode != "quick_post":
        articles = plan.get("articles") if isinstance(plan.get("articles"), list) else []
        if not articles:
            articles = [{"title": title, "angle": plan.get("strategy_summary", ""), "keywords": []}]
        for article in articles[:3]:
            article_title = str(article.get("title") or title)
            queue_job(
                uid, pid, "article_generate", f"Článek: {article_title}",
                {
                    "activity_id": activity_id,
                    "title": article_title,
                    "angle": article.get("angle") or "",
                    "keywords": article.get("keywords") or [],
                    "brief": brief,
                },
                worker_group="text", priority=70, parent_job_id=int(job["id"]),
            )
            article_jobs += 1
    else:
        posts = plan.get("posts") if isinstance(plan.get("posts"), list) else []
        _insert_social_posts(uid, pid, activity_id, posts, parent_job_id=int(job["id"]))

    log_ai_event(uid, pid, "activity_planned", "Marketingová aktivita připravena přes Fresh AI Engine", {"activity_id": activity_id, "engine": res.audit()})
    return {
        "message": f"Marketingová aktivita vytvořena. Navazujících článků: {article_jobs}.",
        "activity_id": activity_id,
        "engine": res.audit(),
        "result": plan,
    }


def worker_article_generate(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    project = _project(pid)
    payload = _payload(job)
    topic = str(payload.get("title") or payload.get("brief") or "Článek")
    notes = str(payload.get("angle") or payload.get("brief") or "")
    wp = one("SELECT * FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND provider='wordpress' ORDER BY id DESC LIMIT 1", {"uid":uid,"pid":pid})
    categories = wordpress_categories(uid, pid) if wp else []
    link_candidates = _article_link_candidates(pid, f"{topic} {notes}", project, 8)
    relevant_knowledge = _knowledge_for_chat(pid, f"{topic} {notes}", 16)
    knowledge_count = int((rows("SELECT COUNT(*) c FROM project_knowledge WHERE project_id=:pid AND status='active'", {"pid":pid}) or [{"c":0}])[0].get("c") or 0)
    _mark_progress(int(job["id"]), 20, f"Linda načítá Fresh Core kontext: {knowledge_count} položek a {len(link_candidates)} ověřených odkazů…")
    prompt = (
        "Jsi Linda, česká seniorní content specialistka a SEO copywriterka. Vytvoř plnohodnotný odborný článek "
        "pro skutečný web a následnou obchodní konverzi. Cílová délka je 850–1400 slov podle tématu; minimum je 700 slov, "
        "pokud téma není výjimečně úzké. Článek musí mít silný úvod, logickou strukturu H2/H3, konkrétní doporučení nebo příklady, "
        "shrnutí a jedno jasné CTA. Nevytvářej obecnou AI vatu. Vycházej pouze ze skutečných dat projektu. "
        "Nevymýšlej URL. Přirozeně použij 3–5 ověřených interních odkazů z níže uvedeného seznamu, pokud jsou tematicky relevantní. "
        "U citlivých témat jako GDPR nepředstírej právní radu; vysvětli prakticky principy a odkaž na relevantní služby/obsah projektu.\n\n"
        f"PROFIL:\n{_profile(project)}\n\nBRAND:\n{_brand(project)}\n\n"
        f"TÉMA: {topic}\nPOZNÁMKY: {notes}\n\n"
        f"FRESH CORE / OVĚŘENÉ INTERNÍ ODKAZY:\n{_article_links_prompt(link_candidates)}\n\n"
        f"RELEVANTNÍ ZNALOSTI WEBU:\n{relevant_knowledge}\n\n"
        f"WORDPRESS RUBRIKY:\n{json.dumps(categories[:60], ensure_ascii=False)}\n\n"
        f"HISTORIE OBSAHU:\n{_history(pid, 25)}"
    )
    res = _engine_or_raise(
        project_id=pid, task_type="marketing", operation="marketing.article.create", quality="balanced",
        prompt=prompt,
        context={"profile": _profile(project), "brand": _brand(project), "fresh_core": {"knowledge_count": knowledge_count, "candidate_links": link_candidates, "categories": categories[:60]}},
        input_data={"topic": topic, "notes": notes, "candidate_links": link_candidates, "categories": categories[:60]},
        output_contract=ARTICLE_SCHEMA,
    )
    article = as_json_object(res.result)
    body = str(article.get("body_html") or "")
    first_words = _html_word_count(body)

    # A short structured response is safe to keep, but for a real blog article Linda
    # gets one bounded expansion pass instead of publishing a 200-word teaser.
    if first_words < 650:
        try:
            _mark_progress(int(job["id"]), 48, f"Text má jen {first_words} slov. Linda ho rozpracovává do plnohodnotného článku…")
            expand_prompt = (
                "Rozpracuj tento návrh na skutečný článek o 850–1400 slovech. Zachovej fakta, téma, tón, slug a SEO záměr. "
                "Doplň praktické H2/H3 sekce, konkrétní vysvětlení, příklady a závěrečné CTA. Použij pouze ověřené interní URL. "
                "Vrať celý JSON objekt znovu podle stejného schématu, ne pouze doplněk.\n\n"
                f"TÉMA: {topic}\n\nOVĚŘENÉ ODKAZY:\n{_article_links_prompt(link_candidates)}\n\n"
                f"PŮVODNÍ NÁVRH:\n{json.dumps(article, ensure_ascii=False)}\n\n"
                f"RELEVANTNÍ ZNALOSTI:\n{relevant_knowledge}"
            )
            expanded = _engine_or_raise(
                project_id=pid, task_type="marketing", operation="marketing.article.create", quality="balanced",
                prompt=expand_prompt,
                context={"profile": _profile(project), "brand": _brand(project), "fresh_core": {"candidate_links": link_candidates}},
                input_data={"topic": topic, "notes": notes, "mode": "expand", "candidate_links": link_candidates},
                output_contract=ARTICLE_SCHEMA,
            )
            expanded_article = as_json_object(expanded.result)
            expanded_body = str(expanded_article.get("body_html") or "")
            if _html_word_count(expanded_body) > first_words:
                article = expanded_article
                body = expanded_body
                res = expanded
        except Exception:
            # The original valid draft remains usable; enrichment failure must not
            # destroy manually requested content.
            pass

    title = str(article.get("title") or topic)[:220]
    slug = str(article.get("slug") or "")[:220]
    activity_id = int(payload.get("activity_id") or 0) or None
    requested = article.get("internal_links") if isinstance(article.get("internal_links"), list) else []
    links = select_links(pid, f"{title} {article.get('excerpt','')} {body}", requested or link_candidates, 5, 0)
    if not links:
        links = link_candidates[:5]
    body = _ensure_article_links(body, links, str(article.get("primary_cta_url") or ""))
    word_count = _html_word_count(body)

    planned_url = ""
    category_id = None
    category_name = str(article.get("category_name") or "")
    if wp and slug:
        try:
            preview = from_connector(wp).preview_url(slug, "post")
            data = _extract_fc_data(preview)
            if preview.ok:
                planned_url = str(data.get("url") or "")
        except Exception:
            pass
        wanted = category_name.lower().strip()
        chosen = None
        for c in categories:
            if wanted and wanted in {str(c.get("name") or "").lower(), str(c.get("slug") or "").lower()}:
                chosen = c; break
        chosen = chosen or suggest_wordpress_category(pid, categories, title, body)
        if chosen:
            category_id = int(chosen.get("id") or 0) or None
            category_name = str(chosen.get("name") or category_name)
    if not planned_url and project.get("website_url") and slug:
        planned_url = str(project["website_url"]).rstrip("/") + "/" + slug.strip("/") + "/"

    with db() as conn:
        result = conn.execute(text(
            "INSERT INTO blog_posts(project_id,topic_id,title,slug,excerpt,body,seo_title,seo_description,"
            "social_teaser,image_prompt,status,activity_id,internal_links_json,planned_url,wp_category_id,wp_category_name) "
            "VALUES(:pid,NULL,:title,:slug,:excerpt,:body,:seo_title,:seo_description,:social_teaser,"
            ":image_prompt,'draft',:activity_id,:internal_links,:planned_url,:cat_id,:cat_name)"
        ), {
            "pid":pid,"title":title,"slug":slug,"excerpt":str(article.get("excerpt") or ""),"body":body,
            "seo_title":str(article.get("seo_title") or "")[:220],"seo_description":str(article.get("seo_description") or "")[:255],
            "social_teaser":str(article.get("social_teaser") or ""),"image_prompt":str(article.get("image_prompt") or ""),
            "activity_id":activity_id,"internal_links":json.dumps(links,ensure_ascii=False),"planned_url":planned_url or None,
            "cat_id":category_id,"cat_name":category_name or None,
        })
        blog_id = int(result.lastrowid)

    manual_calendar = bool(payload.get("manual_calendar"))
    calendar_post_id = None
    if manual_calendar:
        try:
            when = datetime.fromisoformat(str(payload.get("scheduled_at") or ""))
        except Exception:
            when = datetime.now()
        with db() as conn:
            ins2 = conn.execute(text(
                "INSERT INTO posts(project_id,activity_id,platform,post_type,title,body,image_prompt,hashtags,recommended_time,status,scheduled_at,destination_url,destination_type,destination_ref_id,destination_status) "
                "VALUES(:pid,:activity,'blog','blog_promo',:title,:body,:img,'',:recommended,'draft',:scheduled,:url,'blog',:ref,'planned')"
            ), {"pid":pid,"activity":activity_id,"title":title[:190],"body":str(article.get("social_teaser") or article.get("excerpt") or ""),"img":str(article.get("image_prompt") or ""),"recommended":when.strftime("%H:%M"),"scheduled":when,"url":planned_url or None,"ref":blog_id})
            calendar_post_id=int(ins2.lastrowid)
    image_job = queue_job(uid,pid,"image_generate_blog",f"Náhledový obrázek článku: {title}",{"blog_id":blog_id},worker_group="media",priority=60,parent_job_id=int(job["id"]),notify_user=manual_calendar)
    audit_context={"blog_id":blog_id,"calendar_post_id":calendar_post_id,"engine":res.audit(),"word_count":word_count,"internal_links":len(links),"fresh_core":{"connected":bool(wp),"knowledge_count":knowledge_count,"category":category_name or None}}
    log_ai_event(uid,pid,"article_generated","Článek vytvořen přes Fresh AI Engine a Fresh Core kontext",audit_context)
    if manual_calendar:
        return {"message":f"Článek „{title}“ je připravený: {word_count} slov, {len(links)} interních odkazů. Náhledový obrázek se generuje.","blog_id":blog_id,"post_id":calendar_post_id,"planned_url":planned_url,"word_count":word_count,"internal_links":links,"engine":res.audit(),"result":article}
    publish_mode = str(project.get("web_publish_mode") or "publish")
    wp_job = None
    if wp:
        wp_job = queue_job(uid,pid,"wp_publish_blog",f"WordPress: {title}",{"blog_id":blog_id,"force_publish":publish_mode!="review"},worker_group="publication",priority=80,parent_job_id=int(job["id"]),depends_on_job_id=image_job,notify_user=True,dedupe_key=f"wp-publish:blog:{blog_id}")
    queue_job(uid,pid,"social_derivatives",f"Plánuji distribuci článku: {title}",{"blog_id":blog_id},worker_group="text",priority=70,parent_job_id=int(job["id"]),depends_on_job_id=wp_job if (wp_job and publish_mode!="review") else None,dedupe_key=f"social-plan:blog:{blog_id}")
    return {"message":f"Článek „{title}“ je připraven: {word_count} slov a {len(links)} interních odkazů. Navazující obrázek, WordPress a social distribuce jsou ve frontě.","blog_id":blog_id,"planned_url":planned_url,"word_count":word_count,"internal_links":links,"engine":res.audit(),"result":article}

def _insert_social_posts(uid: int, pid: int, activity_id: int | None, posts: list[dict[str, Any]], destination_url: str | None = None, destination_type: str | None = None, destination_ref_id: int | None = None, parent_job_id: int | None = None) -> int:
    count = 0
    allowed = {"facebook", "instagram", "linkedin", "x", "tiktok"}
    for item in posts[:20]:
        platform = str(item.get("platform") or "").lower()
        if platform not in allowed:
            continue
        hashtags = item.get("hashtags") or []
        if isinstance(hashtags, list):
            hashtags = " ".join(str(x) if str(x).startswith("#") else f"#{x}" for x in hashtags)
        slides = item.get("carousel_slides") or []
        when = None
        date, tm = str(item.get("date") or ""), str(item.get("time") or "")
        if date:
            try:
                when = datetime.fromisoformat(f"{date}T{tm or recommended_time(pid, platform)}")
            except Exception:
                when = None
        dest = tracking_url(destination_url, platform, activity_id) if destination_url else None
        with db() as conn:
            result = conn.execute(text(
                "INSERT INTO posts(project_id,plan_id,topic_id,activity_id,platform,post_type,title,body,"
                "image_prompt,carousel_slides,hashtags,recommended_time,status,scheduled_at,destination_url,destination_type,destination_ref_id,destination_status) "
                "VALUES(:pid,NULL,NULL,:activity_id,:platform,:post_type,:title,:body,:image_prompt,"
                ":slides,:hashtags,:recommended,'draft',:scheduled_at,:destination_url,:destination_type,:destination_ref_id,:destination_status)"
            ), {
                "pid":pid,"activity_id":activity_id,"platform":platform,"post_type":str(item.get("post_type") or "single_post")[:40],
                "title":str(item.get("title") or "")[:190],"body":str(item.get("body") or ""),"image_prompt":str(item.get("image_prompt") or ""),
                "slides":json.dumps(slides,ensure_ascii=False),"hashtags":str(hashtags),"recommended":recommended_time(pid,platform),
                "scheduled_at":when,"destination_url":dest,"destination_type":destination_type,"destination_ref_id":destination_ref_id,
                "destination_status":"planned" if destination_type=="blog" else None,
            })
            post_id=int(result.lastrowid)
        media_asset_id = int(item.get("media_asset_id") or 0) if str(item.get("media_asset_id") or "").isdigit() else 0
        if media_asset_id:
            with db() as conn:
                conn.execute(text("UPDATE media_assets SET post_id=:post_id WHERE id=:id AND project_id=:pid AND provider='upload'"), {"post_id":post_id,"id":media_asset_id,"pid":pid})
        if not media_asset_id and str(item.get("image_prompt") or "").strip():
            queue_job(uid,pid,"image_generate_post",f"Obrázek: {item.get('title') or platform}",{"post_id":post_id},worker_group="media",priority=55,parent_job_id=parent_job_id,notify_user=False,dedupe_key=f"image:post:{post_id}")
        if isinstance(slides,list) and slides and str(item.get("post_type") or "") == "carousel":
            for idx,slide in enumerate(slides[:8],1):
                queue_job(uid,pid,"carousel_slide",f"Carousel {idx}: {item.get('title') or platform}",{"post_id":post_id,"slide_index":idx,"slide":slide},worker_group="media",priority=50,parent_job_id=parent_job_id,notify_user=False,dedupe_key=f"carousel:{post_id}:{idx}")
        count += 1
    return count

def worker_social_derivatives(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    payload = _payload(job)
    blog_id = int(payload.get("blog_id") or 0)
    article = one("SELECT * FROM blog_posts WHERE id=:id AND project_id=:pid", {"id": blog_id, "pid": pid})
    if not article:
        raise RuntimeError("Článek pro sociální distribuci nebyl nalezen.")
    project = _project(pid)
    platforms = _connector_platforms(uid, pid)
    if not platforms:
        return {"message": "Článek je připraven, ale projekt nemá připojenou sociální síť.", "blog_id": blog_id}

    destination = str(article.get("destination_url") or article.get("planned_url") or "")
    prompt = (
        "Jsi Linda, AI Social Media Specialist. Vytvoř odlišný distribuční balíček pro připojené "
        "sociální sítě. Hlavní cíl je přivést relevantní návštěvnost na článek a z článku ke konverzi. "
        "Neopakuj stejný text napříč sítěmi. X do 280 znaků.\n\n"
        f"PROJEKT:\n{_profile(project)}\n\nBRAND:\n{_brand(project)}\n\n"
        f"ČLÁNEK: {article.get('title')}\nURL: {destination}\nEXCERPT: {article.get('excerpt')}\n"
        f"TEASER: {article.get('social_teaser')}\n\nPLATFORMY: {', '.join(platforms)}\n\n"
        f"HISTORIE:\n{_history(pid)}"
    )
    res = _engine_or_raise(
        project_id=pid,
        task_type="marketing",
        operation="marketing.social.create",
        quality="economy",
        prompt=prompt,
        context={"platforms": platforms, "brand": _brand(project)},
        input_data={"blog_id": blog_id, "destination_url": destination},
        output_contract=SOCIAL_SCHEMA,
    )
    posts = as_json_list(res.result)
    count = _insert_social_posts(uid, pid, int(article.get("activity_id") or 0) or None, posts, destination or None, "blog", blog_id, int(job["id"]))
    log_ai_event(uid, pid, "social_derivatives", "Sociální distribuce vytvořena přes Fresh AI Engine", {"blog_id": blog_id, "count": count, "engine": res.audit()})
    return {"message": f"Připraveno {count} sociálních výstupů.", "count": count, "engine": res.audit()}


def _mysql_datetime(value: Any) -> Any:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return raw[:19].replace("T", " ")


def _sync_wp_item(pid: int, item: dict[str, Any], signature: list[Any]) -> int:
    url = str(item.get("url") or "")
    if not url:
        return 0
    source_type = str(item.get("type") or item.get("post_type") or "page")
    if source_type not in {"post", "page", "product"}:
        source_type = "page"

    taxonomies = item.get("taxonomies") if isinstance(item.get("taxonomies"), dict) else {}
    categories = item.get("categories") if isinstance(item.get("categories"), list) else taxonomies.get("category", [])
    tags = item.get("tags") if isinstance(item.get("tags"), list) else taxonomies.get("post_tag", [])
    meta = {
        "categories": categories or [],
        "tags": tags or [],
        "taxonomies": taxonomies,
        "seo": item.get("seo") if isinstance(item.get("seo"), dict) else {},
        "commerce": item.get("commerce") if isinstance(item.get("commerce"), dict) else {},
        "status": item.get("status", "publish"),
    }
    title = str(item.get("title") or "")
    excerpt = str(item.get("excerpt") or "")
    content = str(item.get("content") or "")
    signature.append([
        item.get("id"), url, item.get("modified"),
        hashlib.sha256((title + "|" + excerpt + "|" + content).encode()).hexdigest(),
    ])
    enrichment_present = "featured_image" in item or "local_views" in item or "seo" in item
    with db() as conn:
        conn.execute(
            text(
                "INSERT INTO project_knowledge(project_id,source_type,url,title,summary,remote_id,content_body,meta_json,"
                "featured_image_url,modified_at,traffic,status,last_seen_at) "
                "VALUES(:pid,:stype,:url,:title,:summary,:remote_id,:content,:meta,:featured,:modified,:traffic,'active',NOW()) "
                "ON DUPLICATE KEY UPDATE source_type=VALUES(source_type),title=VALUES(title),summary=VALUES(summary),"
                "remote_id=VALUES(remote_id),content_body=VALUES(content_body),"
                "meta_json=IF(:enriched=1,VALUES(meta_json),meta_json),"
                "featured_image_url=IF(:enriched=1,VALUES(featured_image_url),featured_image_url),"
                "modified_at=VALUES(modified_at),traffic=IF(:enriched=1,GREATEST(traffic,VALUES(traffic)),traffic),"
                "status='active',last_seen_at=NOW()"
            ),
            {
                "pid": pid, "stype": source_type, "url": url,
                "title": title[:255], "summary": excerpt,
                "remote_id": str(item.get("id") or ""), "content": content,
                "meta": json.dumps(meta, ensure_ascii=False),
                "featured": str(item.get("featured_image") or "") if "featured_image" in item else None,
                "modified": _mysql_datetime(item.get("modified")),
                "traffic": max(0, int(item.get("local_views") or 0)),
                "enriched": 1 if enrichment_present else 0,
            },
        )
    return 1


def worker_wordpress_sync(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    connector = one(
        "SELECT * FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND provider='wordpress' ORDER BY id DESC LIMIT 1",
        {"uid": uid, "pid": pid},
    )
    if not connector:
        raise RuntimeError("Pro projekt není uložen WordPress / Fresh Core konektor.")
    client = from_connector(connector)
    ping = client.ping()
    if not ping.ok:
        raise RuntimeError(f"Fresh Core ping selhal: HTTP {ping.status_code} {ping.error}")

    signature: list[Any] = []
    total = 0
    pages = 0
    categories: list[Any] = []
    sync_started = datetime.now()

    # Fresh Core 0.5+ preferred path: one mixed endpoint contains exactly the
    # featured-image/SEO/view metadata Linda 3.8 used. 0.4.6 returns 404 here,
    # in which case the current generic /content API is used without breaking rollout.
    export = client.content_export(page=1, per_page=100)
    if export.ok:
        page = 1
        response = export
        while page <= 50:
            data = response.data if isinstance(response.data, dict) else {}
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            items = payload.get("items") if isinstance(payload, dict) else []
            if not isinstance(items, list):
                items = []
            if not categories and isinstance(payload.get("categories"), list):
                categories = payload.get("categories") or []
            for item in items:
                if isinstance(item, dict):
                    total += _sync_wp_item(pid, item, signature)
            pages += 1
            if not bool(payload.get("has_more")) or not items:
                break
            page += 1
            response = client.content_export(page=page, per_page=100)
            if not response.ok:
                raise RuntimeError(f"Fresh Core content/export: HTTP {response.status_code} {response.error}")
    elif export.status_code in {400, 404}:
        # Fresh Core 0.4.6 compatibility during staged rollout.
        cat_res = client.terms("category")
        if cat_res.ok and isinstance(cat_res.data, dict):
            categories = cat_res.data.get("items") or cat_res.data.get("data") or []
        elif cat_res.ok and isinstance(cat_res.data, list):
            categories = cat_res.data

        for post_type in ("page", "post", "product"):
            page = 1
            while page <= 50:
                res = client.content(post_type, page=page, per_page=100, include_content=True, status="publish")
                if not res.ok:
                    if post_type == "product" and res.status_code in {400, 404}:
                        break
                    raise RuntimeError(f"Fresh Core content/{post_type}: HTTP {res.status_code} {res.error}")
                data = res.data if isinstance(res.data, dict) else {}
                payload = data.get("data") if isinstance(data.get("data"), dict) else data
                items = payload.get("items") if isinstance(payload, dict) else []
                if not isinstance(items, list):
                    items = []
                for item in items:
                    if isinstance(item, dict):
                        normalized = dict(item)
                        normalized.setdefault("type", post_type)
                        total += _sync_wp_item(pid, normalized, signature)
                pages += 1
                total_pages = int(payload.get("pages") or payload.get("total_pages") or 0) if isinstance(payload, dict) else 0
                has_more = bool(payload.get("has_more")) if isinstance(payload, dict) and "has_more" in payload else bool(items and (not total_pages or page < total_pages))
                if not items or not has_more:
                    break
                page += 1
    else:
        raise RuntimeError(f"Fresh Core content/export: HTTP {export.status_code} {export.error}")

    # Preserve Linda 3.8 behavior: content removed/unpublished in WP becomes inactive,
    # historical rows are not deleted.
    with db() as conn:
        conn.execute(
            text(
                "UPDATE project_knowledge SET status='inactive' WHERE project_id=:pid "
                "AND source_type IN ('post','page','product') AND (last_seen_at IS NULL OR last_seen_at<:started)"
            ),
            {"pid": pid, "started": sync_started},
        )

    signature.sort(key=lambda x: str(x))
    fingerprint = hashlib.sha256(json.dumps(signature, ensure_ascii=False, default=str).encode()).hexdigest()
    old_fingerprint = str(connector.get("sync_fingerprint") or "")
    changed = not old_fingerprint or old_fingerprint != fingerprint
    with db() as conn:
        conn.execute(text("UPDATE projects SET knowledge_synced_at=NOW() WHERE id=:pid"), {"pid": pid})
        conn.execute(
            text(
                "UPDATE social_connectors SET status='connected',last_sync_at=NOW(),sync_fingerprint=:fp,"
                "categories_json=:cats,last_content_change_at=IF(:changed=1,NOW(),last_content_change_at) WHERE id=:id"
            ),
            {
                "fp": fingerprint, "cats": json.dumps(categories, ensure_ascii=False),
                "changed": 1 if changed else 0, "id": int(connector["id"]),
            },
        )
    if changed:
        queue_job(
            uid, pid, "project_profile", "Linda aktualizuje profil firmy po změně webu",
            {"source": "fresh_core_change"}, worker_group="text", priority=80,
            parent_job_id=int(job["id"]), dedupe_key=f"project_profile:{pid}",
        )
    return {
        "message": f"Fresh Core synchronizace: načteno {total} položek v {pages} dávkách. "
                   + ("Web se změnil; profil se přepočítá." if changed else "Web se nezměnil; AI kredit se nespotřebuje."),
        "items": total, "pages": pages, "changed": changed, "fingerprint": fingerprint,
        "fresh_core_mode": "0.5_export" if export.ok else "0.4.6_fallback",
    }



def worker_media_maintenance(job: dict[str, Any]) -> dict[str, Any]:
    """Bounded self-maintenance for media storage.

    It optimizes only a small number of assets per run, rewrites DB paths only
    after a successful smaller replacement and removes the old file only when
    no other DB row references it. This keeps Passenger/CloudLinux load bounded.
    """
    pid = int(job.get("project_id") or 0)
    payload = _payload(job)
    try:
        requested_limit = int(payload.get("limit") or settings.media_maintenance_batch)
    except Exception:
        requested_limit = int(settings.media_maintenance_batch)
    limit = max(1, min(80, requested_limit))
    # Persist a baseline once so the admin can see cumulative storage savings.
    try:
        baseline_now = sum(p.stat().st_size for p in UPLOAD_ROOT.rglob("*") if p.is_file() and ".thumbs" not in p.parts) if UPLOAD_ROOT.exists() else 0
        with db() as conn:
            conn.execute(text("INSERT IGNORE INTO app_meta(meta_key,meta_value) VALUES('media_maintenance_baseline_bytes',:v)"), {"v":str(baseline_now)})
    except Exception:
        baseline_now = 0
    def maintenance_items(table: str, cursor_key: str, row_limit: int) -> tuple[list[dict[str, Any]], int]:
        cursor_row = one("SELECT meta_value FROM app_meta WHERE meta_key=:k LIMIT 1", {"k": cursor_key})
        try:
            cursor = int(str((cursor_row or {}).get("meta_value") or "0").strip('"'))
        except Exception:
            cursor = 0
        params: dict[str, Any] = {"limit": row_limit, "cursor": cursor}
        sql = f"SELECT id,project_id,file_path FROM {table} WHERE file_path IS NOT NULL AND file_path<>'' AND id>:cursor"
        if pid:
            sql += " AND project_id=:pid"; params["pid"] = pid
        sql += " ORDER BY id ASC LIMIT :limit"
        found = rows(sql, params)
        if not found and cursor:
            cursor = 0; params["cursor"] = 0
            found = rows(sql, params)
        for row in found:
            row["_source_table"] = table
            row["_cursor_key"] = cursor_key
        return found, cursor

    media_limit=max(1, (limit*2)//3)
    ref_limit=max(1, limit-media_limit)
    media_items,_=maintenance_items("media_assets",f"media_maintenance_cursor:{pid or 'all'}",media_limit)
    reference_items,_=maintenance_items("visual_references",f"reference_maintenance_cursor:{pid or 'all'}",ref_limit)
    items=media_items+reference_items
    checked = optimized_count = saved_bytes = 0
    notes: list[str] = []
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    root = Path(__file__).resolve().parents[1]
    for item in items:
        mid = int(item["id"]); stored = str(item.get("file_path") or "")
        source = resolve_upload_path(stored)
        if not source or not source.is_file():
            continue
        checked += 1
        old_size = source.stat().st_size
        target: Path | None = None
        info: dict[str, Any] = {}
        try:
            if source.name.endswith("-optimized.mp4") or "optimized" in source.parts:
                continue
            if source.suffix.lower() in image_exts:
                raw = source.read_bytes()
                data, ext, info = optimize_image_bytes(raw, source.name)
                if info.get("optimized") and len(data) < old_size:
                    validate_image_bytes(data, "image/jpeg" if ext in {"jpg","jpeg"} else f"image/{ext}")
                    outdir = UPLOAD_ROOT / "optimized"; outdir.mkdir(parents=True, exist_ok=True)
                    prefix = "reference" if str(item.get("_source_table") or "") == "visual_references" else "media"
                    target = outdir / f"{prefix}-{mid}-{hashlib.sha1(data).hexdigest()[:10]}.{ext}"
                    tmp_target = target.with_suffix(target.suffix + ".tmp")
                    tmp_target.write_bytes(data)
                    validate_image_bytes(tmp_target.read_bytes(), "image/jpeg" if ext in {"jpg","jpeg"} else f"image/{ext}")
                    tmp_target.replace(target)
            elif source.suffix.lower() in VIDEO_EXTS:
                candidate, info = optimize_video_path(source)
                if info.get("optimized") and candidate != source:
                    target = candidate
            if not target or not target.is_file() or target.stat().st_size >= old_size:
                continue
            new_size = target.stat().st_size
            rel = str(target.relative_to(root))
            source_table=str(item.get("_source_table") or "media_assets")
            with db() as conn:
                if source_table == "visual_references":
                    conn.execute(text("UPDATE visual_references SET file_path=:path WHERE id=:id"), {"path": rel, "id": mid})
                else:
                    conn.execute(text("UPDATE media_assets SET file_path=:path WHERE id=:id"), {"path": rel, "id": mid})
            refs = rows(
                "SELECT (SELECT COUNT(*) FROM media_assets WHERE file_path=:p)+(SELECT COUNT(*) FROM visual_references WHERE file_path=:p) c",
                {"p": stored},
            )
            if source != target and refs and int(refs[0].get("c") or 0) == 0:
                try: source.unlink()
                except Exception: pass
            optimized_count += 1
            saved_bytes += max(0, old_size - new_size)
        except Exception as exc:
            try:
                if target:
                    dangling = target.with_suffix(target.suffix + ".tmp")
                    if dangling.exists(): dangling.unlink()
            except Exception:
                pass
            notes.append(f"#{mid}:{type(exc).__name__}")
    if items:
        cursors: dict[str,int] = {}
        for x in items:
            key=str(x.get("_cursor_key") or "")
            if key:
                cursors[key]=max(cursors.get(key,0),int(x.get("id") or 0))
        with db() as conn:
            for key,value in cursors.items():
                conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES(:k,:v) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"k": key, "v": str(value)})

    # Clear only stale thumbnails; current thumbnails are lazily recreated.
    try:
        cache = UPLOAD_ROOT / ".thumbs"
        if cache.exists():
            now = datetime.now().timestamp()
            for thumb in cache.glob("*.webp"):
                try:
                    if now - thumb.stat().st_mtime > 30 * 86400:
                        thumb.unlink()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        current_bytes = sum(p.stat().st_size for p in UPLOAD_ROOT.rglob("*") if p.is_file() and ".thumbs" not in p.parts) if UPLOAD_ROOT.exists() else 0
        remaining_large = sum(1 for p in UPLOAD_ROOT.rglob("*") if p.is_file() and ".thumbs" not in p.parts and p.suffix.lower() in image_exts and p.stat().st_size > 600_000) if UPLOAD_ROOT.exists() else 0
        with db() as conn:
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('media_maintenance_last_run',:v) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"v":datetime.now().isoformat(timespec='seconds')})
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('media_maintenance_current_bytes',:v) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"v":str(current_bytes)})
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('media_maintenance_last_saved_bytes',:v) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"v":str(saved_bytes)})
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('media_maintenance_remaining_candidates',:v) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"v":str(remaining_large)})
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('media_maintenance_saved_total',:v) ON DUPLICATE KEY UPDATE meta_value=CAST(COALESCE(meta_value,'0') AS UNSIGNED)+VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"v":str(saved_bytes)})
            conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('media_maintenance_optimized_total',:v) ON DUPLICATE KEY UPDATE meta_value=CAST(COALESCE(meta_value,'0') AS UNSIGNED)+VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"v":str(optimized_count)})
    except Exception:
        current_bytes = 0; remaining_large = 0
    return {
        "message": f"Údržba médií: zkontrolováno {checked}, optimalizováno {optimized_count}, ušetřeno {saved_bytes} B.",
        "checked": checked,
        "optimized": optimized_count,
        "saved_bytes": saved_bytes,
        "current_bytes": current_bytes,
        "remaining_candidates": remaining_large,
        "limit": limit,
        "notes": notes[:10],
    }

def worker_proactive_review(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job.get("project_id") or 0)
    project = _project(pid) if pid else {}
    issues: list[str] = []
    if pid:
        connectors = rows("SELECT provider,status,last_test_status,last_preflight_status FROM social_connectors WHERE project_id=:pid AND user_id=:uid", {"pid":pid,"uid":uid})
        if not connectors:
            issues.append("Nejsou připojené žádné publikační kanály.")
        else:
            bad=[str(c.get("provider") or "konektor") for c in connectors if str(c.get("status") or "") not in {"active","connected"} or str(c.get("last_test_status") or "") in {"error","failed"}]
            if bad: issues.append("Zkontroluj konektory: "+", ".join(sorted(set(bad))))
        overdue = one("SELECT COUNT(*) c FROM posts WHERE project_id=:pid AND published_at IS NULL AND scheduled_at<NOW() AND status IN ('scheduled','ready','draft')", {"pid":pid})
        if overdue and int(overdue.get("c") or 0): issues.append(f"{int(overdue.get('c') or 0)} příspěvků je po termínu.")
        approvals = one("SELECT COUNT(*) c FROM posts WHERE project_id=:pid AND status IN ('draft','ready')", {"pid":pid})
        if approvals and int(approvals.get("c") or 0): issues.append(f"{int(approvals.get('c') or 0)} příspěvků čeká na kontrolu nebo schválení.")
        knowledge = one("SELECT COUNT(*) c FROM project_knowledge WHERE project_id=:pid AND status='active'", {"pid":pid})
        if not knowledge or int(knowledge.get("c") or 0)==0: issues.append("Znalostní báze projektu je prázdná.")
    if issues:
        title=f"Linda zkontrolovala projekt: {project.get('name') or pid}"
        message=" ".join(issues[:5])
        notify(uid,pid or None,"proactive_review","info",title,message,f"?page=linda&project_id={pid}",f"proactive:{pid}:{datetime.now().strftime('%Y%m%d')}",12)
        return {"message":message,"issues":issues}
    return {"message":"Projekt je bez nových provozních výjimek.","issues":[]}


def worker_manual_post_generate(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    payload = _payload(job); project = _project(pid)
    post_id = int(payload.get("post_id") or 0)
    post = one("SELECT * FROM posts WHERE id=:id AND project_id=:pid", {"id":post_id,"pid":pid})
    if not post: raise RuntimeError("Rozpracovaný příspěvek nebyl nalezen.")
    topic = str(payload.get("topic") or post.get("title") or "Příspěvek")
    platform = str(post.get("platform") or "facebook")
    post_type = str(post.get("post_type") or "single_post")
    prompt=(
        "Jsi Linda, česká seniorní social media specialistka. Připrav jeden konkrétní příspěvek podle Brand Kitu a tónu projektu. "
        "Obsah musí mít jasný obchodní smysl, nesmí si vymýšlet fakta ani URL. Vrať JSON.\n\n"
        f"PROJEKT:\n{_profile(project)}\n\nBRAND:\n{_brand(project)}\n\n"
        f"KANÁL: {platform}\nFORMÁT: {post_type}\nTÉMA: {topic}\n\n"
        f"RELEVANTNÍ ZNALOSTI:\n{_knowledge_for_chat(pid, topic, 12)}"
    )
    res=_engine_or_raise(project_id=pid,task_type="marketing",operation="marketing.social.create",quality="balanced",prompt=prompt,
                         context={"profile":_profile(project),"brand":_brand(project),"platform":platform,"post_type":post_type},
                         input_data={"topic":topic,"platform":platform,"post_type":post_type},output_contract=MANUAL_POST_SCHEMA)
    data=as_json_object(res.result)
    body=str(data.get("body") or ""); hashtags=str(data.get("hashtags") or "")
    image_prompt=str(data.get("image_prompt") or "")
    destination=None
    q=str(data.get("destination_query") or topic)
    try:
        links=select_links(pid,q,[],1,0); destination=str(links[0].get("url") or "") if links else None
    except Exception: destination=None
    with db() as conn:
        conn.execute(text("UPDATE posts SET title=:title,body=:body,hashtags=:hashtags,image_prompt=:img,destination_url=:url,destination_status=:ds,status='draft' WHERE id=:id"),
                     {"title":str(data.get("title") or topic)[:190],"body":body,"hashtags":hashtags,"img":image_prompt,"url":destination,"ds":"ok" if destination else None,"id":post_id})
    image_job=None
    if image_prompt:
        image_job=queue_job(uid,pid,"image_generate_post",f"Obrázek: {data.get('title') or topic}",{"post_id":post_id},worker_group="media",priority=60,parent_job_id=int(job["id"]),notify_user=True,dedupe_key=f"image:post:{post_id}")
    return {"message":"Příspěvek je připravený v kalendáři." + (" Obrázek se generuje." if image_job else ""),"post_id":post_id,"engine":res.audit(),"image_job_id":image_job}


def process_job(job: dict[str, Any]) -> dict[str, Any]:
    job_type = str(job["type"])
    if job_type == "project_profile":
        return worker_project_profile(job)
    if job_type == "linda_chat":
        return worker_linda_chat(job)
    if job_type == "ai_activity":
        return worker_ai_activity(job)
    if job_type == "article_generate":
        return worker_article_generate(job)
    if job_type == "social_derivatives":
        return worker_social_derivatives(job)
    if job_type == "wordpress_sync":
        return worker_wordpress_sync(job)
    if job_type == "rewrite_post_text":
        return worker_rewrite_post_text(job)
    if job_type == "wp_publish_blog":
        return worker_wp_publish_blog(job)
    if job_type == "wp_publish_post":
        return worker_wp_publish_post(job)
    if job_type == "social_publish_post":
        return worker_social_publish(job)
    if job_type == "analytics_sync":
        return worker_analytics_sync(job)
    if job_type in {"image_generate_post", "image_generate_blog", "carousel_slide", "image_generate_carousel_slide", "free_image_generate"}:
        return worker_image_generate(job)
    if job_type == "batch_group":
        return {"message":"Dávka připravena. Navazující úlohy se zpracují samostatně.","batch":True}
    if job_type == "sitemap_async":
        return worker_sitemap_async(job)
    if job_type == "brand_analyze":
        return worker_brand_analyze(job)
    if job_type == "media_maintenance":
        return worker_media_maintenance(job)
    if job_type == "proactive_review":
        return worker_proactive_review(job)
    if job_type == "manual_post_generate":
        return worker_manual_post_generate(job)
    raise RuntimeError(f"Typ úlohy {job_type} ještě není v Python parity vrstvě podporován.")


def claim_jobs(limit: int = 5, worker_group: str | None = None) -> list[dict[str, Any]]:
    supported = sorted(SUPPORTED_JOB_TYPES)
    placeholders = ",".join(f":t{i}" for i in range(len(supported)))
    params: dict[str, Any] = {f"t{i}": t for i, t in enumerate(supported)}
    params["limit"] = max(1, min(25, limit))
    sql = (
        f"SELECT * FROM task_queue WHERE status='queued' AND type IN ({placeholders}) "
        "AND (available_at IS NULL OR available_at<=NOW()) "
        "AND (depends_on_job_id IS NULL OR EXISTS(SELECT 1 FROM task_queue dep WHERE dep.id=task_queue.depends_on_job_id AND dep.status='done'))"
    )
    if worker_group and worker_group != "all":
        sql += " AND worker_group=:worker_group"
        params["worker_group"] = worker_group
    sql += " ORDER BY priority DESC,id ASC LIMIT :limit"
    candidates = rows(sql, params)
    claimed: list[dict[str, Any]] = []
    for item in candidates:
        with db() as conn:
            result = conn.execute(
                text(
                    "UPDATE task_queue SET status='running',locked_at=NOW(),updated_at=NOW(),"
                    "message='Linda pracuje…' WHERE id=:id AND status='queued'"
                ),
                {"id": int(item["id"])},
            )
            if result.rowcount == 1:
                item["status"] = "running"
                claimed.append(item)
    return claimed


def cleanup_legacy_chat_jobs(user_id: int | None = None) -> int:
    """Finish queued legacy chat jobs. Interactive chat is synchronous since 0.4.3.

    Old queued linda_chat rows can otherwise block the queue forever and still show
    obsolete Engine validation errors even though current chat calls Engine directly.
    """
    where = "type='linda_chat' AND status IN ('queued','running')"
    params: dict[str, Any] = {}
    if user_id is not None:
        where += " AND user_id=:uid"
        params["uid"] = int(user_id)
    with db() as conn:
        result = conn.execute(text(
            "UPDATE task_queue SET status='done',progress=100,locked_at=NULL,finished_at=NOW(),updated_at=NOW(),"
            "message='Historická chatová úloha uzavřena. Chat nyní komunikuje s AI Engine přímo.' "
            f"WHERE {where}"
        ), params)
        return int(result.rowcount or 0)


def recover_stale_jobs(minutes: int = 30, user_id: int | None = None) -> int:
    """Return abandoned running jobs to the queue after a Passenger/cron interruption.

    Legacy rows sometimes have NULL locked_at. In that case updated_at is the best
    heartbeat available, so use COALESCE instead of leaving them permanently running.
    """
    minutes = max(10, min(180, int(minutes)))
    where_user = ""
    params: dict[str, Any] = {}
    if user_id is not None:
        where_user = " AND user_id=:uid"
        params["uid"] = int(user_id)
    with db() as conn:
        result = conn.execute(text(
            f"UPDATE task_queue SET "
            "attempts=attempts+1,"
            "status=IF(attempts+1>=max_attempts,'failed','queued'),"
            "locked_at=NULL,available_at=NOW(),updated_at=NOW(),"
            "message=IF(attempts+1>=max_attempts,'Úloha po opakovaném timeoutu selhala.','Úloha obnovena po přerušení workeru.') "
            f"WHERE status='running' AND COALESCE(locked_at,updated_at)<DATE_SUB(NOW(),INTERVAL {minutes} MINUTE){where_user}"
        ), params)
        return int(result.rowcount or 0)



def remind_unresolved_failures(user_id: int | None = None, limit: int = 8) -> int:
    """Re-surface recent unresolved failures once per day until they are retried/fixed."""
    where = "q.status='failed' AND q.notify_user=1 AND q.finished_at IS NOT NULL AND q.finished_at<DATE_SUB(NOW(),INTERVAL 6 HOUR) AND q.finished_at>DATE_SUB(NOW(),INTERVAL 7 DAY)"
    params: dict[str, Any] = {"limit": max(1, min(20, int(limit)))}
    if user_id is not None:
        where += " AND q.user_id=:uid"
        params["uid"] = int(user_id)
    failed = rows(f"SELECT q.* FROM task_queue q WHERE {where} ORDER BY q.finished_at DESC LIMIT :limit", params)
    sent=0
    day=datetime.now().strftime('%Y%m%d')
    for job in failed:
        try:
            friendly=_friendly_failure(job, str(job.get('message') or ''))
            notify(
                int(job['user_id']), int(job.get('project_id') or 0) or None, 'job_failed_reminder', 'warning',
                f"Stále čeká na opravu: {job.get('title') or job.get('type')}",
                str(friendly.get('message') or 'Tato úloha zůstává nedokončená.'),
                str(friendly.get('action_url') or '?page=queue'), f"job-failed-reminder:{int(job['id'])}:{day}", 0,
            )
            sent+=1
        except Exception:
            continue
    return sent

def process_available_jobs(limit: int = 5, worker_group: str | None = None) -> dict[str, Any]:
    recovered = recover_stale_jobs()
    jobs = claim_jobs(limit=limit, worker_group=worker_group)
    done, failed = 0, 0
    details: list[dict[str, Any]] = []
    for job in jobs:
        try:
            _mark_progress(int(job["id"]), 10, "Linda zpracovává úlohu přes Python worker…")
            result = process_job(job)
            _finish(int(job["id"]), result)
            if int(job.get("notify_user") or 0):
                try:
                    ntype = "plan_ready" if str(job.get("type"))=="ai_activity" else ("media_batch" if str(job.get("worker_group"))=="media" else "job_done")
                    notify(int(job["user_id"]), int(job.get("project_id") or 0) or None, ntype, "info", str(job.get("title") or "Linda dokončila úlohu"), str(result.get("message") or "Hotovo."), _job_action_url(job), f"job-done:{int(job['id'])}", 0)
                except Exception:
                    pass
            done += 1
            details.append({"id": job["id"], "type": job["type"], "ok": True})
        except Exception as exc:
            failure = _fail(job, exc)
            if failure.get("retry"):
                try:
                    notify(
                        int(job["user_id"]), int(job.get("project_id") or 0) or None, "job_retry", "warning",
                        f"Linda úlohu zopakuje: {job.get('title') or job.get('type')}",
                        str(failure.get("friendly_message") or "Úloha se nepovedla napoprvé. Linda ji automaticky zkusí znovu."),
                        "?page=queue", f"job-retry:{int(job['id'])}:{int(failure.get('attempt') or 0)}", 0
                    )
                except Exception:
                    pass
            else:
                try:
                    notify(
                        int(job["user_id"]), int(job.get("project_id") or 0) or None, "job_failed", "critical",
                        str(failure.get("friendly_title") or f"Linda úlohu nedokončila: {job.get('title') or job.get('type')}"),
                        str(failure.get("friendly_message") or "Úloha vyžaduje kontrolu."),
                        str(failure.get("action_url") or "?page=queue"), f"job-failed:{int(job['id'])}", 0
                    )
                except Exception:
                    pass
            failed += 1
            details.append({"id": job["id"], "type": job["type"], "ok": False, "error": str(failure.get("friendly_message") or exc), "retry": bool(failure.get("retry"))})
    return {"ok": failed == 0, "recovered_stale": recovered, "claimed": len(jobs), "done": done, "failed": failed, "details": details}

def worker_rewrite_post_text(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    payload = _payload(job)
    post_id = int(payload.get("post_id") or 0)
    instruction = str(payload.get("instruction") or "").strip()[:1500]
    post = one("SELECT * FROM posts WHERE id=:id AND project_id=:pid", {"id": post_id, "pid": pid})
    if not post:
        raise RuntimeError("Příspěvek nenalezen.")
    project = _project(pid)
    is_blog = str(post.get("platform") or "") == "blog"
    knowledge = _knowledge_for_chat(pid, f"{post.get('title') or ''} {post.get('body') or ''}", 10)
    existing = str(post.get("body") or "")
    if is_blog:
        prompt = (
            "Jsi Linda, česká seniorní content specialistka. Přepiš níže uvedený krátký webový obsah na plnohodnotný "
            "užitečný článek pro WordPress. Cíl je typicky 700–1200 slov podle tématu, žádná vata. Vrať JSON. "
            "Pole body musí být čisté bezpečné HTML vhodné do WordPressu: odstavce, H2/H3, seznamy a přirozené CTA. "
            "Nevymýšlej fakta, která nejsou v kontextu. Zachovej téma a obchodní cíl. Obrázek se NEMĚNÍ. "
            "Hashtagy pro web nech prázdné.\n\n"
            f"Projekt:\n{_profile(project)}\n\nRelevantní znalosti webu:\n{knowledge}\n\n"
            f"Současný název: {post.get('title') or ''}\nSoučasný text: {existing}\n"
            f"Doplňující pokyn uživatele: {instruction or 'bez dalšího pokynu'}"
        )
        operation = "marketing.article.create"; quality = "balanced"
    else:
        platform = str(post.get("platform") or "social")
        prompt = (
            "Jsi Linda, česká social media specialistka. Přepiš příspěvek tak, aby byl konkrétní, čitelný, "
            "odpovídal platformě a vedl ke konverzi. Vrať JSON. Zachovej fakta a záměr; obrázek se NEMĚNÍ. "
            "Nevkládej neověřené URL do textu.\n\n"
            f"Platforma: {platform}\nProjekt:\n{_profile(project)}\n\nRelevantní znalosti:\n{knowledge}\n\n"
            f"Současný název: {post.get('title') or ''}\nSoučasný text: {existing}\n"
            f"Současné hashtagy: {post.get('hashtags') or ''}\nDoplňující pokyn: {instruction or 'bez dalšího pokynu'}"
        )
        operation = "marketing.social.create"; quality = "economy"
    res = _engine_or_raise(
        project_id=pid, task_type="marketing", operation=operation, quality=quality,
        prompt=prompt, context={"project": _profile(project), "brand": _brand(project)},
        input_data={"post_id": post_id, "platform": post.get("platform"), "rewrite": True},
        output_contract=POST_REWRITE_SCHEMA,
    )
    try:
        out = as_json_object(res.result)
    except Exception:
        # Some Engine/router versions keep the validated object in a response wrapper.
        try:
            out = as_json_object(res.raw)
        except Exception:
            raw_text = str(res.result or "").strip()
            if is_blog and len(re.sub(r"<[^>]+>", " ", raw_text).split()) >= 250:
                out = {"title": str(post.get("title") or ""), "body": raw_text, "hashtags": ""}
            else:
                raise RuntimeError("AI odpověděla, ale výstup nešlo bezpečně převést na upravený obsah.")
    title = str(out.get("title") or post.get("title") or "")[:220]
    body = str(out.get("body") or out.get("body_html") or out.get("content") or existing).strip()
    hashtags = "" if is_blog else str(out.get("hashtags") or post.get("hashtags") or "").strip()
    if is_blog and len(re.sub(r"<[^>]+>", " ", body).split()) < 250:
        raise RuntimeError("AI vrátila příliš krátký text pro článek. Zkus úlohu znovu s konkrétnějším pokynem.")
    with db() as conn:
        conn.execute(text("UPDATE posts SET title=:title,body=:body,hashtags=:hashtags WHERE id=:id"), {"title": title, "body": body, "hashtags": hashtags, "id": post_id})
    log_ai_event(uid, pid, "post_rewrite", "Linda přegenerovala text příspěvku", {"post_id": post_id, "engine": res.audit(), "blog": is_blog})
    return {"message": "Text článku byl rozšířen; obrázek zůstal zachovaný." if is_blog else "Text příspěvku byl přegenerován; obrázek zůstal zachovaný.", "post_id": post_id, "engine": res.audit()}


# ---- Linda 3.8 parity workers -------------------------------------------------

def _wp_connector(uid: int, pid: int) -> dict[str, Any]:
    c = one("SELECT * FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND provider='wordpress' ORDER BY id DESC LIMIT 1", {"uid": uid, "pid": pid})
    if not c:
        raise RuntimeError("Chybí WordPress / Fresh Core konektor.")
    return c


def _extract_fc_data(res: Any) -> dict[str, Any]:
    data = res.data if hasattr(res, "data") and isinstance(res.data, dict) else {}
    return data.get("data") if isinstance(data.get("data"), dict) else data


def worker_wp_publish_blog(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    payload = _payload(job)
    blog_id = int(payload.get("blog_id") or 0)
    force = bool(payload.get("force_publish"))
    b = one("SELECT * FROM blog_posts WHERE id=:id AND project_id=:pid", {"id": blog_id, "pid": pid})
    if not b:
        raise RuntimeError("Článek nenalezen.")
    c = _wp_connector(uid, pid)
    fc = from_connector(c)
    status = "publish" if force else "draft"
    featured = media_public_url(int(b["featured_media_id"])) if b.get("featured_media_id") else ""
    tax = {}
    if b.get("wp_category_id"):
        tax["category"] = [int(b["wp_category_id"])]
    elif b.get("wp_category_name"):
        tax["category"] = [str(b["wp_category_name"])]
    _mark_progress(int(job["id"]), 80, "Publikuji článek přes Fresh Core…")
    res = fc.upsert_content({
        "post_type": "post",
        "post_id": int(b.get("wp_post_id") or 0) or None,
        "title": b.get("title") or "Článek",
        "slug": b.get("slug") or "",
        "content": b.get("body") or "",
        "excerpt": b.get("excerpt") or "",
        "status": status,
        "featured_image_url": featured,
        "taxonomies": tax,
    })
    if not res.ok:
        if res.status_code in {400, 404}:
            raise RuntimeError("Fresh Core na tomto webu ještě nemá write/content/upsert. Aktualizuj Fresh Core 0.5 a spusť úlohu znovu.")
        raise RuntimeError(f"Fresh Core publish: HTTP {res.status_code} {res.error}")
    data = _extract_fc_data(res)
    if not data.get("ok", True):
        raise RuntimeError(str(data.get("message") or "Fresh Core odmítl publikaci."))
    wp_id = int(data.get("post_id") or 0)
    url = str(data.get("url") or "")
    featured_wp = int(data.get("featured_media_id") or 0)
    if featured and not featured_wp:
        raise RuntimeError(f"WordPress článek byl vytvořen ({url or wp_id}), ale Fresh Core nepřipojil náhledový obrázek. Spusť publikaci znovu po kontrole veřejné media URL.")
    local_status = "published" if status == "publish" else "ready"
    with db() as conn:
        conn.execute(text("UPDATE blog_posts SET wp_post_id=:wp,destination_url=:url,status=:status,published_at=IF(:pub=1,NOW(),published_at) WHERE id=:id"), {"wp": wp_id or None, "url": url or None, "status": local_status, "pub": 1 if status == "publish" else 0, "id": blog_id})
        if status == "publish" and url:
            conn.execute(text("INSERT INTO project_knowledge(project_id,source_type,url,title,summary,remote_id,content_body,status,last_seen_at,modified_at) VALUES(:pid,'post',:url,:title,:summary,:remote,:body,'active',NOW(),NOW()) ON DUPLICATE KEY UPDATE title=VALUES(title),summary=VALUES(summary),remote_id=VALUES(remote_id),content_body=VALUES(content_body),status='active',last_seen_at=NOW(),modified_at=NOW()"), {"pid":pid,"url":url,"title":b.get("title") or "","summary":b.get("excerpt") or "","remote":str(wp_id or ""),"body":str(b.get("body") or '')})
    if status == "publish" and url:
        linked = rows("SELECT id,platform,activity_id,body FROM posts WHERE project_id=:pid AND destination_type='blog' AND destination_ref_id=:bid", {"pid":pid,"bid":blog_id})
        with db() as conn:
            for sp in linked:
                tracked = tracking_url(url, str(sp.get("platform") or "social"), int(sp.get("activity_id") or 0) or None)
                body = str(sp.get("body") or "")
                planned = str(b.get("planned_url") or "")
                if planned:
                    body = body.replace(planned, tracked)
                conn.execute(text("UPDATE posts SET body=:body,destination_url=:url,destination_status='ok',destination_checked_at=NOW(),publication_hold=NULL,publication_hold_at=NULL,publication_hold_message=NULL WHERE id=:id"), {"body":body,"url":tracked,"id":int(sp["id"])})
    return {"message": "Článek publikován přes Fresh Core." if status == "publish" else "Článek uložen ve WordPressu jako koncept.", "blog_id": blog_id, "wp_post_id": wp_id, "url": url, "status": status}


def worker_wp_publish_post(job: dict[str, Any]) -> dict[str, Any]:
    """Publish legacy posts.platform='blog' rows as real WordPress articles.

    Legacy Linda stored some web articles in `posts`, where the body was often
    only a social teaser. Before the first web publication we expand obviously
    short copy into a proper article, preserve its existing media, choose the
    most plausible WordPress category and send a sideloadable featured image.
    """
    uid, pid = int(job["user_id"]), int(job["project_id"])
    payload = _payload(job)
    post_id = int(payload.get("post_id") or 0)
    force = bool(payload.get("force_publish", True))
    post = one("SELECT * FROM posts WHERE id=:id AND project_id=:pid", {"id": post_id, "pid": pid})
    if not post:
        raise RuntimeError("Webový příspěvek nenalezen.")
    if str(post.get("destination_type") or "") == "blog" and int(post.get("destination_ref_id") or 0):
        blog_id=int(post.get("destination_ref_id") or 0)
        pseudo=dict(job); pseudo["payload"]=json.dumps({"blog_id":blog_id,"force_publish":force},ensure_ascii=False)
        result=worker_wp_publish_blog(pseudo)
        with db() as conn:
            conn.execute(text("UPDATE posts SET status=:status,published_at=IF(:pub=1,NOW(),published_at),external_id=:ext,permalink=:url,destination_url=:url,destination_status=IF(:url IS NULL OR :url='',destination_status,'ok'),destination_checked_at=IF(:url IS NULL OR :url='',destination_checked_at,NOW()) WHERE id=:id"),
                         {"status":"published" if force else "ready","pub":1 if force else 0,"ext":str(result.get("wp_post_id") or "") or None,"url":str(result.get("url") or "") or None,"id":post_id})
        result["post_id"]=post_id
        return result

    # A legacy web card with 2–3 sentences is a promo, not an article. Linda may
    # try to expand it through AI Engine, but an AI contract failure must never
    # turn into an endless publication retry loop. If enrichment fails, the
    # article is safely held for review and the worker finishes normally.
    _mark_progress(int(job["id"]), 22, "Kontroluji připravenost článku…")
    plain = re.sub(r"<[^>]+>", " ", str(post.get("body") or ""))
    word_count = len([x for x in plain.split() if x.strip()])
    if word_count < 220:
        _mark_progress(int(job["id"]), 35, "Text je krátký. Linda ho zkusí rozšířit přes AI Engine…")
        try:
            pseudo = dict(job)
            pseudo["payload"] = json.dumps({"post_id": post_id, "instruction": "Rozviň na plnohodnotný článek připravený k okamžité publikaci na webu."}, ensure_ascii=False)
            worker_rewrite_post_text(pseudo)
            post = one("SELECT * FROM posts WHERE id=:id AND project_id=:pid", {"id": post_id, "pid": pid}) or post
            plain = re.sub(r"<[^>]+>", " ", str(post.get("body") or ""))
            word_count = len([x for x in plain.split() if x.strip()])
        except Exception as exc:
            message = (
                f"Článek má jen přibližně {word_count} slov a automatické rozšíření přes AI Engine se teď nepodařilo. "
                "Linda ho raději nepublikovala jako příliš krátký text. Otevři detail, text doplň nebo po opravě AI Engine spusť publikaci znovu."
            )
            hold_publication(post, "content_short", message, uid)
            with db() as conn:
                conn.execute(text("UPDATE task_queue SET notify_user=0 WHERE id=:id"), {"id": int(job["id"])})
            return {
                "message": message,
                "post_id": post_id,
                "held": "content_short",
                "word_count": word_count,
                "technical_warning": str(exc)[:1200],
            }
        if word_count < 220:
            message = (
                f"Článek má po rozšíření stále jen přibližně {word_count} slov. Linda publikaci pozastavila, aby na web neposlala příliš tenký obsah."
            )
            hold_publication(post, "content_short", message, uid)
            with db() as conn:
                conn.execute(text("UPDATE task_queue SET notify_user=0 WHERE id=:id"), {"id": int(job["id"])})
            return {"message": message, "post_id": post_id, "held": "content_short", "word_count": word_count}

    _mark_progress(int(job["id"]), 62, "Připravuji WordPress a rubriku…")
    c = _wp_connector(uid, pid)
    fc = from_connector(c)
    status = "publish" if force else "draft"
    media = one("SELECT id FROM media_assets WHERE post_id=:id AND file_path IS NOT NULL AND file_path<>'' ORDER BY id DESC LIMIT 1", {"id": post_id})
    featured = media_public_url(int(media["id"])) if media else ""
    body = str(post.get("body") or "")
    if "<" not in body:
        body = "".join(f"<p>{line}</p>" for line in body.splitlines() if line.strip())

    cats = wordpress_categories(uid, pid)
    category_id = int(payload.get("wp_category_id") or 0) or None
    chosen = next((c for c in cats if int(c.get("id") or 0) == category_id), None) if category_id else None
    if not chosen:
        chosen = suggest_wordpress_category(pid, cats, str(post.get("title") or ""), body)
        category_id = int(chosen.get("id") or 0) if chosen else None
    tax = {"category": [category_id]} if category_id else {}

    plain_body = re.sub(r"<[^>]+>", " ", body)
    excerpt = re.sub(r"\s+", " ", str(post.get("notes") or "").strip() or plain_body).strip()[:420]
    _mark_progress(int(job["id"]), 80, "Publikuji článek přes Fresh Core…")
    res = fc.upsert_content({
        "post_type": "post",
        "external_id": f"linda-post-{post_id}",
        "change_id": f"linda-post-{post_id}-{int(datetime.now().timestamp())}",
        "source_module": "linda",
        "title": post.get("title") or "Článek",
        "content": body,
        "excerpt": excerpt,
        "status": status,
        "featured_image_url": featured,
        "taxonomies": tax,
    })
    if not res.ok:
        raise RuntimeError(f"Fresh Core publish: HTTP {res.status_code} {res.error}")
    _mark_progress(int(job["id"]), 94, "Ukládám výsledek publikace…")
    data = _extract_fc_data(res)
    wp_id = int(data.get("post_id") or 0)
    url = str(data.get("url") or "")
    featured_wp = int(data.get("featured_media_id") or 0)
    with db() as conn:
        conn.execute(text(
            "UPDATE posts SET status=:status,published_at=IF(:pub=1,NOW(),published_at),external_id=:ext,permalink=:url,"
            "destination_url=COALESCE(:url,destination_url),destination_status=IF(:url IS NULL OR :url='',destination_status,'ok'),destination_checked_at=IF(:url IS NULL OR :url='',destination_checked_at,NOW()),"
            "publication_hold=NULL,publication_hold_at=NULL,publication_hold_message=NULL WHERE id=:id"
        ), {"status":"published" if status=="publish" else "ready","pub":1 if status=="publish" else 0,"ext":str(wp_id or "") or None,"url":url or None,"id":post_id})
    if featured and not featured_wp:
        raise RuntimeError(f"WordPress článek byl vytvořen ({url or wp_id}), ale Fresh Core nepřipojil náhledový obrázek. Zkontroluj veřejnou media URL a spusť publikaci znovu.")
    cat_msg = f" · rubrika {chosen.get('name')}" if chosen else ""
    return {"message":("Článek publikován na WordPress přes Fresh Core" if status=="publish" else "Článek uložen ve WordPressu jako koncept") + cat_msg + ".", "post_id":post_id,"wp_post_id":wp_id,"url":url,"status":status,"featured_media_id":featured_wp,"category":chosen}

def worker_social_publish(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    post_id = int(_payload(job).get("post_id") or 0)
    post = one("SELECT * FROM posts WHERE id=:id AND project_id=:pid", {"id":post_id,"pid":pid})
    if not post:
        raise RuntimeError("Post nenalezen.")
    pf = connector_preflight(uid, pid, str(post.get("platform") or ""), True)
    if not pf.get("ok"):
        hold_publication(post, "connector", str(pf.get("message") or "Konektor není připraven."), uid)
        with db() as conn:
            conn.execute(text("UPDATE posts SET status='scheduled' WHERE id=:id"), {"id":post_id})
            conn.execute(text("UPDATE task_queue SET notify_user=0 WHERE id=:id"), {"id":int(job["id"])})
        return {"message":"Publikaci jsem odložila: konektor není připraven.","held":"connector"}
    # Conversion-first default: when a social post has no destination, let Linda
    # choose the most relevant known web URL before publication.
    if not str(post.get("destination_url") or "").strip() and str(post.get("destination_type") or "") != "blog":
        try:
            regenerate_post_link(uid, post_id)
            post = one("SELECT * FROM posts WHERE id=:id AND project_id=:pid", {"id":post_id,"pid":pid}) or post
        except Exception:
            pass
    dest = check_destination(post, uid, True)
    if not dest.get("ok"):
        hold_publication(post, "link", str(dest.get("message") or "Cílový odkaz není připraven."), uid)
        with db() as conn:
            conn.execute(text("UPDATE posts SET status='scheduled' WHERE id=:id"), {"id":post_id})
            conn.execute(text("UPDATE task_queue SET notify_user=0 WHERE id=:id"), {"id":int(job["id"])})
        return {"message":"Publikaci jsem odložila: cílový odkaz není dostupný.","held":"link"}
    post["destination_url"] = dest.get("url") or post.get("destination_url")
    result = publish_post(post, pf["connector"])
    if not result.get("ok"):
        raise RuntimeError(str(result.get("message") or "Publikace selhala."))
    ext = str(result.get("external_id") or ""); permalink = str(result.get("permalink") or "")
    with db() as conn:
        conn.execute(text("UPDATE posts SET status='published',published_at=NOW(),external_id=:ext,permalink=:permalink,publication_hold=NULL,publication_hold_at=NULL,publication_hold_message=NULL WHERE id=:id"), {"ext":ext or None,"permalink":permalink or None,"id":post_id})
        conn.execute(text("INSERT INTO publication_log(user_id,project_id,post_id,provider,status,message,response,external_id,permalink) VALUES(:uid,:pid,:post,:provider,'published',:msg,:response,:ext,:permalink)"), {"uid":uid,"pid":pid,"post":post_id,"provider":post.get("platform"),"msg":str(result.get("message") or "Publikováno"),"response":json.dumps(result.get("response") or result,ensure_ascii=False,default=str),"ext":ext or None,"permalink":permalink or None})
    return {"message":result.get("message") or "Publikováno.","external_id":ext,"permalink":permalink}


def worker_analytics_sync(job: dict[str, Any]) -> dict[str, Any]:
    uid, pid = int(job["user_id"]), int(job["project_id"])
    c = _wp_connector(uid, pid)
    res = from_connector(c).analytics_summary()
    if not res.ok:
        if res.status_code in {400,404}:
            raise RuntimeError("Fresh Core na webu ještě nemá analytics/summary. Aktualizuj Fresh Core 0.5.")
        raise RuntimeError(f"Fresh Core analytics: HTTP {res.status_code} {res.error}")
    data = _extract_fc_data(res)
    woo = data.get("woocommerce") if isinstance(data.get("woocommerce"), dict) else {}
    with db() as conn:
        conn.execute(text("UPDATE projects SET analytics_synced_at=NOW() WHERE id=:pid"), {"pid":pid})
        conn.execute(text("UPDATE social_connectors SET metadata=:meta,last_sync_at=NOW() WHERE id=:id"), {"meta":json.dumps(data,ensure_ascii=False),"id":int(c["id"])})
        if woo and woo.get("available"):
            conn.execute(text("INSERT INTO content_metrics(user_id,project_id,post_id,provider,external_id,metric_date,hour_of_day,impressions,reach_count,engagements,clicks,conversions,revenue,currency,raw_json) VALUES(:uid,:pid,NULL,'wordpress',:ext,CURDATE(),NULL,0,0,0,0,:conv,:rev,:cur,:raw) ON DUPLICATE KEY UPDATE conversions=VALUES(conversions),revenue=VALUES(revenue),currency=VALUES(currency),raw_json=VALUES(raw_json),updated_at=NOW()"), {"uid":uid,"pid":pid,"ext":f"wordpress-site-{pid}","conv":int(woo.get("orders") or 0),"rev":float(woo.get("revenue") or 0),"cur":str(woo.get("currency") or ""),"raw":json.dumps(data,ensure_ascii=False)})
    return {"message":"Analytický souhrn z Fresh Core synchronizován.","data":data}


def _engine_image_bytes(value: Any) -> tuple[bytes, str, dict[str, Any]]:
    """Extract, download and strictly validate a real image returned by Fresh AI Engine."""
    data = _engine_image_payload(value)
    raw_b64 = str(data.get("base64") or "")
    url = str(data.get("url") or "")
    mime_hint = str(data.get("mime_type") or "").lower()
    content = b""
    source = ""
    if raw_b64:
        source = "base64"
        if raw_b64.startswith("data:"):
            try:
                header, raw_b64 = raw_b64.split(",", 1)
            except ValueError as exc:
                raise RuntimeError("AI Engine vrátil poškozený data URL obrázku.") from exc
            if ";" in header:
                mime_hint = header[5:].split(";", 1)[0].lower()
        try:
            content = base64.b64decode(raw_b64, validate=False)
        except Exception as exc:
            raise RuntimeError(f"AI Engine vrátil nečitelná obrazová data ({type(exc).__name__}).") from exc
    elif url:
        source = "url"
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise RuntimeError("AI Engine vrátil neplatnou URL obrázku.")
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    ctype = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                    if ctype:
                        mime_hint = ctype
                    chunks=[]; total=0; limit=32*1024*1024
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise RuntimeError("Vygenerovaný obrázek je větší než bezpečný limit 32 MB.")
                        chunks.append(chunk)
                    content=b"".join(chunks)
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Obrázek z AI Enginu se nepodařilo stáhnout ({type(exc).__name__}).") from exc
    else:
        result_type = ""
        if isinstance(value, dict):
            result_type = str(value.get("result_type") or "")
        if result_type == "text":
            raise RuntimeError("AI Engine vrátil text místo obrázku.")
        raise RuntimeError("AI Engine nevytvořil obrázek – v odpovědi chybí obrazová data nebo URL.")
    meta = validate_image_bytes(content, mime_hint)
    meta.update({"source": source, "provider_mime_type": mime_hint or None, "url": url or None})
    return content, str(meta.get("format") or "png"), meta


def _cleanup_generated_file(target: Path | None, media_id: int | None = None) -> None:
    try:
        if target and target.exists():
            target.unlink()
    except Exception:
        pass
    if media_id:
        try:
            for thumb in (UPLOAD_ROOT / ".thumbs").glob(f"{media_id}-*.webp"):
                try: thumb.unlink()
                except Exception: pass
        except Exception:
            pass


def _save_engine_image(
    pid: int,
    post_id: int | None,
    blog_id: int | None,
    prompt: str,
    result: Any,
    kind: str,
    title: str,
    *,
    existing_asset_id: int | None = None,
) -> tuple[int, dict[str, Any]]:
    """Persist one Engine image only after validation/optimization and create its thumbnail.

    DB binding is committed only after the generated file and thumbnail are both valid.
    On any failure the new file is removed and the queue job stays non-successful.
    """
    content, ext, source_meta = _engine_image_bytes(result)
    optimized, optimized_ext, optimize_info = optimize_image_bytes(content, f"generated.{ext}")
    content = optimized
    ext = optimized_ext or ext
    final_meta = validate_image_bytes(content, f"image/{'jpeg' if ext in {'jpg','jpeg'} else ext}")
    root = Path(__file__).resolve().parents[1] / "storage" / "uploads" / "generated"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(content).hexdigest()[:12]
    name = f"ai-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{digest}.{ext}"
    target = root / name
    tmp = root / (name + ".tmp")
    media_id: int | None = None
    old_asset_path: str | None = None
    try:
        tmp.write_bytes(content)
        validate_image_bytes(tmp.read_bytes(), file_mime(tmp.with_suffix('.'+ext)))
        tmp.replace(target)
        rel = str(target.relative_to(Path(__file__).resolve().parents[1]))
        if blog_id and not post_id:
            linked = one("SELECT id FROM posts WHERE project_id=:pid AND destination_type='blog' AND destination_ref_id=:bid ORDER BY id DESC LIMIT 1", {"pid":pid,"bid":blog_id})
            if linked:
                post_id = int(linked.get("id") or 0) or None
        with db() as conn:
            if existing_asset_id:
                old = conn.execute(text("SELECT file_path FROM media_assets WHERE id=:id AND project_id=:pid LIMIT 1"), {"id":existing_asset_id,"pid":pid}).mappings().first()
                if not old:
                    raise RuntimeError("Cílové médium pro vygenerovaný obrázek už neexistuje.")
                old_asset_path = str(old.get("file_path") or "")
                conn.execute(text("UPDATE media_assets SET post_id=COALESCE(:post,post_id),blog_post_id=COALESCE(:blog,blog_post_id),prompt=:prompt,file_path=:path,provider='fresh-ai-engine',status='generated',kind=:kind,title=:title,error_message=NULL WHERE id=:id AND project_id=:pid"), {"post":post_id,"blog":blog_id,"prompt":prompt,"path":rel,"kind":kind,"title":title[:190],"id":existing_asset_id,"pid":pid})
                media_id = int(existing_asset_id)
            else:
                ins = conn.execute(text("INSERT INTO media_assets(project_id,post_id,blog_post_id,prompt,file_path,provider,status,kind,title) VALUES(:pid,:post,:blog,:prompt,:path,'fresh-ai-engine','generated',:kind,:title)"), {"pid":pid,"post":post_id,"blog":blog_id,"prompt":prompt,"path":rel,"kind":kind,"title":title[:190]})
                media_id = int(ins.lastrowid)
            if blog_id:
                conn.execute(text("UPDATE blog_posts SET featured_media_id=:mid WHERE id=:id AND project_id=:pid"), {"mid":media_id,"id":blog_id,"pid":pid})
            thumb = thumbnail_path(int(media_id), target)
            if not thumb or not thumb.is_file() or thumb.stat().st_size <= 0:
                raise RuntimeError("Obrázek se podařilo uložit, ale nepodařilo se vytvořit náhled.")
        # Old free-image file can be deleted only after the DB update succeeded.
        if old_asset_path and old_asset_path != rel:
            old_path = resolve_upload_path(old_asset_path, allow_scan=False)
            if old_path and old_path != target:
                try:
                    refs = rows("SELECT COUNT(*) c FROM media_assets WHERE file_path=:p", {"p":old_asset_path})
                    if refs and int(refs[0].get("c") or 0) == 0:
                        old_path.unlink()
                except Exception:
                    pass
        return int(media_id), {
            "source": source_meta,
            "optimized": optimize_info,
            "stored": final_meta,
            "file_path": rel,
            "thumbnail": str(thumb.relative_to(Path(__file__).resolve().parents[1])),
        }
    except Exception:
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass
        _cleanup_generated_file(target, media_id)
        raise


def worker_image_generate(job: dict[str, Any]) -> dict[str, Any]:
    pid = int(job["project_id"]); payload = _payload(job); typ = str(job["type"])
    job_id = int(job["id"]); uid = int(job["user_id"])
    _mark_progress(job_id, 20, "Linda připravuje zadání obrázku a Brand Kit…")
    project = _project(pid)
    post_id = int(payload.get("post_id") or 0) or None
    blog_id = int(payload.get("blog_id") or 0) or None
    existing_asset_id: int | None = None
    if typ == "free_image_generate":
        existing_asset_id = int(payload.get("asset_id") or 0) or None
        asset = one("SELECT * FROM media_assets WHERE id=:id AND project_id=:pid", {"id":existing_asset_id,"pid":pid}) if existing_asset_id else None
        if not asset:
            raise RuntimeError("Volný obrázek nebyl nalezen.")
        title = str(asset.get("title") or "Volná kreativita")
        specific = str(asset.get("prompt") or "")
        kind = "free_image"
    elif post_id:
        target = one("SELECT * FROM posts WHERE id=:id AND project_id=:pid", {"id":post_id,"pid":pid})
        if not target:
            raise RuntimeError("Příspěvek pro obrázek nebyl nalezen.")
        title = str(target.get("title") or "Post")
        specific = str(target.get("image_prompt") or "")
        kind = "carousel_slide" if typ in {"carousel_slide","image_generate_carousel_slide"} else "post_image"
        if typ in {"carousel_slide","image_generate_carousel_slide"}:
            specific += f"\nCarousel slide {payload.get('slide_index')}: {payload.get('slide') or ''}"
    elif blog_id:
        target = one("SELECT * FROM blog_posts WHERE id=:id AND project_id=:pid", {"id":blog_id,"pid":pid})
        if not target:
            raise RuntimeError("Článek pro obrázek nebyl nalezen.")
        title = str(target.get("title") or "Článek")
        specific = str(target.get("image_prompt") or "")
        kind = "blog_featured"
    else:
        raise RuntimeError("Chybí cílový post, článek nebo mediální asset.")

    prompt = f"Vizuální styl projektu:\n{_brand(project)}\n\nVytvoř marketingový obrázek pro: {title}\n{specific}\nNevkládej náhodná loga ani dlouhé texty."

    # Local media tooling is mandatory. Fail before the paid provider call rather
    # than generating the same image repeatedly and discovering the missing
    # dependency only afterwards.
    require_pillow()
    transparent = bool(payload.get("transparent_background") or payload.get("transparent"))
    _mark_progress(job_id, 40, "Generuji obrázek přes Fresh AI Engine…")
    res = _engine_or_raise(
        project_id=pid,
        task_type="image_generation",
        operation="image.generate",
        quality="balanced",
        prompt=prompt,
        context={"brand":_brand(project), "source":"Linda", "job_id":job_id},
        input_data={
            "post_id":post_id, "blog_id":blog_id, "asset_id":existing_asset_id, "kind":kind, "job_id":job_id,
            "output_format":"png" if transparent else "jpeg",
            "background":"transparent" if transparent else "opaque",
            "output_compression":None if transparent else max(60, min(95, int(settings.media_image_quality))),
        },
    )
    envelope = {"result":res.result,"raw":res.raw,"result_type":res.result_type}
    _mark_progress(job_id, 70, "Obrázek je vytvořený. Ověřuji formát a rozměry…")
    try:
        # Validation is performed inside _save_engine_image too; doing it here makes
        # the queue message precise before any file/DB mutation occurs.
        _engine_image_bytes(envelope)
        _mark_progress(job_id, 82, "Optimalizuji obrázek pro web a sociální sítě…")
        media_id, media_meta = _save_engine_image(pid, post_id, blog_id, prompt, envelope, kind, title, existing_asset_id=existing_asset_id)
        _mark_progress(job_id, 95, "Připojuji obrázek k obsahu a vytvářím náhled…")
    except Exception as exc:
        audit = res.audit()
        keys = sorted(str(k) for k in (res.raw or {}).keys())[:30]
        log_ai_event(uid,pid,"image_generate_error",f"Obrázek se nepodařilo uložit: {exc}",{"engine":audit,"error":str(exc),"engine_top_level_keys":keys,"result_type":res.result_type,"job_id":job_id})
        raise RuntimeError(str(exc)) from exc
    log_ai_event(uid,pid,"image_generated","Obrázek vytvořen, optimalizován a připojen přes Fresh AI Engine",{"engine":res.audit(),"media_id":media_id,"post_id":post_id,"blog_id":blog_id,"media":media_meta,"job_id":job_id})
    size = int(((media_meta.get("stored") or {}).get("bytes") or 0))
    return {"message":f"Hotovo – připravila jsem nový obrázek{f' ({size/1024:.0f} kB)' if size else ''}.","media_id":media_id,"post_id":post_id,"blog_id":blog_id,"media":media_meta,"engine":res.audit()}


def worker_sitemap_async(job: dict[str, Any]) -> dict[str, Any]:
    """Compatibility worker for legacy queued sitemap jobs.
    Prefer Fresh Core when a WordPress connector exists; otherwise ingest sitemap URLs/titles without AI.
    """
    uid,pid=int(job["user_id"]),int(job["project_id"]); project=_project(pid)
    wp=one("SELECT * FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND provider='wordpress' ORDER BY id DESC LIMIT 1",{"uid":uid,"pid":pid})
    if wp:
        return worker_wordpress_sync({**job,"type":"wordpress_sync","payload":json.dumps({"connector_id":int(wp["id"]),"manual":False})})
    website=str(project.get("website_url") or "").rstrip("/")
    if not website: raise RuntimeError("Projekt nemá web ani WordPress konektor.")
    import xml.etree.ElementTree as ET
    sitemap=website+"/sitemap.xml"
    with httpx.Client(timeout=30,follow_redirects=True,headers={"User-Agent":"FreshAISocialBot/0.4.1"}) as client:
        rr=client.get(sitemap); rr.raise_for_status(); root=ET.fromstring(rr.content)
    urls=[]
    for el in root.iter():
        if el.tag.endswith("loc") and el.text and el.text.startswith("http"): urls.append(el.text.strip())
    with db() as conn:
        for url in urls[:500]:
            title=url.rstrip("/").split("/")[-1].replace("-"," ") or project.get("name") or url
            conn.execute(text("INSERT INTO project_knowledge(project_id,source_type,url,title,status,last_seen_at) VALUES(:pid,'page',:url,:title,'active',NOW()) ON DUPLICATE KEY UPDATE title=VALUES(title),status='active',last_seen_at=NOW()"),{"pid":pid,"url":url,"title":title[:255]})
        conn.execute(text("UPDATE projects SET knowledge_synced_at=NOW() WHERE id=:pid"),{"pid":pid})
    queue_job(uid,pid,"project_profile","Linda aktualizuje profil firmy po sitemapě",{"source":"sitemap_change"},worker_group="text",priority=80,parent_job_id=int(job["id"]),dedupe_key=f"project_profile:{pid}")
    return {"message":f"Sitemapa načtena: {len(urls[:500])} URL. Profil projektu se aktualizuje následně.","items":len(urls[:500])}


BRAND_VISION_SCHEMA = {
    "type":"object",
    "properties":{
        "summary":{"type":"string"},"colors":{"type":"array","items":{"type":"string"}},
        "typography":{"type":"array","items":{"type":"string"}},"composition":{"type":"array","items":{"type":"string"}},
        "photography":{"type":"array","items":{"type":"string"}},"illustration":{"type":"array","items":{"type":"string"}},
        "do":{"type":"array","items":{"type":"string"}},"dont":{"type":"array","items":{"type":"string"}},
        "image_prompt_rules":{"type":"array","items":{"type":"string"}},
    },
    "required":["summary","colors","typography","composition","photography","illustration","do","dont","image_prompt_rules"]
}


def worker_brand_analyze(job: dict[str, Any]) -> dict[str, Any]:
    pid=int(job["project_id"]); uid=int(job.get("user_id") or 0); project=_project(pid)
    refs=rows("SELECT id,title,notes,file_path FROM visual_references WHERE project_id=:pid ORDER BY id DESC LIMIT 6",{"pid":pid})
    if not refs and not project.get("visual_style"):
        raise RuntimeError("Brand kit je prázdný. Nahraj alespoň jeden vizuální vzor nebo doplň vizuální styl.")

    # Generic text requests must never receive megabytes of base64 disguised as
    # application JSON. First let Engine inspect each reference on its dedicated
    # image.understand path, then synthesize Style DNA from compact observations.
    root=Path(__file__).resolve().parents[1]
    visual_notes=[]
    vision_audits=[]
    for r in refs[:4]:
        try:
            path=(root/str(r.get("file_path") or "")).resolve()
            if not path.is_file() or path.stat().st_size>8_000_000:
                continue
            mime=mimetypes.guess_type(path.name)[0] or "image/jpeg"
            if not mime.startswith("image/"):
                continue
            image_base64=base64.b64encode(path.read_bytes()).decode()
            vision_prompt=(
                "Jsi vizuální analytik pro marketingovou značku. Popiš pouze to, co je skutečně vidět: "
                "barevnost, typografický charakter, kompozici, styl fotografie/ilustrace, opakující se grafické prvky, "
                "míru kontrastu, práci s logem a co by měl generátor nových vizuálů zachovat nebo naopak nedělat. "
                "Nevymýšlej názvy fontů ani barvy, které z obrázku nelze spolehlivě určit. Odpověz stručně česky."
            )
            vr=_engine_or_raise(
                project_id=pid, task_type="image_understanding", operation="image.understand", quality="balanced",
                prompt=vision_prompt, context={"source":"Linda Brand Kit","reference_id":int(r["id"])},
                input_data={"image_base64":image_base64,"mime_type":mime}, output_contract=None,
            )
            description=str(vr.result or "").strip()
            if description:
                visual_notes.append({
                    "reference_id":int(r["id"]),
                    "title":str(r.get("title") or path.name),
                    "notes":str(r.get("notes") or ""),
                    "visual_analysis":description[:5000],
                })
                vision_audits.append(vr.audit())
        except Exception as exc:
            vision_audits.append({"reference_id":int(r.get("id") or 0),"ok":False,"error":str(exc)[:700]})
            continue

    if refs and not visual_notes and not project.get("visual_style"):
        raise RuntimeError("Linda nedokázala přečíst žádný obrázek z Brand Kitu. Zkontroluj obrazové soubory a image understanding v AI Enginu.")

    prompt=(
        "Jsi Linda, brand a creative director. Z textového popisu projektu a z níže uvedených skutečných vizuálních analýz "
        "vytvoř Style DNA prakticky použitelné pro konzistentní generování marketingových obrázků. "
        "Nevymýšlej loga ani prvky, které nejsou ve vzorech. Vrať JEDEN JSON objekt podle požadovaného schématu, nikdy pole objektů.\n\n"
        f"Projekt: {project.get('name')}\nTextový vizuální styl: {project.get('visual_style') or ''}\nTone: {project.get('brand_tone') or ''}\n"
        f"Vizuální analýzy referencí: {json.dumps(visual_notes, ensure_ascii=False)}"
    )
    res=_engine_or_raise(
        project_id=pid,task_type="analysis",operation="analysis.brand_style",quality="balanced",prompt=prompt,
        context={"project":_profile(project)},
        input_data={"visual_analyses":visual_notes,"reference_count":len(visual_notes)},
        output_contract=BRAND_VISION_SCHEMA,
    )
    dna=as_json_object(res.result)
    with db() as conn:
        conn.execute(text("UPDATE projects SET brand_style_dna=:dna,brand_analysis_at=NOW() WHERE id=:pid"),{"dna":json.dumps(dna,ensure_ascii=False),"pid":pid})
        for r in refs:
            conn.execute(text("UPDATE visual_references SET analyzed_at=NOW() WHERE id=:id"),{"id":int(r["id"])})
    if uid:
        log_ai_event(uid,pid,"brand_analyzed","Linda přečetla vizuální reference a aktualizovala Brand Style DNA",{
            "engine":res.audit(),"vision_runs":vision_audits,"reference_count":len(visual_notes)
        })
    return {"message":f"Brand Style DNA bylo aktualizováno z {len(visual_notes)} vizuálních referencí.","style_dna":dna,"engine":res.audit(),"vision_runs":vision_audits}
