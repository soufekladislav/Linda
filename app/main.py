from __future__ import annotations

import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import db, one, ping as db_ping, rows, schema_report
from .engine import engine_client
from .fresh_core import from_connector
from . import fresh_key
from .manifest import build_manifest
from .media_tools import resolve_upload_path, optimize_image_bytes, optimize_video_path, validate_image_bytes, thumbnail_path, file_mime, pillow_available
from .repositories import (
    action_rows, blog_row, blog_rows, connector, content_rows, create_or_update_connector,
    create_project, dashboard_stats, delete_connector, delete_post, delete_project, delete_reference,
    duplicate_post, knowledge_rows, list_activities, list_connectors, list_projects, mark_notifications_read,
    media_rows, notification_rows, notification_preferences, post_row, project, project_options, queue_job, queue_rows, queue_summary,
    recent_metrics, reference_rows, save_notification_preferences, save_reference, update_blog, update_connector_test, update_post, update_project,
    user_by_email, user_by_id, chat_events, connected_platforms, all_users, log_ai_event,
)
from .security import (
    csrf_token,
    current_user,
    hash_password,
    is_superadmin,
    safe_developer_token,
    verify_csrf,
    verify_php_password,
)
from .workers import process_available_jobs, run_linda_chat_direct, recover_stale_jobs, cleanup_legacy_chat_jobs, remind_unresolved_failures
from .workflows import conversion_workflows, post_formats, save_conversion_workflows, save_post_formats, workflow_by_key, planned_tasks, save_planned_tasks
from .proactivity import enqueue_due_planned_tasks
from .legacy_features import (
    connector_preflight, refresh_action_items, refresh_blog_links, regenerate_post_link, notify,
    schedule_due_publications, test_connector, check_destination, wordpress_categories, suggest_wordpress_category,
)

APP_ROOT = Path(__file__).resolve().parents[1]
LINDA_IMAGE = "https://www.freshcrackers.cz/wp-content/uploads/2026/06/ai-social-media.png"

QUEUE_STATUS_LABELS = {"queued":"Čeká","running":"Probíhá","done":"Dokončeno","failed":"Selhalo"}
QUEUE_GROUP_LABELS = {"text":"Text a AI","media":"Média","publication":"Publikace","maintenance":"Údržba","all":"Vše"}
QUEUE_TYPE_LABELS = {
    "project_profile":"AI profil projektu","ai_activity":"Marketingový plán","article_generate":"Článek","social_derivatives":"Distribuce článku",
    "wordpress_sync":"Synchronizace webu","wp_publish_blog":"Publikace článku na web","wp_publish_post":"Publikace webového obsahu",
    "social_publish_post":"Publikace na sociální síť","analytics_sync":"Synchronizace analytiky","image_generate_post":"Generování obrázku",
    "image_generate_blog":"Obrázek článku","carousel_slide":"Slide carouselu","image_generate_carousel_slide":"Slide carouselu",
    "free_image_generate":"Volný obrázek","brand_analyze":"Kontrola Brand Kitu","media_maintenance":"Úklid médií",
    "rewrite_post_text":"Přegenerování textu","manual_post_generate":"Ruční příspěvek s Lindou","proactive_review":"Proaktivní kontrola projektu",
}

app = FastAPI(title="Linda", version=settings.app_version)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="fresh_ai_social_session",
    same_site="lax",
    https_only=settings.app_url.startswith("https://"),
    max_age=60 * 60 * 24 * 30,
)
app.mount("/assets", StaticFiles(directory=APP_ROOT / "assets"), name="assets")
fresh_key.ensure_schema()

templates = Environment(
    loader=FileSystemLoader(APP_ROOT / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)
templates.filters["tojson"] = lambda value, indent=None: json.dumps(value, ensure_ascii=False, indent=indent, default=str)

def _chat_markup(value: Any) -> Markup:
    """Render a deliberately small, escaped markdown subset for Linda chat."""
    text_value = html.escape(str(value or ""))
    # Markdown links: only ordinary web or local app links survive.
    def repl_link(match):
        label = match.group(1)
        url = html.unescape(match.group(2)).strip()
        if not (url.startswith("https://") or url.startswith("http://") or url.startswith("/?") or url.startswith("?page=")):
            return label
        safe_url = html.escape(url, quote=True)
        external = ' target="_blank" rel="noopener"' if url.startswith(("http://", "https://")) else ''
        return f'<a href="{safe_url}"{external}>{label}</a>'
    text_value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl_link, text_value)
    text_value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text_value)
    # Auto-link plain URLs after escaping.
    text_value = re.sub(r'(?<!["=])(https?://[^\s<]+)', lambda m: f'<a href="{html.escape(html.unescape(m.group(1)), quote=True)}" target="_blank" rel="noopener">{m.group(1)}</a>', text_value)
    lines = text_value.splitlines()
    out = []
    in_ul = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(('- ', '• ')):
            if not in_ul:
                out.append('<ul>'); in_ul = True
            out.append(f'<li>{stripped[2:]}</li>')
        else:
            if in_ul:
                out.append('</ul>'); in_ul = False
            out.append(line if line else '<br>')
    if in_ul:
        out.append('</ul>')
    return Markup('<br>'.join(out).replace('<br><ul>', '<ul>').replace('</ul><br>', '</ul>'))

templates.filters["chat_markup"] = _chat_markup

NAV = {
    "projects": "Projekty",
    "queue": "Fronta úloh",
    "settings": "Nastavení",
}
PROJECT_NAV = [
    ("dashboard", "Linda"),
    ("calendar", "Kalendář"),
    ("content", "Obsah"),
    ("media", "Média"),
    ("analytics", "Výsledky"),
]

PROVIDERS = {
    "wordpress": "WordPress",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "google_business": "Google Business Profile",
    "x": "X",
    "tiktok": "TikTok",
    "blog": "Blog",
}
PROVIDER_ICONS = {
    "wordpress": "W",
    "facebook": "f",
    "instagram": "◎",
    "linkedin": "in",
    "google_business": "G",
    "x": "𝕏",
    "tiktok": "♪",
    "blog": "B",
}


def _redirect(page: str, extra: str = "") -> RedirectResponse:
    return RedirectResponse(url=f"/?page={page}{extra}", status_code=303)


def _flash(request: Request, message: str | None = None) -> str | None:
    if message is not None:
        request.session["flash"] = message
        return None
    return request.session.pop("flash", None)


def _context(request: Request, title: str, page: str, **extra: Any) -> dict[str, Any]:
    user = current_user(request)
    uid = int(user["id"]) if user else 0
    summary = queue_summary(uid) if user else {"queued": 0, "running": 0, "failed": 0, "done": 0}
    unread = 0
    if user:
        try:
            unread = int(rows(
                "SELECT COUNT(*) c FROM notifications WHERE user_id=:uid AND is_read=0",
                {"uid": uid}
            )[0]["c"])
        except Exception:
            unread = 0
    project_list = project_options(uid) if user else []
    current_project_id = int(request.query_params.get("project_id") or request.session.get("current_project_id") or (project_list[0]["id"] if project_list else 0))
    current_project = project(uid, current_project_id) if user and current_project_id else None
    if current_project:
        request.session["current_project_id"] = current_project_id
    elif user:
        current_project_id = 0
    approval_count_global = 0
    if user and current_project_id:
        try:
            approval_count_global = int(rows(
                "SELECT COUNT(*) c FROM posts WHERE project_id=:pid AND status IN ('draft','ready')",
                {"pid": current_project_id},
            )[0].get("c") or 0)
        except Exception:
            approval_count_global = 0
    data = {
        "request": request,
        "title": title,
        "current_page": page,
        "user": user,
        "nav": NAV,
        "project_nav": PROJECT_NAV,
        "project_options_global": project_list,
        "current_project": current_project,
        "connected_platforms_global": connected_platforms(uid, current_project_id) if user and current_project_id else [],
        "app_version": settings.app_version,
        "linda_image": LINDA_IMAGE,
        "flash": _flash(request),
        "csrf": csrf_token(request),
        "registration_mode": settings.registration_mode,
        "fresh_key_ready": fresh_key.configured(),
        "work_count": summary["queued"] + summary["running"],
        "unread_count": unread,
        "notification_preview": notification_rows(uid, 6) if user else [],
        "current_project_id": current_project_id,
        "actor_user": request.session.get("actor_user"),
        "developer_token": settings.developer_token,
        "providers": PROVIDERS,
        "provider_icons": PROVIDER_ICONS,
        "approval_count": approval_count_global,
    }
    data.update(extra)
    return data


def _render(request: Request, template: str, title: str, page: str, **extra: Any) -> HTMLResponse:
    html = templates.get_template(template).render(**_context(request, title, page, **extra))
    return HTMLResponse(html)


def _safe_upload_path(stored: str) -> Path | None:
    return resolve_upload_path(stored)


def _file_response(path: Path) -> FileResponse:
    mime = file_mime(path)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


def _public_media_signature(media_id: int) -> str:
    key = settings.media_signing_key or settings.cron_token or "fresh-ai-social-media"
    return hmac.new(key.encode("utf-8"), f"media:{media_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def _need_user(request: Request) -> dict[str, Any] | RedirectResponse:
    user = current_user(request)
    if not user:
        return _redirect("login")
    return user


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _safe_filename(name: str) -> str:
    base = Path(name or "upload.bin").name
    stem = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in base)
    return stem[:160] or "upload.bin"


async def _save_upload(upload: Any, subdir: str = "references", max_bytes: int = 15 * 1024 * 1024) -> str:
    if not upload or not getattr(upload, "filename", ""):
        raise RuntimeError("Nebyl vybrán soubor.")
    root = APP_ROOT / "storage" / "uploads" / subdir
    root.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}-{_safe_filename(upload.filename)}"
    target = root / name
    data = await upload.read()
    if len(data) > max_bytes:
        raise RuntimeError(f"Soubor je příliš velký (max. {max_bytes // (1024*1024)} MB).")
    content_type = str(getattr(upload, "content_type", "") or "")
    if content_type.startswith("image/") or Path(str(upload.filename)).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        validate_image_bytes(data, content_type)
        optimized, ext, _info = optimize_image_bytes(data, str(upload.filename))
        data = optimized
        validate_image_bytes(data, "image/jpeg" if ext in {"jpg","jpeg"} else f"image/{ext}")
        target = target.with_suffix("." + ext)
        target.write_bytes(data)
        return str(target.relative_to(APP_ROOT))

    target.write_bytes(data)
    if content_type.startswith("video/") or target.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}:
        optimized_path, info = optimize_video_path(target)
        if info.get("optimized") and optimized_path != target:
            try:
                target.unlink()
            except Exception:
                pass
            target = optimized_path
    return str(target.relative_to(APP_ROOT))



def _ensure_media_maintenance(user_id: int, project_id: int) -> None:
    if not project_id:
        return
    try:
        days = max(1, min(30, int(settings.media_maintenance_days)))
        recent = rows(
            f"SELECT id FROM task_queue WHERE user_id=:uid AND project_id=:pid AND type='media_maintenance' "
            f"AND updated_at >= DATE_SUB(NOW(), INTERVAL {days} DAY) ORDER BY id DESC LIMIT 1",
            {"uid": user_id, "pid": project_id},
        )
        if not recent:
            queue_job(
                user_id, project_id, "media_maintenance", "Linda uklízí média projektu", {},
                worker_group="maintenance", priority=5, notify_user=False,
                dedupe_key=f"media-maintenance:{project_id}",
            )
    except Exception:
        # Housekeeping must never block the interactive workspace.
        pass

def _queue_status_text(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    if status == "queued" and int(row.get("attempts") or 0) > 0:
        return "Opakování"
    return QUEUE_STATUS_LABELS.get(status, status or "—")


def _queue_action_url(row: dict[str, Any]) -> str:
    pid = int(row.get("project_id") or 0)
    try:
        payload = json.loads(str(row.get("payload") or "{}"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    post_id = int(payload.get("post_id") or 0)
    blog_id = int(payload.get("blog_id") or 0)
    asset_id = int(payload.get("asset_id") or 0)
    if post_id:
        return f"?page=content&project_id={pid}#post-{post_id}"
    if blog_id:
        return f"?page=content&project_id={pid}&view=articles#blog-{blog_id}"
    if asset_id:
        return f"?page=media&project_id={pid}"
    typ = str(row.get("type") or "")
    if typ in {"wordpress_sync","project_profile","brand_analyze","proactive_review"}:
        return f"?page=settings&project_id={pid}" if pid else "?page=settings"
    if typ == "analytics_sync":
        return f"?page=analytics&project_id={pid}" if pid else "?page=analytics"
    return "?page=queue"


def _queue_friendly_message(row: dict[str, Any]) -> dict[str, str]:
    raw = str(row.get("message") or "").strip()
    action = _queue_action_url(row)
    pid = int(row.get("project_id") or 0)
    friendly = raw
    title = str(row.get("title") or "Úloha Lindy")
    technical = ""
    try:
        payload = json.loads(str(row.get("result_json") or "{}"))
        if isinstance(payload, dict):
            friendly = str(payload.get("friendly_message") or friendly)
            action = str(payload.get("action_url") or action)
            technical = str(payload.get("technical_error") or "")
            title = str(payload.get("friendly_title") or title)
    except Exception:
        pass
    # Translate legacy/raw failures created before 0.4.6.5.
    if str(row.get("status") or "") == "failed" and (not friendly or friendly == raw):
        if "Engine nevrátil očekávaný JSON" in raw or "structured_output_invalid" in raw or "neplatný strukturovaný výstup" in raw:
            friendly = "AI Engine nedodal validní strukturovaná data. Linda původní obsah zachovala a úlohu už nebude automaticky opakovat dokola. Po opravě AI Engine klikni na Zkusit znovu."
            action = "?page=queue"
            technical = raw
        elif "AI Engine vrátil text místo obrázku" in raw or "text místo obrazového URL/base64" in raw:
            friendly = "AI Engine vrátil text místo skutečného obrázku. Obsah zůstal zachovaný; po opravě generování klikni na Zkusit znovu."
            technical = raw
        elif "AI Engine nevytvořil obrázek" in raw or "Fresh AI Engine nemá aktivní generování obrázků" in raw or "chybí obrazová data nebo URL" in raw:
            friendly = "AI Engine nedodal použitelný obrazový soubor. Text, termín ani ostatní data se nezměnily; úlohu můžeš bezpečně zopakovat."
            technical = raw
        elif "nepodařilo se vytvořit náhled" in raw:
            friendly = "Obrázek vznikl, ale Linda nedokončila náhled a proto ho neoznačila za hotový. Původní obsah zůstal zachovaný."
            technical = raw
        elif "input_data" in raw and "valid dictionary" in raw:
            friendly = "Jde o starší úlohu vytvořenou před opravou komunikace s AI Engine. Vytvoř ji znovu; nové úlohy už používají správný formát dat."
            action = f"?page=dashboard&project_id={pid}" if pid else "?page=dashboard"
            technical = raw
        elif "Znalostní báze je prázdná" in raw:
            friendly = "Linda zatím nezná web projektu. Nejdřív synchronizuj WordPress nebo načti znalostní bázi."
            action = f"?page=knowledge&project_id={pid}" if pid else "?page=projects"
            technical = raw
        elif "WordPress / Fresh Core konektor" in raw or "Pro projekt není uložen WordPress" in raw:
            friendly = "Projekt nemá funkční WordPress / Fresh Core připojení. Obsah zůstal uložený a publikaci můžeš zopakovat po opravě konektoru."
            action = f"?page=connectors&project_id={pid}&provider=wordpress" if pid else "?page=connectors"
            technical = raw
        elif "Brand kit je prázdný" in raw:
            friendly = "Brand Kit nemá dost podkladů. Nahraj vizuální vzor nebo doplň vizuální styl projektu."
            action = f"?page=references&project_id={pid}" if pid else "?page=projects"
            technical = raw
    return {"title": title, "message": friendly or "Úloha čeká na zpracování.", "action_url": action, "technical": technical}


def _active_post_jobs(user_id: int, project_id: int | None) -> dict[int, dict[str, Any]]:
    if not project_id:
        return {}
    jobs = rows(
        "SELECT id,status,type,title,message,payload,progress,updated_at FROM task_queue "
        "WHERE user_id=:uid AND project_id=:pid AND status IN ('queued','running') ORDER BY FIELD(status,'running','queued'),priority DESC,id DESC",
        {"uid": user_id, "pid": project_id},
    )
    labels = {
        "rewrite_post_text": "Linda upravuje text",
        "image_generate_post": "Linda připravuje obrázek",
        "carousel_slide": "Linda připravuje carousel",
        "image_generate_carousel_slide": "Linda připravuje carousel",
        "social_publish_post": "Linda publikuje na síť",
        "wp_publish_post": "Linda publikuje na web",
        "manual_post_generate": "Linda připravuje obsah",
    }
    out: dict[int, dict[str, Any]] = {}
    for job in jobs:
        try:
            payload = json.loads(str(job.get("payload") or "{}"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            continue
        post_id = int(payload.get("post_id") or 0)
        if not post_id or post_id in out:
            continue
        typ = str(job.get("type") or "")
        out[post_id] = {
            "id": int(job.get("id") or 0),
            "status": str(job.get("status") or "queued"),
            "type": typ,
            "label": labels.get(typ, "Linda pracuje na změně"),
            "message": str(job.get("message") or ""),
            "progress": int(job.get("progress") or 0),
            "updated_at": str(job.get("updated_at") or ""),
        }
    return out


def _decorate_post_jobs(items: list[dict[str, Any]], jobs: dict[int, dict[str, Any]]) -> None:
    for item in items:
        item["active_job"] = jobs.get(int(item.get("id") or 0))


def _default_publish_time(platform: str) -> tuple[int, int]:
    return {
        "facebook": (18, 30), "instagram": (19, 0), "linkedin": (9, 0),
        "google_business": (10, 0), "x": (12, 30), "tiktok": (19, 30), "blog": (8, 0),
    }.get(str(platform or ""), (10, 0))

def _reschedule_overdue_posts(user_id: int, project_id: int, start_date: datetime | None = None) -> int:
    start = (start_date or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    if start.date() <= datetime.now().date():
        start += timedelta(days=1)
    data = rows(
        "SELECT p.* FROM posts p JOIN projects pr ON pr.id=p.project_id "
        "WHERE (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid)) AND p.project_id=:pid AND p.published_at IS NULL "
        "AND p.scheduled_at IS NOT NULL AND p.scheduled_at<NOW() "
        "AND p.status IN ('scheduled','ready','draft') ORDER BY p.scheduled_at ASC,p.id ASC",
        {"uid": user_id, "pid": project_id},
    )
    platform_index: dict[str, int] = {}
    changed = 0
    with db() as conn:
        for item in data:
            platform = str(item.get("platform") or "facebook")
            index = platform_index.get(platform, 0)
            day = start + timedelta(days=index)
            hour, minute = _default_publish_time(platform)
            target = day.replace(hour=hour, minute=minute)
            conn.execute(text(
                "UPDATE posts SET status='scheduled',scheduled_at=:dt,publication_hold=NULL,"
                "publication_hold_at=NULL,publication_hold_message=NULL WHERE id=:id"
            ), {"dt": target, "id": int(item["id"])})
            platform_index[platform] = index + 1
            changed += 1
    return changed

def _proactive_brief(user_id: int, project_id: int) -> dict[str, Any]:
    if not project_id:
        return {"message": "Ahoj, jsem Linda. Vyber projekt a ráda tě provedu celým nastavením. Nejsi na to sám 🙂", "actions": []}
    connected = rows("SELECT provider FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND status IN ('connected','configured')", {"uid": user_id, "pid": project_id})
    knowledge = int((rows("SELECT COUNT(*) c FROM project_knowledge WHERE project_id=:pid AND status='active'", {"pid": project_id}) or [{"c":0}])[0].get("c") or 0)
    approvals = int((rows("SELECT COUNT(*) c FROM posts WHERE project_id=:pid AND status IN ('draft','ready')", {"pid": project_id}) or [{"c":0}])[0].get("c") or 0)
    overdue = int((rows("SELECT COUNT(*) c FROM posts WHERE project_id=:pid AND published_at IS NULL AND scheduled_at<NOW() AND status IN ('scheduled','ready','draft')", {"pid": project_id}) or [{"c":0}])[0].get("c") or 0)
    due_today = int((rows("SELECT COUNT(*) c FROM posts WHERE project_id=:pid AND DATE(scheduled_at)=CURDATE() AND status='scheduled'", {"pid": project_id}) or [{"c":0}])[0].get("c") or 0)
    parts = ["Ahoj, jsem Linda a ráda tě provedu celým procesem nastavení i plánování. Nejsi na to sám 🙂"]
    actions = []
    if not connected:
        parts.append("Nejdřív bych připojila publikační kanály, ať můžu plán nejen navrhovat, ale i skutečně provádět.")
        actions.append({"label":"Připojit kanály","href":f"?page=connectors&project_id={project_id}"})
    if knowledge == 0:
        parts.append("Zatím nemám načtený web. Bez znalostí webu nedokážu bezpečně vybírat produkty, články ani cílové odkazy.")
        actions.append({"label":"Načíst web","href":f"?page=knowledge&project_id={project_id}"})
    if approvals:
        parts.append(f"Ke kontrole čeká {approvals} výstupů.")
        actions.append({"label":f"Schválit ({approvals})","href":f"?page=content&project_id={project_id}&view=approval"})
    if overdue:
        parts.append(f"V kalendáři je {overdue} starších nepublikovaných položek. Umím je hromadně přesunout na nové termíny.")
        actions.append({"label":f"Přeplánovat ({overdue})","action":"reschedule_overdue"})
    if due_today:
        parts.append(f"Na dnešek je naplánováno {due_today} výstupů.")
    if len(parts) == 1:
        parts.append("Projekt je připravený. Napiš mi obchodní cíl nebo co chceš dnes zlepšit a navrhnu další krok.")
    return {"message": " ".join(parts), "actions": actions[:4]}

def _admin_queue_rows(
    limit: int = 300,
    *,
    status: str = "",
    user_id: int = 0,
    project_id: int = 0,
    job_type: str = "",
) -> list[dict[str, Any]]:
    where=[]; params: dict[str, Any] = {"limit": max(20, min(500, limit))}
    if status in {"queued","running","failed","done"}:
        where.append("q.status=:status"); params["status"]=status
    if user_id:
        where.append("q.user_id=:filter_uid"); params["filter_uid"]=user_id
    if project_id:
        where.append("q.project_id=:filter_pid"); params["filter_pid"]=project_id
    if job_type:
        where.append("q.type=:job_type"); params["job_type"]=job_type
    sql=(
        "SELECT q.*,u.name user_name,u.email user_email,p.name project_name FROM task_queue q "
        "LEFT JOIN users u ON u.id=q.user_id LEFT JOIN projects p ON p.id=q.project_id "
        + (("WHERE " + " AND ".join(where) + " ") if where else "")
        + "ORDER BY FIELD(q.status,'running','queued','failed','done'),q.priority DESC,q.id DESC LIMIT :limit"
    )
    items = rows(sql, params)
    for item in items:
        item["friendly"] = _queue_friendly_message(item)
        item["status_label"] = _queue_status_text(item)
        item["action_url"] = _queue_action_url(item)
    return items


def _meta_ready() -> bool:
    return bool(settings.meta_app_id and settings.meta_app_secret and "PASTE_" not in settings.meta_app_id and "PASTE_" not in settings.meta_app_secret)


def _meta_redirect_url() -> str:
    return settings.app_url.rstrip("/") + "/?page=meta_oauth_callback"


def _linkedin_ready() -> bool:
    return bool(settings.linkedin_client_id and settings.linkedin_client_secret)


def _linkedin_redirect_url() -> str:
    # LinkedIn trusted redirect URLs are registered as absolute paths; query
    # parameters are not a reliable callback identity in the Developer Portal.
    return settings.app_url.rstrip("/") + "/oauth/linkedin/callback"


def _google_business_ready() -> bool:
    return bool(settings.google_business_client_id and settings.google_business_client_secret)


def _google_business_redirect_url() -> str:
    return settings.app_url.rstrip("/") + "/?page=google_business_oauth_callback"


def _finish_linkedin_oauth(request: Request, uid: int):
    saved = request.session.get("linkedin_oauth") or {}
    state = str(request.query_params.get("state") or "")
    code = str(request.query_params.get("code") or "")
    error = str(request.query_params.get("error") or "")
    error_description = str(request.query_params.get("error_description") or "")
    pid = int(saved.get("project_id") or 0)
    if error:
        request.session.pop("linkedin_oauth", None)
        _flash(request, f"LinkedIn přihlášení bylo zrušeno nebo odmítnuto: {error_description or error}")
        return _redirect("connectors", f"&project_id={pid}&provider=linkedin" if pid else "")
    if not code or not state or state != saved.get("state") or not project(uid, pid):
        _flash(request, "LinkedIn přihlášení nebylo možné ověřit.")
        return _redirect("connectors", f"&project_id={pid}&provider=linkedin" if pid else "")
    try:
        with httpx.Client(timeout=35, follow_redirects=True) as client:
            token_res = client.post("https://www.linkedin.com/oauth/v2/accessToken", data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _linkedin_redirect_url(),
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
            }, headers={"Accept": "application/json"})
            token_res.raise_for_status()
            token_data = token_res.json()
            access_token = str(token_data.get("access_token") or "")
            refresh_token = str(token_data.get("refresh_token") or "")
            expires_in = int(token_data.get("expires_in") or 0)
            if not access_token:
                raise RuntimeError("LinkedIn nevrátil access token.")
            profile_res = client.get("https://api.linkedin.com/v2/userinfo", headers={
                "Authorization": f"Bearer {access_token}", "Accept": "application/json"
            })
            profile_res.raise_for_status()
            profile = profile_res.json()
        subject = str(profile.get("sub") or "").strip()
        if not subject:
            raise RuntimeError("LinkedIn nevrátil identifikátor přihlášeného profilu.")
        author_urn = subject if subject.startswith("urn:li:") else f"urn:li:person:{subject}"
        display_name = str(profile.get("name") or "").strip() or " ".join(
            x for x in [str(profile.get("given_name") or "").strip(), str(profile.get("family_name") or "").strip()] if x
        ).strip() or str(profile.get("email") or "").strip() or "LinkedIn profil"
        existing = next((x for x in list_connectors(uid, pid) if x.get("provider") == "linkedin"), None)
        existing_refresh = str((existing or {}).get("refresh_token") or "")
        cid = create_or_update_connector(uid, {
            "project_id": pid,
            "provider": "linkedin",
            "account_name": display_name,
            "client_id": settings.linkedin_client_id,
            "client_secret": None,
            "access_token": access_token,
            "refresh_token": refresh_token or existing_refresh or None,
            "expires_at": (datetime.now() + timedelta(seconds=max(0, expires_in - 60))) if expires_in else None,
            "endpoint_url": None,
            "auth_user": author_urn,
            "notes": json.dumps({"source": "linkedin_oauth", "mode": "member"}, ensure_ascii=False),
        }, int(existing["id"]) if existing else None)
        result = test_connector(connector(uid, cid) or {})
        update_connector_test(cid, uid, bool(result.get("ok")), str(result.get("message") or ""), result.get("capabilities"))
        request.session.pop("linkedin_oauth", None)
        _flash(request, "LinkedIn profil byl připojen." if result.get("ok") else str(result.get("message") or "LinkedIn je uložený, ale test neprošel."))
        return _redirect("connectors", f"&project_id={pid}&provider=linkedin")
    except Exception as exc:
        request.session.pop("linkedin_oauth", None)
        _flash(request, f"LinkedIn přihlášení selhalo: {exc}")
        return _redirect("connectors", f"&project_id={pid}&provider=linkedin" if pid else "")


@app.get("/healthz")
def healthz() -> JSONResponse:
    database = db_ping()
    schema = schema_report()
    media_ready = pillow_available()
    engine_configured = bool(settings.engine_api_key)
    return JSONResponse({
        "app": "Linda",
        "version": settings.app_version,
        "ok": bool(database.get("ok") and schema.get("ok") and media_ready and engine_configured),
        "database": bool(database.get("ok")),
        "schema": bool(schema.get("ok")),
        "engine_configured": engine_configured,
        "media_ready": media_ready,
        "pillow": media_ready,
        "error": (None if database.get("ok") else database.get("error")) or (None if media_ready else "Pillow není dostupný v aktivním Python prostředí."),
    })


@app.get("/health")
def health() -> JSONResponse:
    return healthz()


@app.get("/oauth/linkedin/callback")
def linkedin_oauth_callback_route(request: Request):
    user = current_user(request)
    if not user:
        _flash(request, "Nejdřív se přihlas do Lindy a zopakuj připojení LinkedIn.")
        return _redirect("login")
    return _finish_linkedin_oauth(request, int(user["id"]))


@app.get("/developer/{token}/manifest.json")
def developer_manifest(token: str) -> JSONResponse:
    if not safe_developer_token(token, settings.developer_token):
        raise HTTPException(status_code=404, detail="Not found")
    return JSONResponse(build_manifest())


@app.get("/public-media/{media_id}/{sig}/{filename}")
async def public_media_file(media_id: int, sig: str, filename: str):
    expected = _public_media_signature(media_id) if media_id else ""
    if not media_id or not sig or not hmac.compare_digest(sig, expected):
        return HTMLResponse("Neplatný odkaz na médium.", status_code=403)
    result = rows("SELECT file_path FROM media_assets WHERE id=:id AND file_path IS NOT NULL LIMIT 1", {"id": media_id})
    target = _safe_upload_path(str(result[0].get("file_path") or "")) if result else None
    if not target:
        return HTMLResponse("Soubor nenalezen.", status_code=404)
    response = _file_response(target)
    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["Content-Disposition"] = f'inline; filename="{Path(filename).name}"'
    return response


@app.api_route("/", methods=["GET", "POST"], response_class=HTMLResponse)
async def root(request: Request):
    page = request.query_params.get("page") or ("dashboard" if current_user(request) else "login")

    if page == "logout":
        request.session.clear()
        return _redirect("login")

    if page == "public_media":
        media_id = int(request.query_params.get("id") or 0)
        supplied = str(request.query_params.get("sig") or "")
        expected = _public_media_signature(media_id) if media_id else ""
        if not media_id or not supplied or not hmac.compare_digest(supplied, expected):
            return HTMLResponse("Neplatný odkaz na médium.", status_code=403)
        row = rows("SELECT file_path FROM media_assets WHERE id=:id AND file_path IS NOT NULL LIMIT 1", {"id": media_id})
        target = _safe_upload_path(str(row[0].get("file_path") or "")) if row else None
        if not target:
            return HTMLResponse("Soubor nenalezen.", status_code=404)
        if str(request.query_params.get("variant") or "") == "thumb":
            thumb = thumbnail_path(media_id, target)
            if thumb:
                target = thumb
        response = _file_response(target)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    if page == "cron_worker":
        supplied = request.query_params.get("token", "")
        if not settings.cron_token or supplied != settings.cron_token:
            return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
        worker = str(request.query_params.get("worker") or "")
        # One Linda batch at a time. This prevents overlapping cPanel cron calls
        # from multiplying Passenger/Python/AI/media load on CloudLinux.
        lock_name = "linda:cron_worker"
        with db() as lock_conn:
            acquired = int(lock_conn.execute(text("SELECT GET_LOCK(:name,0)"), {"name": lock_name}).scalar() or 0)
            if acquired != 1:
                return JSONResponse({"ok": True, "busy": True, "worker": worker or "all", "message": "Předchozí běh Lindy ještě pracuje."})
            try:
                try:
                    lock_conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('linda_cron_last_run',NOW()) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"))
                    lock_conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('linda_cron_last_worker',:worker) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"worker": worker or "all"})
                except Exception:
                    pass
                prep_errors=[]
                try: cleanup_legacy_chat_jobs()
                except Exception as exc: prep_errors.append({"step":"legacy_chat_cleanup","error":str(exc)[:500]})
                try: recovered = recover_stale_jobs(20)
                except Exception as exc: recovered=0; prep_errors.append({"step":"recover_stale","error":str(exc)[:500]})
                try: proactive = enqueue_due_planned_tasks()
                except Exception as exc: proactive={"count":0,"items":[]}; prep_errors.append({"step":"planned_tasks","error":str(exc)[:500]})
                try: scheduled = schedule_due_publications()
                except Exception as exc: scheduled=[]; prep_errors.append({"step":"schedule_publications","error":str(exc)[:500]})
                try: refresh_action_items()
                except Exception as exc: prep_errors.append({"step":"refresh_actions","error":str(exc)[:500]})
                try: reminders=remind_unresolved_failures(limit=8)
                except Exception as exc: reminders=0; prep_errors.append({"step":"failure_reminders","error":str(exc)[:500]})
                if worker in {"text", "media", "publication", "maintenance"}:
                    try: result = process_available_jobs(limit=12, worker_group=worker)
                    except Exception as exc: result={"ok":False,"claimed":0,"done":0,"failed":0,"details":[],"worker_error":str(exc)[:1000]}
                else:
                    groups = []
                    for grp, limit in (("publication", 5), ("text", 5), ("media", 4), ("maintenance", 3)):
                        try: group_result=process_available_jobs(limit=limit, worker_group=grp)
                        except Exception as exc: group_result={"ok":False,"claimed":0,"done":0,"failed":0,"details":[],"worker_error":str(exc)[:1000]}
                        groups.append({"group": grp, **group_result})
                    result = {"ok": True, "completed_with_errors": any(not x.get("ok") for x in groups), "groups": groups, "claimed": sum(int(x.get("claimed") or 0) for x in groups), "done": sum(int(x.get("done") or 0) for x in groups), "failed": sum(int(x.get("failed") or 0) for x in groups), "prep_errors":prep_errors, "failure_reminders":reminders}
                try:
                    lock_conn.execute(text("INSERT INTO app_meta(meta_key,meta_value) VALUES('linda_cron_last_result',:value) ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"), {"value": json.dumps({"ok": bool(result.get("ok")), "worker": worker or "all", "recovered_stale": recovered, "planned": proactive, "scheduled": scheduled, "done": result.get("done"), "failed": result.get("failed"), "claimed": result.get("claimed")}, ensure_ascii=False, default=str)})
                except Exception:
                    pass
                return JSONResponse({"ok": bool(result.get("ok")), "worker": worker or "all", "planned_tasks_enqueued": proactive, "scheduled_publications": scheduled, "processed": result})
            finally:
                try:
                    lock_conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
                except Exception:
                    pass

    if page == "api_status37":
        user = current_user(request)
        if not user:
            return JSONResponse({"ok": False}, status_code=401)
        uid_status=int(user["id"])
        summary = queue_summary(uid_status)
        active = rows("SELECT progress FROM task_queue WHERE user_id=:uid AND status='running' ORDER BY id DESC LIMIT 1", {"uid":uid_status})
        unread = rows("SELECT COUNT(*) c FROM notifications WHERE user_id=:uid AND is_read=0", {"uid":uid_status})
        work_count = summary["queued"] + summary["running"]
        progress = int(active[0].get("progress") or 0) if active else 0
        return JSONResponse({
            "ok": True,
            "working": work_count,
            "queued": summary["queued"],
            "running": summary["running"],
            "failed": summary["failed"],
            "progress": progress,
            "work": {"count": work_count, "progress": progress},
            "unread": int(unread[0].get("c") or 0) if unread else 0,
        })

    if page == "api_notifications":
        user = current_user(request)
        if not user:
            return JSONResponse({"ok": False}, status_code=401)
        items = notification_rows(int(user["id"]), 8)
        return JSONResponse({"ok": True, "items": [
            {"id": int(x.get("id") or 0), "title": str(x.get("title") or "Linda"), "message": str(x.get("message") or ""), "level": str(x.get("level") or "info"), "action_url": str(x.get("action_url") or "?page=notifications"), "is_read": int(x.get("is_read") or 0), "created_at": str(x.get("created_at") or "")}
            for x in items
        ]})


    if page == "api_queue_status":
        user = current_user(request)
        if not user:
            return JSONResponse({"ok": False}, status_code=401)
        uid_q = int(user["id"])
        # Queue diagnostics is also a safe repair point for abandoned legacy rows.
        cleanup_legacy_chat_jobs(uid_q)
        recover_stale_jobs(20, uid_q)
        items = queue_rows(uid_q, 120)
        meta = {str(x.get("meta_key")): str(x.get("meta_value") or "") for x in rows("SELECT meta_key,meta_value FROM app_meta WHERE meta_key IN ('linda_cron_last_run','linda_cron_last_worker','linda_cron_last_result')")}
        api_items = []
        for x in items:
            friendly = _queue_friendly_message(x)
            try:
                payload = json.loads(str(x.get("payload") or "{}"))
            except Exception:
                payload = {}
            api_items.append({
                "id": int(x.get("id") or 0), "status": str(x.get("status") or ""), "status_label": _queue_status_text(x), "progress": int(x.get("progress") or 0),
                "title": str(x.get("title") or ""), "message": str(friendly.get("message") or x.get("message") or ""), "type": str(x.get("type") or ""), "type_label": QUEUE_TYPE_LABELS.get(str(x.get("type") or ""), str(x.get("type") or "")),
                "worker_group": str(x.get("worker_group") or ""), "worker_group_label": QUEUE_GROUP_LABELS.get(str(x.get("worker_group") or ""), str(x.get("worker_group") or "")), "project_name": str(x.get("project_name") or ""),
                "attempts": int(x.get("attempts") or 0), "max_attempts": int(x.get("max_attempts") or 3),
                "post_id": int(payload.get("post_id") or 0) if isinstance(payload, dict) else 0,
                "blog_id": int(payload.get("blog_id") or 0) if isinstance(payload, dict) else 0,
                "asset_id": int(payload.get("asset_id") or 0) if isinstance(payload, dict) else 0,
                "created_at": str(x.get("created_at") or ""), "started_at": str(x.get("locked_at") or ""), "updated_at": str(x.get("updated_at") or ""), "locked_at": str(x.get("locked_at") or ""),
                "available_at": str(x.get("available_at") or ""), "finished_at": str(x.get("finished_at") or ""),
                "action_url": str(friendly.get("action_url") or _queue_action_url(x)),
                "technical": str(friendly.get("technical") or "") if str(user.get("role") or "") == "superadmin" else "",
            })
        return JSONResponse({"ok": True, "summary": queue_summary(uid_q), "cron": meta, "items": api_items})

    if page == "api_calendar_move":
        user = current_user(request)
        if not user or request.method != "POST":
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        form = await request.form()
        try:
            verify_csrf(request, str(form.get("csrf") or ""))
        except Exception:
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        uid_move = int(user["id"]); post_id = int(form.get("post_id") or 0)
        p = post_row(uid_move, post_id) if post_id else None
        if not p:
            return JSONResponse({"ok": False, "error": "post_not_found"}, status_code=404)
        target_date = str(form.get("date") or "").strip(); target_platform = str(form.get("platform") or p.get("platform") or "facebook").strip()
        try:
            base = datetime.fromisoformat(target_date)
            old_dt = p.get("scheduled_at")
            if old_dt:
                target = base.replace(hour=old_dt.hour, minute=old_dt.minute)
            else:
                hh, mm = _default_publish_time(target_platform); target = base.replace(hour=hh, minute=mm)
            update_post(uid_move, post_id, {"platform": target_platform, "status": "scheduled", "scheduled_at": target})
            return JSONResponse({"ok": True, "post_id": post_id, "scheduled_at": target.isoformat(timespec="minutes"), "platform": target_platform})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)

    if page == "api_calendar_create":
        user = current_user(request)
        if not user or request.method != "POST":
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        form = await request.form()
        try:
            verify_csrf(request, str(form.get("csrf") or ""))
        except Exception:
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        uid_new=int(user["id"]); pid=int(form.get("project_id") or 0); kind=str(form.get("content_kind") or "post").strip()
        if not project(uid_new,pid):
            return JSONResponse({"ok":False,"error":"project_not_found"},status_code=404)
        topic=str(form.get("topic") or "").strip()
        if not topic:
            return JSONResponse({"ok":False,"error":"Doplň téma nebo zadání."},status_code=400)
        platform="blog" if kind=="article" else str(form.get("platform") or "facebook").strip()
        post_type="blog_promo" if kind=="article" else str(form.get("post_type") or "single_post").strip()
        date_s=str(form.get("date") or datetime.now().strftime("%Y-%m-%d")); time_s=str(form.get("time") or "").strip()
        try:
            base=datetime.fromisoformat(date_s)
            if time_s and re.match(r"^\d{2}:\d{2}$",time_s):
                hh,mm=[int(x) for x in time_s.split(":",1)]
            else:
                hh,mm=_default_publish_time(platform)
            scheduled=base.replace(hour=hh,minute=mm,second=0,microsecond=0)
        except Exception:
            scheduled=datetime.now().replace(second=0,microsecond=0)
        if kind=="article":
            jid=queue_job(uid_new,pid,"article_generate",f"Článek: {topic}",{"title":topic,"brief":topic,"angle":str(form.get("instruction") or "").strip(),"manual_calendar":True,"scheduled_at":scheduled.isoformat(timespec="minutes")},worker_group="text",priority=85,notify_user=True,dedupe_key=f"manual-article:{pid}:{hashlib.sha1((topic+scheduled.isoformat()).encode()).hexdigest()[:16]}")
            return JSONResponse({"ok":True,"message":f"Linda připraví článek do kalendáře. Úloha #{jid}.","job_id":jid})
        with db() as conn:
            ins=conn.execute(text("INSERT INTO posts(project_id,platform,post_type,title,body,image_prompt,hashtags,recommended_time,status,scheduled_at,destination_status) VALUES(:pid,:platform,:ptype,:title,'','','',:recommended,'draft',:scheduled,NULL)"),{"pid":pid,"platform":platform,"ptype":post_type,"title":topic[:190],"recommended":f"{scheduled.hour:02d}:{scheduled.minute:02d}","scheduled":scheduled})
            post_id=int(ins.lastrowid)
        jid=queue_job(uid_new,pid,"manual_post_generate",f"Linda připravuje příspěvek: {topic}",{"post_id":post_id,"topic":topic},worker_group="text",priority=85,notify_user=True,dedupe_key=f"manual-post:{post_id}")
        return JSONResponse({"ok":True,"message":f"Příspěvek je v kalendáři a Linda připravuje text. Úloha #{jid}.","job_id":jid,"post_id":post_id})

    if page == "api_post_detail":
        user = current_user(request)
        if not user:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        uid_post = int(user["id"]); post_id = int(request.query_params.get("post_id") or 0)
        p = post_row(uid_post, post_id) if post_id else None
        if not p:
            return JSONResponse({"ok": False, "error": "post_not_found"}, status_code=404)
        current = rows("SELECT id,title,kind,provider,status,file_path FROM media_assets WHERE post_id=:id AND file_path IS NOT NULL AND file_path<>'' ORDER BY id DESC LIMIT 1", {"id": post_id})
        library = rows("SELECT id,title,kind,provider,status,file_path FROM media_assets WHERE project_id=:pid AND file_path IS NOT NULL AND file_path<>'' ORDER BY id DESC LIMIT 18", {"pid": int(p.get("project_id") or 0)})
        def media_json(x):
            mid = int(x.get("id") or 0)
            return {"id": mid, "title": str(x.get("title") or "Médium"), "kind": str(x.get("kind") or ""), "provider": str(x.get("provider") or ""), "status": str(x.get("status") or ""), "url": f"/?page=file&type=media&id={mid}", "thumb_url": f"/?page=file&type=media&id={mid}&variant=thumb"}
        cats = wordpress_categories(uid_post, int(p.get("project_id") or 0)) if str(p.get("platform") or "") == "blog" else []
        suggested = suggest_wordpress_category(int(p.get("project_id") or 0), cats, str(p.get("title") or ""), str(p.get("body") or "")) if cats else None
        return JSONResponse({"ok": True, "post": {
            "id": post_id, "project_id": int(p.get("project_id") or 0), "title": str(p.get("title") or ""), "platform": str(p.get("platform") or "facebook"),
            "post_type": str(p.get("post_type") or "single_post"), "body": str(p.get("body") or ""), "hashtags": str(p.get("hashtags") or ""),
            "image_prompt": str(p.get("image_prompt") or ""), "scheduled_at": p.get("scheduled_at").strftime("%Y-%m-%dT%H:%M") if p.get("scheduled_at") else "",
            "status": str(p.get("status") or "draft"), "destination_url": str(p.get("destination_url") or ""), "notes": str(p.get("notes") or ""),
            "destination_status": str(p.get("destination_status") or ""), "destination_checked_at": str(p.get("destination_checked_at") or ""),
            "destination_type": str(p.get("destination_type") or ""), "destination_ref_id": int(p.get("destination_ref_id") or 0),
            "permalink": str(p.get("permalink") or ""), "external_id": str(p.get("external_id") or ""),
            "publication_hold": bool(p.get("publication_hold")), "publication_hold_message": str(p.get("publication_hold_message") or "")
        }, "current_media": media_json(current[0]) if current else None, "media_library": [media_json(x) for x in library],
        "wp_categories": cats, "suggested_category": suggested})

    if page == "api_post_action":
        user = current_user(request)
        if not user or request.method != "POST":
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        form = await request.form()
        try:
            verify_csrf(request, str(form.get("csrf") or ""))
        except Exception:
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        uid_post = int(user["id"]); post_id = int(form.get("post_id") or 0); action = str(form.get("action") or "save")
        p = post_row(uid_post, post_id) if post_id else None
        if not p:
            return JSONResponse({"ok": False, "error": "post_not_found"}, status_code=404)
        project_id = int(p.get("project_id") or 0)
        try:
            if action == "save":
                values = {"title": str(form.get("title") or "").strip(), "platform": str(form.get("platform") or p.get("platform") or "facebook"), "post_type": str(form.get("post_type") or p.get("post_type") or "single_post"), "body": str(form.get("body") or ""), "image_prompt": str(form.get("image_prompt") or ""), "hashtags": str(form.get("hashtags") or ""), "status": str(form.get("status") or p.get("status") or "draft"), "scheduled_at": _parse_dt(form.get("scheduled_at")), "destination_url": str(form.get("destination_url") or "").strip() or None, "notes": str(form.get("notes") or "").strip() or None}
                update_post(uid_post, post_id, values)
                return JSONResponse({"ok": True, "message": "Příspěvek uložen."})
            if action == "generate_image":
                jid = queue_job(uid_post, project_id, "image_generate_post", f"Obrázek: {p.get('title')}", {"post_id": post_id}, worker_group="media", priority=60, notify_user=True, dedupe_key=f"image:post:{post_id}")
                return JSONResponse({"ok": True, "message": f"Generování obrázku je ve frontě #{jid}.", "job_id": jid})
            if action == "regenerate_text":
                instruction = str(form.get("ai_instruction") or "").strip()[:1500]
                jid = queue_job(uid_post, project_id, "rewrite_post_text", f"Linda upravuje text: {p.get('title')}", {"post_id": post_id, "instruction": instruction}, worker_group="text", priority=85, notify_user=True, dedupe_key=f"rewrite:post:{post_id}")
                return JSONResponse({"ok": True, "message": f"Linda přegeneruje text ve frontě #{jid}. Obrázek zůstane zachovaný.", "job_id": jid})
            if action == "verify_link":
                fresh_post = post_row(uid_post, post_id) or p
                if str(fresh_post.get("platform") or "") == "blog":
                    if fresh_post.get("permalink"):
                        return JSONResponse({"ok": True, "message": "WordPress článek už má veřejnou URL.", "url": str(fresh_post.get("permalink") or ""), "status": "ok"})
                    # Before publication the web URL is only a preview/planned URL.
                    try:
                        wp_rows = rows("SELECT * FROM social_connectors WHERE user_id=:uid AND project_id=:pid AND provider='wordpress' ORDER BY id DESC LIMIT 1", {"uid": uid_post, "pid": project_id})
                        wp = wp_rows[0] if wp_rows else None
                        if wp:
                            slug = re.sub(r"[^a-z0-9]+", "-", str(fresh_post.get("title") or "").lower()).strip("-")[:180]
                            preview = from_connector(wp).preview_url(slug, "post")
                            payload = preview.data.get("data") if preview.ok and isinstance(preview.data, dict) and isinstance(preview.data.get("data"), dict) else (preview.data if preview.ok and isinstance(preview.data, dict) else {})
                            url = str(payload.get("url") or "")
                            if url:
                                update_post(uid_post, post_id, {"destination_url": url})
                                return JSONResponse({"ok": True, "message": "Fresh Core připravil budoucí URL článku. Veřejná bude po publikaci.", "url": url, "status": "planned"})
                    except Exception:
                        pass
                    return JSONResponse({"ok": True, "message": "Webový článek zatím nemá veřejnou URL. Doplním ji při publikaci.", "url": "", "status": "planned"})
                if str(fresh_post.get("destination_type") or "") == "blog" and fresh_post.get("destination_ref_id"):
                    checked = check_destination(fresh_post, uid_post, True)
                    return JSONResponse({"ok": True, "message": checked.get("message") or "Navázaný článek zatím čeká na publikaci.", "url": checked.get("url") or str(fresh_post.get("destination_url") or ""), "status": "ok" if checked.get("ok") else "planned"})
                checked = check_destination(fresh_post, uid_post, True) if fresh_post.get("destination_url") else {"ok": False}
                if not checked.get("ok"):
                    try:
                        new_url = regenerate_post_link(uid_post, post_id)
                        fresh_post = post_row(uid_post, post_id) or fresh_post
                        checked = check_destination(fresh_post, uid_post, True)
                        if checked.get("ok"):
                            return JSONResponse({"ok": True, "message": "Linda našla a ověřila vhodný cílový odkaz.", "url": checked.get("url") or new_url, "status": "ok"})
                    except Exception as exc:
                        return JSONResponse({"ok": False, "error": f"Vhodný funkční odkaz jsem nenašla: {exc}"}, status_code=400)
                return JSONResponse({"ok": bool(checked.get("ok")), "message": checked.get("message") or "Odkaz ověřen.", "url": checked.get("url") or "", "status": "ok" if checked.get("ok") else "error"}, status_code=200 if checked.get("ok") else 400)
            if action == "publish":
                update_post(uid_post, post_id, {"status": "scheduled", "scheduled_at": datetime.now(), "publication_hold": None, "publication_hold_at": None, "publication_hold_message": None})
                if str(p.get("platform") or "") == "blog":
                    cat_id = int(form.get("wp_category_id") or 0) or None
                    jid = queue_job(uid_post, project_id, "wp_publish_post", f"WordPress: {p.get('title')}", {"post_id": post_id, "force_publish": True, "wp_category_id": cat_id}, worker_group="publication", priority=100, notify_user=True, dedupe_key=f"wp-publish:post:{post_id}")
                else:
                    jid = queue_job(uid_post, project_id, "social_publish_post", f"Publikace: {p.get('title')}", {"post_id": post_id}, worker_group="publication", priority=100, notify_user=True, dedupe_key=f"publish:post:{post_id}")
                return JSONResponse({"ok": True, "message": f"Publikace je ve frontě #{jid}.", "job_id": jid})
            if action == "duplicate":
                nid = duplicate_post(uid_post, post_id)
                return JSONResponse({"ok": True, "message": f"Vytvořena kopie #{nid}.", "post_id": nid})
            if action == "delete":
                delete_post(uid_post, post_id)
                return JSONResponse({"ok": True, "message": "Příspěvek smazán.", "deleted": True})
            if action == "assign_media":
                media_id = int(form.get("media_id") or 0)
                src = rows("SELECT * FROM media_assets WHERE id=:id AND project_id=:pid AND file_path IS NOT NULL AND file_path<>'' LIMIT 1", {"id": media_id, "pid": project_id})
                if not src:
                    return JSONResponse({"ok": False, "error": "media_not_found"}, status_code=404)
                m = src[0]
                with db() as conn:
                    ins = conn.execute(text("INSERT INTO media_assets(project_id,post_id,prompt,file_path,provider,status,kind,title) VALUES(:pid,:post,:prompt,:path,:provider,:status,:kind,:title)"), {"pid": project_id, "post": post_id, "prompt": str(m.get("prompt") or "Vybráno z knihovny"), "path": str(m.get("file_path") or ""), "provider": str(m.get("provider") or "library"), "status": str(m.get("status") or "generated"), "kind": str(m.get("kind") or "post_image"), "title": str(m.get("title") or p.get("title") or "Médium")[:190]})
                    new_id = int(ins.lastrowid)
                return JSONResponse({"ok": True, "message": "Médium přiřazeno k příspěvku.", "media_id": new_id, "thumb_url": f"/?page=file&type=media&id={new_id}&variant=thumb"})
            if action == "upload_media":
                upload = form.get("file")
                path = await _save_upload(upload, "user-media", 120*1024*1024)
                suffix = Path(path).suffix.lower(); kind = "user_video" if suffix in {".mp4",".mov",".m4v",".webm",".avi",".mkv"} else "user_image"
                title = str(form.get("media_title") or getattr(upload, "filename", "") or p.get("title") or "Nahrané médium").strip()
                with db() as conn:
                    ins = conn.execute(text("INSERT INTO media_assets(project_id,post_id,prompt,file_path,status,kind,title,provider) VALUES(:pid,:post,'Uživatelský upload',:path,'generated',:kind,:title,'upload')"), {"pid": project_id, "post": post_id, "path": path, "kind": kind, "title": title[:190]})
                    media_id = int(ins.lastrowid)
                media_path = resolve_upload_path(path, allow_scan=False)
                if kind == "user_image" and media_path:
                    thumbnail_path(media_id, media_path)
                return JSONResponse({"ok": True, "message": "Médium nahráno, optimalizováno a přiřazeno.", "media_id": media_id, "thumb_url": f"/?page=file&type=media&id={media_id}&variant=thumb"})
            return JSONResponse({"ok": False, "error": "unsupported_action"}, status_code=400)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:1200]}, status_code=400)

    if page == "api_chat_action":
        user = current_user(request)
        if not user or request.method != "POST":
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        form = await request.form()
        try:
            verify_csrf(request, str(form.get("csrf") or ""))
        except Exception:
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        uid_action = int(user["id"]); pid = int(form.get("project_id") or 0); action = str(form.get("chat_action") or "")
        if not project(uid_action, pid):
            return JSONResponse({"ok": False, "error": "project_not_found"}, status_code=404)
        try:
            if action == "reschedule_overdue":
                count = _reschedule_overdue_posts(uid_action, pid)
                return JSONResponse({"ok": True, "message": f"Přeplánovala jsem {count} starších položek do nejbližších volných dní.", "href": f"?page=calendar&project_id={pid}"})
            if action == "sync_web":
                wp = next((x for x in list_connectors(uid_action, pid) if x.get("provider") == "wordpress"), None)
                if not wp:
                    return JSONResponse({"ok": False, "error": "Nejdřív připoj WordPress / Fresh Core."}, status_code=400)
                jid = queue_job(uid_action,pid,"wordpress_sync","Synchronizuji znalosti z webu",{"manual":True,"connector_id":int(wp["id"])},worker_group="maintenance",priority=50,notify_user=True,dedupe_key=f"wordpress_sync:{pid}")
                return JSONResponse({"ok": True, "message": f"Synchronizaci webu jsem přidala do fronty #{jid}.", "href": f"?page=queue"})
            if action == "refresh_profile":
                jid = queue_job(uid_action,pid,"project_profile","Linda aktualizuje profil projektu",{"manual":True},worker_group="text",priority=90,notify_user=True,dedupe_key=f"project_profile:{pid}")
                return JSONResponse({"ok": True, "message": f"Aktualizaci AI profilu jsem přidala do fronty #{jid}."})
            if action == "analyze_brand":
                jid = queue_job(uid_action,pid,"brand_analyze","Linda analyzuje Brand Kit",{"manual":True},worker_group="media",priority=80,notify_user=True,dedupe_key=f"brand_analyze:{pid}")
                return JSONResponse({"ok": True, "message": f"Brand Kit analyzuji ve frontě #{jid}.", "href": f"?page=references&project_id={pid}"})
            if action == "project_check":
                jid = queue_job(uid_action,pid,"proactive_review","Linda kontroluje stav projektu",{"manual":True},worker_group="maintenance",priority=60,notify_user=True,dedupe_key=f"proactive_review:{pid}:{datetime.now().strftime('%Y%m%d%H')}")
                return JSONResponse({"ok": True, "message": f"Kontrolu projektu jsem přidala do fronty #{jid}.", "href": "?page=queue"})
            if action == "plan_14_days":
                jid = queue_job(uid_action,pid,"ai_activity","Linda připravuje 14denní plán",{"mode":"campaign","brief":"Připrav praktický 14denní publikační plán z existujícího webového obsahu a obchodních cílů. Upřednostni konverze, existující články/produkty a připojené kanály."},worker_group="text",priority=85,notify_user=True,dedupe_key=f"plan14:{pid}:{datetime.now().strftime('%Y%m%d')}")
                return JSONResponse({"ok": True, "message": f"14denní plán připravuji ve frontě #{jid}.", "href": "?page=queue"})
            return JSONResponse({"ok": False, "error": "unknown_action"}, status_code=400)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:500]}, status_code=500)

    if page == "api_chat":
        user = current_user(request)
        if not user:
            return JSONResponse({"ok": False}, status_code=401)
        pid = int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        if not pid or not project(int(user["id"]), pid):
            return JSONResponse({"ok": False, "messages": []}, status_code=404)
        messages = chat_events(int(user["id"]), pid, 50)
        return JSONResponse({"ok": True, "project_id": pid, "messages": [
            {"id": int(m.get("id") or 0), "role": "user" if m.get("event") == "chat_user" else "linda", "message": str(m.get("message") or ""), "created_at": str(m.get("created_at") or "")}
            for m in messages
        ]})

    if page == "api_chat_send":
        user = current_user(request)
        if not user:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        if request.method != "POST":
            return JSONResponse({"ok": False, "error": "method_not_allowed"}, status_code=405)
        form = await request.form()
        try:
            verify_csrf(request, str(form.get("csrf") or ""))
        except Exception:
            return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
        uid_api = int(user["id"])
        pid = int(form.get("project_id") or request.session.get("current_project_id") or 0)
        message = str(form.get("message") or "").strip()
        workflow_key = str(form.get("workflow_key") or "").strip()
        if not pid or not project(uid_api, pid):
            return JSONResponse({"ok": False, "error": "project_not_found"}, status_code=404)
        if not message:
            return JSONResponse({"ok": False, "error": "empty_message"}, status_code=400)
        log_ai_event(uid_api, pid, "chat_user", message, {"workflow_key": workflow_key, "mode": "interactive"})
        try:
            result = run_linda_chat_direct(uid_api, pid, message, workflow_key)
            return JSONResponse({"ok": True, **result})
        except Exception as exc:
            log_ai_event(uid_api, pid, "chat_error", "Linda nedokončila odpověď.", {"error": str(exc)[:2000]})
            return JSONResponse({"ok": False, "error": "Linda se teď nemohla spojit s AI Enginem.", "detail": str(exc)[:500]}, status_code=502)

    if page == "fresh_key_login":
        if not fresh_key.configured():
            _flash(request, "Fresh Key není nakonfigurovaný.")
            return _redirect("login")
        state = secrets.token_urlsafe(32)
        request.session["fresh_key_login_state"] = state
        current = current_user(request)
        request.session["fresh_key_link_local_user_id"] = int(current["id"]) if current else 0
        return_url = settings.app_url.rstrip("/") + "/?" + urlencode({"page": "fresh_key_login_callback"})
        try:
            return RedirectResponse(url=fresh_key.sso_authorize_url(state, return_url), status_code=303)
        except Exception as exc:
            _flash(request, f"Fresh Key přihlášení se nepodařilo spustit: {exc}")
            return _redirect("login")

    if page == "fresh_key_login_callback":
        state = str(request.query_params.get("state") or "")
        expected = str(request.session.pop("fresh_key_login_state", "") or "")
        code = str(request.query_params.get("fresh_key_code") or "")
        preferred_user_id = int(request.session.pop("fresh_key_link_local_user_id", 0) or 0)
        if not expected or not state or not hmac.compare_digest(expected, state) or not code:
            _flash(request, "Fresh Key přihlášení nebylo možné bezpečně ověřit.")
            return _redirect("login")
        try:
            data = fresh_key.sso_exchange(code)
            identity = data.get("identity") or {}
            identity_id = str(identity.get("id") or "")
            email = str(identity.get("email") or "").strip().lower()
            display_name = str(identity.get("display_name") or "").strip() or email.split("@", 1)[0]
            external_user_id = str(data.get("external_user_id") or "").strip()
            binding_token = str(data.get("binding_token") or "").strip()
            if not identity_id or not email:
                raise RuntimeError("Fresh Key nevrátil ověřenou identitu.")

            target = None
            if preferred_user_id:
                target = user_by_id(preferred_user_id)
                if not target:
                    raise RuntimeError("Lokální Linda účet pro propojení nebyl nalezen.")
                if external_user_id and external_user_id != str(preferred_user_id):
                    raise RuntimeError("Fresh Key identita je už v Lindě propojena s jiným účtem.")
            elif external_user_id:
                target = user_by_id(int(external_user_id)) if external_user_id.isdigit() else None
                if not target:
                    raise RuntimeError("Fresh Key odkazuje na Linda účet, který už neexistuje.")
            else:
                target = user_by_email(email)

            if target and str(target.get("email") or "").strip().lower() != email:
                raise RuntimeError("E-mail lokálního účtu neodpovídá ověřené Fresh Key identitě.")

            if not target:
                with db() as conn:
                    result = conn.execute(text(
                        "INSERT INTO users(name,email,password_hash,role,status) VALUES(:name,:email,:hash,'user','active')"
                    ), {"name": display_name[:190], "email": email, "hash": hash_password(secrets.token_urlsafe(48))})
                    target_id = int(result.lastrowid)
                target = user_by_id(target_id)
            target_id = int(target["id"])

            if binding_token:
                bound_identity = fresh_key.sso_bind(binding_token, target_id)
                if bound_identity and bound_identity != identity_id:
                    raise RuntimeError("Fresh Key vrátil jinou identitu při propojení.")
            elif external_user_id != str(target_id):
                raise RuntimeError("Fresh Key účet se nepodařilo spárovat s Lindou.")

            with db() as conn:
                conn.execute(text("UPDATE users SET fresh_key_identity_id=:fk WHERE id=:id"), {"fk": identity_id, "id": target_id})
            target = user_by_id(target_id) or target
            request.session["user"] = {
                "id": target_id, "name": str(target.get("name") or display_name),
                "email": str(target.get("email") or email), "role": str(target.get("role") or "user"),
            }
            try:
                fresh_key.accept_local_invitations(target_id, email)
            except Exception:
                pass
            local_projects = list_projects(target_id)
            _flash(request, "Fresh Key účet je propojený. Projekty a projektové konektory můžeš používat napříč Fresh aplikacemi.")
            return _redirect("dashboard" if local_projects else "projects")
        except Exception as exc:
            _flash(request, f"Fresh Key přihlášení se nepodařilo dokončit: {exc}")
            return _redirect("login")

    if page == "login":
        if current_user(request):
            return _redirect("dashboard")
        if request.method == "POST":
            form = await request.form()
            verify_csrf(request, str(form.get("csrf") or ""))
            email = str(form.get("email") or "").strip().lower()
            password = str(form.get("password") or "")
            user = user_by_email(email)
            if user and str(user.get("status") or "active") == "active" and verify_php_password(password, str(user.get("password_hash") or "")):
                request.session["user"] = {
                    "id": int(user["id"]), "name": str(user["name"]),
                    "email": str(user["email"]), "role": str(user["role"]),
                }
                try:
                    fresh_key.accept_local_invitations(int(user["id"]), email)
                except Exception:
                    pass
                return _redirect("dashboard")
            _flash(request, "Nesprávný e-mail nebo heslo.")
            return _redirect("login")
        return _render(request, "login.html", "Přihlášení", "login")

    if page == "register":
        if settings.registration_mode == "closed":
            _flash(request, "Registrace je nyní uzavřená. Účet ti může založit administrátor.")
            return _redirect("login")
        if request.method == "POST":
            form = await request.form()
            verify_csrf(request, str(form.get("csrf") or ""))
            name = str(form.get("name") or "").strip()
            email = str(form.get("email") or "").strip().lower()
            password = str(form.get("password") or "")
            password2 = str(form.get("password2") or "")
            if not name or not email or len(password) < 8 or password != password2:
                _flash(request, "Zkontroluj jméno, e-mail a hesla.")
                return _redirect("register")
            if settings.registration_mode == "invite":
                _flash(request, "Registrace je pouze na pozvánku. Požádej administrátora o založení účtu.")
                return _redirect("login")
            if user_by_email(email):
                _flash(request, "Účet s tímto e-mailem už existuje.")
                return _redirect("login")
            with db() as conn:
                result = conn.execute(
                    text("INSERT INTO users(name,email,password_hash,role,status) VALUES(:name,:email,:hash,'user','active')"),
                    {"name": name, "email": email, "hash": hash_password(password)},
                )
                user_id = int(result.lastrowid)
            request.session["user"] = {"id": user_id, "name": name, "email": email, "role": "user"}
            try:
                fresh_key.accept_local_invitations(user_id, email)
            except Exception:
                pass
            _flash(request, "Účet je připraven. Teď spolu založíme první projekt.")
            return RedirectResponse(url="/?page=projects&new=1", status_code=303)
        return _render(request, "register.html", "Registrace", "register")

    user = _need_user(request)
    if isinstance(user, RedirectResponse):
        return user
    uid = int(user["id"])

    if page == "file":
        file_type = str(request.query_params.get("type") or "media")
        file_id = int(request.query_params.get("id") or 0)
        if file_type == "reference":
            result = rows(
                "SELECT v.file_path FROM visual_references v JOIN projects pr ON pr.id=v.project_id "
                "WHERE v.id=:id AND (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid)) LIMIT 1",
                {"id": file_id, "uid": uid},
            )
        else:
            result = rows(
                "SELECT m.file_path FROM media_assets m JOIN projects pr ON pr.id=m.project_id "
                "WHERE m.id=:id AND (pr.user_id=:uid OR EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id=pr.id AND pm.user_id=:uid)) LIMIT 1",
                {"id": file_id, "uid": uid},
            )
        target = _safe_upload_path(str(result[0].get("file_path") or "")) if result else None
        if not target:
            return HTMLResponse("Soubor nenalezen.", status_code=404)
        if file_type != "reference" and str(request.query_params.get("variant") or "") == "thumb":
            thumb = thumbnail_path(file_id, target)
            if thumb:
                target = thumb
        return _file_response(target)

    if page == "dashboard":
        projects = project_options(uid)
        selected = int(request.query_params.get("project_id") or request.session.get("current_project_id") or (projects[0]["id"] if projects else 0))
        if selected and project(uid, selected):
            request.session["current_project_id"] = selected
        else:
            selected = 0
        if request.method == "POST":
            form = await request.form()
            verify_csrf(request, str(form.get("csrf") or ""))
            action = str(form.get("action") or "chat")
            if action == "chat":
                pid = int(form.get("project_id") or selected or 0)
                message = str(form.get("message") or "").strip()
                workflow_key = str(form.get("workflow_key") or "").strip()
                if not project(uid, pid):
                    _flash(request, "Vyber projekt, se kterým má Linda pracovat.")
                    return _redirect("projects")
                if not message:
                    _flash(request, "Napiš Lindě, co dnes potřebuješ vyřešit.")
                    return _redirect("dashboard", f"&project_id={pid}")
                log_ai_event(uid, pid, "chat_user", message, {"workflow_key": workflow_key, "mode": "interactive"})
                try:
                    run_linda_chat_direct(uid, pid, message, workflow_key)
                    _flash(request, "Linda odpověděla.")
                except Exception as exc:
                    log_ai_event(uid, pid, "chat_error", "Linda nedokončila odpověď.", {"error": str(exc)[:2000]})
                    _flash(request, "Linda se teď nemohla spojit s AI Enginem. Zkus to znovu nebo otevři diagnostiku.")
                return _redirect("dashboard", f"&project_id={pid}")
        try:
            refresh_action_items(uid)
        except Exception:
            pass
        if selected:
            _ensure_media_maintenance(uid, selected)
        actions = action_rows(uid, 30)
        if selected:
            actions = [a for a in actions if not a.get("project_id") or int(a.get("project_id") or 0) == selected]
        approval_count = 0
        if selected:
            try:
                approval_count = int(rows(
                    "SELECT COUNT(*) c FROM posts WHERE project_id=:pid AND status IN ('draft','ready')",
                    {"pid": selected},
                )[0]["c"])
            except Exception:
                approval_count = 0
        return _render(
            request, "dashboard.html", "Linda", page,
            stats=dashboard_stats(uid), queue=queue_summary(uid), actions=actions[:8],
            recent_posts=content_rows(uid, selected or None, 8), projects=projects,
            selected_project=selected, chat_messages=chat_events(uid, selected, 50) if selected else [],
            workflows=conversion_workflows(), approval_count=approval_count, proactive_brief=_proactive_brief(uid, selected),
        )

    if page == "projects":
        if request.method == "POST":
            form = await request.form()
            verify_csrf(request, str(form.get("csrf") or ""))
            action = str(form.get("action") or "create")
            if action == "create":
                values = {
                    "name": str(form.get("name") or "").strip(),
                    "website_url": str(form.get("website_url") or "").strip() or None,
                    "brand_tone": str(form.get("brand_tone") or "").strip() or None,
                    "target_audience": str(form.get("target_audience") or "").strip() or None,
                    "main_offer": str(form.get("main_offer") or "").strip() or None,
                    "visual_style": str(form.get("visual_style") or "").strip() or None,
                    "content_goals": str(form.get("content_goals") or "").strip() or None,
                }
                if not values["name"]:
                    _flash(request, "Doplň název projektu.")
                else:
                    pid = create_project(uid, values)
                    _flash(request, "Projekt byl vytvořen v Lindě. Do Fresh Key ho odešleš až z bloku Propojení v nastavení projektu.")
                    return _redirect("project_setup", f"&project_id={pid}")
            elif action == "attach_fresh_project":
                # Legacy 0.4.8.2 action intentionally disabled. Cross-app links are
                # proposals now and must be confirmed in the target application.
                _flash(request, "Přímé připojení bylo nahrazeno potvrzovaným workflow. Připojení navrhni v AI Enginu a potvrď ho v Lindě.")
            elif action == "fresh_key_accept_request":
                request_id = str(form.get("request_id") or "").strip()
                target_pid = int(form.get("project_id") or 0)
                try:
                    pending = fresh_key.pending_link_requests(uid)
                    req = next((x for x in pending if str(x.get("id") or "") == request_id), None)
                    if not req:
                        raise RuntimeError("Návrh propojení už není aktivní.")
                    if not target_pid:
                        values = {
                            "name": str(req.get("target_project_name") or req.get("link_name") or "Projekt"),
                            "website_url": str(req.get("requested_project_url") or "") or None,
                            "brand_tone": None, "target_audience": None, "main_offer": None,
                            "visual_style": None, "content_goals": None,
                        }
                        target_pid = create_project(uid, values)
                    fresh_key.accept_link_request(uid, request_id, target_pid)
                    _flash(request, "Propojení bylo potvrzeno. Projekt zůstává spravovaný v Lindě, Fresh Key jen drží společnou vazbu.")
                    return _redirect("project_setup", f"&project_id={target_pid}")
                except Exception as exc:
                    _flash(request, f"Propojení se nepodařilo potvrdit: {exc}")
            elif action == "fresh_key_decline_request":
                try:
                    # Na stránce projektů jde o návrh na vytvoření nového lokálního
                    # projektu, takže ještě neexistuje lokální role, kterou by šlo ověřit.
                    # Fresh Key sám ověří, že návrh patří tomuto uživateli/aplikaci.
                    fresh_key.decline_link_request(uid, str(form.get("request_id") or ""))
                    _flash(request, "Návrh propojení byl odmítnut.")
                except Exception as exc:
                    _flash(request, f"Návrh se nepodařilo odmítnout: {exc}")
            elif action == "delete":
                pid=int(form.get("project_id") or 0)
                try:
                    delete_project(uid,pid); _flash(request,"Projekt byl smazán.")
                except Exception as exc:
                    _flash(request,str(exc))
        fresh_projects = []
        fresh_key_requests = []
        fresh_key_error = ""
        if fresh_key.configured():
            try:
                fresh_key.ensure_identity(uid)
                fresh_projects = fresh_key.available_projects(uid)
                fresh_key_requests = fresh_key.pending_link_requests(uid)
                for req in fresh_key_requests:
                    target = int(req.get("target_external_project_id") or 0) if str(req.get("target_external_project_id") or "").isdigit() else None
                    notify(uid, target, "fresh_key_link", "info", "Návrh propojení projektu",
                           f"{req.get('requester',{}).get('email') or 'Uživatel Fresh AI'} navrhuje propojení {req.get('link_name') or 'projektu'}.",
                           f"?page={'project_setup' if target else 'projects'}" + (f"&project_id={target}" if target else ""),
                           f"fresh-key-link:{req.get('id')}", 24)
            except Exception as exc:
                fresh_key_error = str(exc)
        local_projects = list_projects(uid)
        local_fresh_ids = {str(x.get("fresh_key_project_id") or "") for x in local_projects if x.get("fresh_key_project_id")}
        for fp in fresh_projects:
            fp["already_in_linda"] = str(fp.get("id") or "") in local_fresh_ids
        return _render(request, "projects.html", "Projekty", page, projects=local_projects, fresh_projects=fresh_projects, fresh_key_requests=fresh_key_requests, fresh_key_error=fresh_key_error)

    if page == "project_setup":
        pid=int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        p=project(uid,pid)
        if not p:
            _flash(request,"Projekt nebyl nalezen."); return _redirect("projects")
        request.session["current_project_id"]=pid
        project_role = fresh_key.local_project_role(uid, pid)
        if request.method == "POST":
            form=await request.form(); verify_csrf(request,str(form.get("csrf") or "")); action=str(form.get("action") or "save")
            if project_role == "viewer":
                _flash(request, "Čtenář může projekt pouze prohlížet. Nemůže nic upravovat ani schvalovat.")
                return _redirect("project_setup",f"&project_id={pid}")
            if action in {"save","save_profile","save_style_overrides"}:
                values={
                    "name":str(form.get("name") or p.get("name") or "").strip(),
                    "website_url":str(form.get("website_url") or "").strip() or None,
                    "brand_tone":str(form.get("brand_tone") or "").strip() or None,
                    "target_audience":str(form.get("target_audience") or "").strip() or None,
                    "main_offer":str(form.get("main_offer") or "").strip() or None,
                    "visual_style":str(form.get("visual_style") or "").strip() or None,
                    "content_goals":str(form.get("content_goals") or "").strip() or None,
                    "autopilot_mode":str(form.get("autopilot_mode") or p.get("autopilot_mode") or "review"),
                    "web_publish_mode":str(form.get("web_publish_mode") or p.get("web_publish_mode") or "publish"),
                    "brand_style_overrides":str(form.get("brand_style_overrides") or p.get("brand_style_overrides") or "").strip() or None,
                }
                update_project(uid,pid,values); _flash(request,"Nastavení projektu uloženo.")
            elif action == "refresh_profile":
                jid=queue_job(uid,pid,"project_profile","Linda aktualizuje profil projektu",{"manual":True},worker_group="text",priority=90,notify_user=True,dedupe_key=f"project_profile:{pid}")
                _flash(request,f"Aktualizace profilu je ve frontě #{jid}.")
            elif action == "analyze_brand":
                jid=queue_job(uid,pid,"brand_analyze","Linda analyzuje Brand kit",{"manual":True},worker_group="media",priority=75,notify_user=True,dedupe_key=f"brand_analyze:{pid}")
                _flash(request,f"Analýza Brand kitu je ve frontě #{jid}.")
            elif action == "fresh_key_publish_project":
                try:
                    result = fresh_key.publish_project(uid, pid)
                    if str(result.get("status") or "") == "match_required":
                        names = ", ".join(str(x.get("name") or "") for x in (result.get("candidates") or [])[:3])
                        _flash(request, f"Fresh Key našel pravděpodobně stejný projekt{(': ' + names) if names else ''}. Otevři Spravovat propojení a potvrď, zda ho chceš připojit k existujícímu Propojení, nebo ponechat samostatně.")
                    else:
                        _flash(request, "Projekt byl odeslán do Fresh Key.")
                except Exception as exc:
                    _flash(request, f"Projekt se nepodařilo odeslat do Fresh Key: {exc}")
            elif action == "fresh_key_accept_request":
                try:
                    if not fresh_key.can_manage_local_project(uid, pid):
                        raise RuntimeError("Propojení může potvrdit pouze Vlastník nebo Správce projektu.")
                    fresh_key.accept_link_request(uid, str(form.get("request_id") or ""), pid)
                    _flash(request, "Návrh propojení byl potvrzen.")
                except Exception as exc:
                    _flash(request, f"Propojení se nepodařilo potvrdit: {exc}")
            elif action == "fresh_key_decline_request":
                try:
                    if not fresh_key.can_manage_local_project(uid, pid):
                        raise RuntimeError("Propojení může odmítnout pouze Vlastník nebo Správce projektu.")
                    fresh_key.decline_link_request(uid, str(form.get("request_id") or ""))
                    _flash(request, "Návrh propojení byl odmítnut.")
                except Exception as exc:
                    _flash(request, f"Návrh se nepodařilo odmítnout: {exc}")
            elif action == "project_share_member":
                email = str(form.get("member_email") or "").strip().lower()
                role = str(form.get("member_role") or "editor").strip() or "editor"
                relationship = str(form.get("member_relationship") or "member").strip() or "member"
                try:
                    result = fresh_key.share_local_project(uid, pid, email, role, relationship)
                    if result.get("status") == "active":
                        _flash(request, "Uživatel byl přidán do projektu v Lindě a dostal potvrzení spolupráce.")
                    else:
                        _flash(request, "Pozvánka k projektu byla uložena a odeslána e-mailem.")
                except Exception as exc:
                    _flash(request, f"Uživatele se nepodařilo přidat: {exc}")
            elif action == "project_share_update":
                try:
                    fresh_key.update_local_project_share(uid, pid, str(form.get("member_email") or ""), str(form.get("member_role") or "editor"), str(form.get("member_relationship") or "member"))
                    _flash(request, "Role uživatele byla upravena v Lindě.")
                except Exception as exc:
                    _flash(request, f"Uživatele se nepodařilo upravit: {exc}")
            elif action == "project_share_remove":
                try:
                    fresh_key.remove_local_project_share(uid, pid, str(form.get("member_email") or ""))
                    _flash(request, "Uživatel nebo pozvánka byly z projektu odebrány.")
                except Exception as exc:
                    _flash(request, f"Uživatele se nepodařilo odebrat: {exc}")
            return _redirect("project_setup",f"&project_id={pid}")
        connectors=list_connectors(uid,pid)
        kcount=rows("SELECT COUNT(*) c FROM project_knowledge WHERE project_id=:pid AND status='active'",{"pid":pid})[0]["c"]
        refs=reference_rows(uid,pid)
        project_people=fresh_key.local_project_people(uid,pid)
        project_can_manage=fresh_key.can_manage_local_project(uid,pid)
        fresh_key_members=[]
        fresh_key_requests=[]
        fresh_key_members_error=""
        fresh_key_link={"linked":False,"applications":[],"members":[],"can_manage":False}
        if fresh_key.configured():
            try:
                fresh_key_link=fresh_key.project_link_summary(uid,pid)
                fresh_key_members=fresh_key_link.get("members") or []
                fresh_key_requests=[x for x in fresh_key.pending_link_requests(uid) if str(x.get("target_external_project_id") or "") == str(pid)]
                for req in fresh_key_requests:
                    notify(uid, pid, "fresh_key_link", "info", "Návrh propojení projektu",
                           f"{req.get('requester',{}).get('email') or 'Uživatel Fresh AI'} navrhuje propojení s {req.get('link_name') or 'Fresh Key'}.",
                           f"?page=project_setup&project_id={pid}", f"fresh-key-link:{req.get('id')}", 24)
            except Exception as exc:
                fresh_key_members_error=str(exc)
        return _render(request,"project_setup.html",str(p.get("name") or "Projekt"),page,project=p,connectors=connectors,knowledge_count=int(kcount),references=refs,fresh_key_link=fresh_key_link,fresh_key_members=fresh_key_members,fresh_key_requests=fresh_key_requests,fresh_key_members_error=fresh_key_members_error,project_people=project_people,project_can_manage=project_can_manage,project_can_edit=fresh_key.can_edit_local_project(uid,pid),project_role=project_role,fresh_key_ready=fresh_key.configured(),fresh_key_manage_url=(fresh_key_link.get("manage_url") or fresh_key.manage_url()))

    if page == "linda":
        projects = project_options(uid)
        selected = int(request.query_params.get("project_id") or request.session.get("current_project_id") or (projects[0]["id"] if projects else 0))
        if selected:
            request.session["current_project_id"] = selected
        if request.method == "POST":
            form = await request.form()
            verify_csrf(request, str(form.get("csrf") or ""))
            pid = int(form.get("project_id") or 0)
            brief = str(form.get("brief") or "").strip()
            mode = str(form.get("mode") or "campaign")
            workflow_key = str(form.get("workflow_key") or "").strip()
            if not project(uid, pid):
                _flash(request, "Projekt nebyl nalezen.")
                return _redirect("linda")
            if not brief:
                _flash(request, "Doplň témata nebo krátké zadání pro Lindu.")
                return _redirect("linda", f"&project_id={pid}")
            job_id = queue_job(
                uid, pid, "ai_activity", "Linda připravuje marketingovou aktivitu",
                {"mode": mode, "brief": brief, "workflow_key": workflow_key},
                worker_group="text", priority=80, notify_user=True,
            )
            _flash(request, f"Linda převzala zadání do fronty #{job_id}. Můžeš pokračovat.")
            return _redirect("linda", f"&project_id={pid}")
        return _render(request, "linda.html", "Nová aktivita", page, projects=projects, selected_project=selected, workflows=conversion_workflows())

    if page == "queue":
        cleanup_legacy_chat_jobs(uid)
        recover_stale_jobs(20, uid)
        if request.method == "POST":
            form = await request.form()
            verify_csrf(request, str(form.get("csrf") or ""))
            action = str(form.get("action") or "process")
            if action == "retry_job":
                job_id = int(form.get("job_id") or 0)
                job_rows = rows("SELECT * FROM task_queue WHERE id=:id AND user_id=:uid LIMIT 1", {"id": job_id, "uid": uid})
                if job_rows:
                    job = job_rows[0]
                    try:
                        payload = json.loads(str(job.get("payload") or "{}"))
                    except Exception:
                        payload = {}
                    with db() as conn:
                        conn.execute(text("UPDATE task_queue SET status='queued',attempts=0,progress=0,message='Čeká na opakování.',locked_at=NULL,finished_at=NULL,available_at=NOW(),updated_at=NOW() WHERE id=:id AND user_id=:uid"), {"id": job_id, "uid": uid})
                        post_id = int(payload.get("post_id") or 0) if isinstance(payload, dict) else 0
                        if post_id:
                            conn.execute(text("UPDATE posts SET publication_hold=NULL,publication_hold_at=NULL,publication_hold_message=NULL WHERE id=:id"), {"id": post_id})
                    _flash(request, "Úlohu jsem vrátila do fronty.")
                return _redirect("queue")
            if action == "cancel_job":
                job_id = int(form.get("job_id") or 0)
                if job_id:
                    with db() as conn:
                        conn.execute(text("UPDATE task_queue SET status='failed',locked_at=NULL,finished_at=NOW(),message='Zrušeno uživatelem.',updated_at=NOW() WHERE id=:id AND user_id=:uid AND status IN ('queued','running')"), {"id":job_id,"uid":uid})
                    _flash(request, "Úlohu jsem zrušila.")
                return _redirect("queue")
            group = str(form.get("worker_group") or "all")
            result = process_available_jobs(limit=12, worker_group=group)
            _flash(request, f"Worker: hotovo {result['done']}, selhalo {result['failed']}, převzato {result['claimed']}.")
            return _redirect("queue")
        qmeta = {str(x.get("meta_key")): str(x.get("meta_value") or "") for x in rows("SELECT meta_key,meta_value FROM app_meta WHERE meta_key IN ('linda_cron_last_run','linda_cron_last_worker','linda_cron_last_result')")}
        qrows = queue_rows(uid, 180)
        for q in qrows:
            q["friendly"] = _queue_friendly_message(q)
        active_rows = [q for q in qrows if str(q.get("status")) in {"queued","running"}]
        failed_rows = [q for q in qrows if str(q.get("status")) == "failed"][:24]
        done_rows = [q for q in qrows if str(q.get("status")) == "done"][:8]
        last_run = {}
        try:
            last_run = json.loads(qmeta.get("linda_cron_last_result") or "{}")
            if not isinstance(last_run, dict): last_run = {}
        except Exception:
            last_run = {}
        return _render(request, "queue.html", "Fronta úloh", page, rows=qrows, active_rows=active_rows, failed_rows=failed_rows, done_rows=done_rows, last_run=last_run, summary=queue_summary(uid), queue_runtime=qmeta, queue_status_labels=QUEUE_STATUS_LABELS, queue_group_labels=QUEUE_GROUP_LABELS, queue_type_labels=QUEUE_TYPE_LABELS)

    if page == "settings":
        selected=int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        return _render(request, "settings.html", "Nastavení", page, selected_project=selected, projects=project_options(uid))

    if page == "admin":
        if user["role"] != "superadmin":
            raise HTTPException(status_code=403, detail="Superadmin only")
        tab=str(request.query_params.get("tab") or "users")
        if request.method=="POST":
            form=await request.form(); verify_csrf(request,str(form.get("csrf") or "")); action=str(form.get("action") or "")
            try:
                if action=="create_user":
                    name=str(form.get("name") or "").strip(); email=str(form.get("email") or "").strip().lower(); password=str(form.get("password") or ""); role=str(form.get("role") or "user")
                    if not name or not email or len(password)<8:
                        _flash(request,"Doplň jméno, e-mail a heslo min. 8 znaků."); return _redirect("admin","&tab=users")
                    if role not in {"user","admin","superadmin"}: role="user"
                    with db() as conn: conn.execute(text("INSERT INTO users(name,email,password_hash,role,status,created_by) VALUES(:name,:email,:hash,:role,'active',:creator)"),{"name":name,"email":email,"hash":hash_password(password),"role":role,"creator":uid})
                    _flash(request,"Uživatel vytvořen."); return _redirect("admin","&tab=users")
                if action=="reset_password":
                    target=int(form.get("user_id") or 0); password=str(form.get("password") or "")
                    if target and len(password)>=8:
                        with db() as conn: conn.execute(text("UPDATE users SET password_hash=:hash WHERE id=:id"),{"hash":hash_password(password),"id":target})
                        _flash(request,"Heslo změněno.")
                    else: _flash(request,"Heslo musí mít alespoň 8 znaků.")
                    return _redirect("admin","&tab=users")
                if action=="save_workflow":
                    items=conversion_workflows(); key=str(form.get("key") or "").strip() or f"workflow_{len(items)+1}"
                    item={"key":key,"label":str(form.get("label") or key).strip(),"description":str(form.get("description") or "").strip(),"rules":str(form.get("rules") or "").strip(),"enabled":bool(form.get("enabled"))}
                    items=[x for x in items if str(x.get("key"))!=key]+[item]; save_conversion_workflows(items); _flash(request,"Logická cesta uložena.")
                    return _redirect("admin", "&tab=workflows")
                if action=="delete_workflow":
                    key=str(form.get("key") or ""); save_conversion_workflows([x for x in conversion_workflows() if str(x.get("key"))!=key]); _flash(request,"Logická cesta smazána."); return _redirect("admin","&tab=workflows")
                if action=="save_format":
                    items=post_formats(); key=str(form.get("key") or "").strip() or f"format_{len(items)+1}"
                    plats=[x.strip() for x in str(form.get("platforms") or "").split(",") if x.strip()]
                    item={"key":key,"label":str(form.get("label") or key).strip(),"platforms":plats,"asset":str(form.get("asset") or "image_optional").strip(),"enabled":bool(form.get("enabled"))}
                    items=[x for x in items if str(x.get("key"))!=key]+[item]; save_post_formats(items); _flash(request,"Formát uložen."); return _redirect("admin","&tab=formats")
                if action=="delete_format":
                    key=str(form.get("key") or ""); save_post_formats([x for x in post_formats() if str(x.get("key"))!=key]); _flash(request,"Formát smazán."); return _redirect("admin","&tab=formats")
                if action=="save_planned_task":
                    items=planned_tasks(); key=str(form.get("key") or "").strip() or f"task_{len(items)+1}"
                    cadence=str(form.get("cadence") or "daily"); cadence=cadence if cadence in {"daily","weekly"} else "daily"
                    task=str(form.get("task") or "proactive_review")
                    allowed={"proactive_review","analytics_sync","media_maintenance","project_profile","brand_analyze","wordpress_sync"}
                    if task not in allowed: task="proactive_review"
                    try: hour=max(0,min(23,int(form.get("hour") or 8)))
                    except Exception: hour=8
                    try: weekday=max(0,min(6,int(form.get("weekday") or 0)))
                    except Exception: weekday=0
                    try: project_id=int(form.get("project_id") or 0) or None
                    except Exception: project_id=None
                    item={"key":key,"label":str(form.get("label") or key).strip(),"description":str(form.get("description") or "").strip(),"task":task,"cadence":cadence,"hour":hour,"weekday":weekday,"project_id":project_id,"enabled":bool(form.get("enabled"))}
                    items=[x for x in items if str(x.get("key"))!=key]+[item]; save_planned_tasks(items); _flash(request,"Plánovaná úloha uložena."); return _redirect("admin","&tab=schedules")
                if action=="delete_planned_task":
                    key=str(form.get("key") or ""); save_planned_tasks([x for x in planned_tasks() if str(x.get("key"))!=key]); _flash(request,"Plánovaná úloha smazána."); return _redirect("admin","&tab=schedules")
                if action=="toggle_user":
                    target=int(form.get("user_id") or 0)
                    with db() as conn: conn.execute(text("UPDATE users SET status=IF(status='active','inactive','active') WHERE id=:id"),{"id":target})
                    _flash(request,"Stav uživatele změněn."); return _redirect("admin","&tab=users")
                if action=="set_role":
                    target=int(form.get("user_id") or 0); role=str(form.get("role") or "user")
                    if role not in {"user","admin","superadmin"}: role="user"
                    with db() as conn: conn.execute(text("UPDATE users SET role=:role WHERE id=:id"),{"role":role,"id":target})
                    _flash(request,"Role uložena."); return _redirect("admin","&tab=users")
                if action in {"queue_retry","queue_cancel","queue_delete_done"}:
                    job_id=int(form.get("job_id") or 0)
                    if job_id:
                        with db() as conn:
                            if action=="queue_retry":
                                job_rows = rows("SELECT payload FROM task_queue WHERE id=:id LIMIT 1", {"id": job_id})
                                conn.execute(text("UPDATE task_queue SET status='queued',attempts=0,locked_at=NULL,finished_at=NULL,available_at=NOW(),progress=0,message='Úloha vrácena do fronty administrátorem.',updated_at=NOW() WHERE id=:id"),{"id":job_id})
                                if job_rows:
                                    try:
                                        payload = json.loads(str(job_rows[0].get("payload") or "{}"))
                                    except Exception:
                                        payload = {}
                                    post_id = int(payload.get("post_id") or 0) if isinstance(payload, dict) else 0
                                    if post_id:
                                        conn.execute(text("UPDATE posts SET publication_hold=NULL,publication_hold_at=NULL,publication_hold_message=NULL WHERE id=:id"), {"id": post_id})
                            elif action=="queue_cancel":
                                job_rows = rows("SELECT type,payload FROM task_queue WHERE id=:id LIMIT 1", {"id": job_id})
                                conn.execute(text("UPDATE task_queue SET status='failed',locked_at=NULL,finished_at=NOW(),message='Zrušeno administrátorem.',updated_at=NOW() WHERE id=:id AND status IN ('queued','running')"),{"id":job_id})
                                if job_rows and str(job_rows[0].get("type") or "") in {"wp_publish_post","social_publish_post"}:
                                    try:
                                        payload = json.loads(str(job_rows[0].get("payload") or "{}"))
                                    except Exception:
                                        payload = {}
                                    post_id = int(payload.get("post_id") or 0) if isinstance(payload, dict) else 0
                                    if post_id:
                                        conn.execute(text("UPDATE posts SET publication_hold='manual',publication_hold_at=NOW(),publication_hold_message='Publikace byla ručně pozastavena administrátorem.' WHERE id=:id"), {"id": post_id})
                            else:
                                conn.execute(text("DELETE FROM task_queue WHERE id=:id AND status='done'"),{"id":job_id})
                    _flash(request,"Fronta byla upravena."); return _redirect("admin","&tab=queue")
                if action=="queue_process":
                    result=process_available_jobs(limit=12,worker_group="all")
                    _flash(request,f"Worker: hotovo {result.get('done',0)}, selhalo {result.get('failed',0)}."); return _redirect("admin","&tab=queue")
                if action in {"media_maintenance","media_maintenance_now","media_maintenance_project","media_maintenance_all"}:
                    try: batch_limit=max(1,min(80,int(form.get("limit") or settings.media_maintenance_batch)))
                    except Exception: batch_limit=int(settings.media_maintenance_batch)
                    try: target_project=int(form.get("project_id") or 0)
                    except Exception: target_project=0
                    if action=="media_maintenance_all": target_project=0
                    title="Linda uklízí mediální knihovnu" if not target_project else f"Linda uklízí média projektu #{target_project}"
                    jid=queue_job(uid,target_project or None,"media_maintenance",title,{"limit":batch_limit,"manual":True},worker_group="maintenance",priority=95 if action=="media_maintenance_now" else 25,notify_user=True,dedupe_key=f"media-maintenance:{target_project or 'global'}")
                    if action=="media_maintenance_now":
                        result=process_available_jobs(limit=1,worker_group="maintenance")
                        _flash(request,f"Údržba spuštěna: dokončeno {result.get('done',0)}, problém {result.get('failed',0)}.")
                    else:
                        _flash(request,f"Údržba médií je ve frontě #{jid}.")
                    return _redirect("admin","&tab=diagnostics")
            except Exception as exc:
                _flash(request,f"Admin konzole: {exc}")
        admin_users=all_users(); admin_projects=rows("SELECT id,name FROM projects ORDER BY name")
        def _query_int(name: str) -> int:
            try:
                return max(0, int(request.query_params.get(name) or 0))
            except (TypeError, ValueError):
                return 0
        qf={
            "status":str(request.query_params.get("queue_status") or ""),
            "user_id":_query_int("queue_user"),
            "project_id":_query_int("queue_project"),
            "job_type":str(request.query_params.get("queue_type") or ""),
        }
        return _render(request,"admin.html","Admin konzole",page,tab=tab,users=admin_users,workflows=conversion_workflows(),formats=post_formats(),planned_tasks=planned_tasks(),projects_all=admin_projects,admin_queue=_admin_queue_rows(status=qf["status"],user_id=qf["user_id"],project_id=qf["project_id"],job_type=qf["job_type"]),admin_queue_filters=qf,db_health=db_ping(),schema=schema_report(),engine_health=engine_client.health(),manifest=build_manifest(),queue_status_labels=QUEUE_STATUS_LABELS,queue_group_labels=QUEUE_GROUP_LABELS,queue_type_labels=QUEUE_TYPE_LABELS)

    if page == "connectors":
        selected = int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        focus = str(request.query_params.get("provider") or "")
        fresh_key_error = ""
        if selected and project(uid, selected) and fresh_key.configured():
            try:
                fresh_key.ensure_project(uid, selected)
                fresh_key.sync_local_connectors(uid, selected)
            except Exception as exc:
                fresh_key_error = str(exc)
        if request.method == "POST":
            form = await request.form(); verify_csrf(request, str(form.get("csrf") or ""))
            action = str(form.get("action") or "")
            connector_id = int(form.get("connector_id") or 0)
            existing_connector = connector(uid, connector_id) if connector_id else None
            if action in {"create","update"}:
                pid=int(form.get("project_id") or 0)
                if not project(uid,pid):
                    _flash(request,"Vyber platný projekt."); return _redirect("connectors")
                values={
                    "project_id":pid,
                    "provider":str(form.get("provider") or "wordpress").strip(),
                    "account_name":str(form.get("account_name") or "").strip() or None,
                    "client_id":str(form.get("client_id") or "").strip() or (existing_connector or {}).get("client_id"),
                    "client_secret":str(form.get("client_secret") or "").strip() or (existing_connector or {}).get("client_secret"),
                    "access_token":str(form.get("access_token") or "").strip() or (existing_connector or {}).get("access_token"),
                    "refresh_token":str(form.get("refresh_token") or "").strip() or (existing_connector or {}).get("refresh_token"),
                    "expires_at":_parse_dt(form.get("expires_at")),
                    "endpoint_url":str(form.get("endpoint_url") or "").strip() or None,
                    "auth_user":str(form.get("auth_user") or "").strip() or None,
                    "notes":str(form.get("notes") or "").strip() or None,
                }
                # Fresh Key owns new social OAuth secrets. Manual connectors are
                # retained only for legacy installations and non-Fresh providers.
                if values["provider"] in fresh_key.SOCIAL_PROVIDERS and fresh_key.configured() and action == "create":
                    _flash(request, "Tento kanál připoj přes Fresh Key jedním tlačítkem.")
                    return _redirect("connectors",f"&project_id={pid}&provider={values['provider']}")
                cid=create_or_update_connector(uid,values,connector_id if action=="update" else None)
                saved = connector(uid, cid) or values
                result = test_connector(saved)
                update_connector_test(cid, uid, bool(result.get("ok")), str(result.get("message") or ""), result.get("capabilities"))
                if result.get("ok") and values["provider"] == "wordpress":
                    jid = queue_job(uid,pid,"wordpress_sync",f"Synchronizuji WordPress: {values.get('account_name') or values.get('endpoint_url')}",{"connector_id":cid,"manual":True},worker_group="maintenance",priority=40,notify_user=True,dedupe_key=f"wordpress_sync:{pid}")
                    _flash(request,f"WordPress je připojený. Synchronizaci webu spouštím ve frontě #{jid}.")
                else:
                    _flash(request, str(result.get("message") or "Konektor uložen."))
                return _redirect("connectors",f"&project_id={pid}&provider={values['provider']}")
            c=connector(uid,connector_id) if connector_id else None
            if action == "delete":
                if c:
                    selected=int(c.get("project_id") or 0)
                    if int(c.get("fresh_key_managed") or 0):
                        try: fresh_key.disconnect(uid, c)
                        except Exception as exc:
                            _flash(request, f"Fresh Key odpojení selhalo: {exc}")
                            return _redirect("connectors",f"&project_id={selected}")
                    delete_connector(uid,connector_id); _flash(request,"Konektor byl od projektu odpojen.")
                return _redirect("connectors",f"&project_id={selected}" if selected else "")
            if not c:
                _flash(request, "Konektor nebyl nalezen."); return _redirect("connectors")
            selected = int(c.get("project_id") or 0)
            focus = str(c.get("provider") or "")
            if action == "test":
                result=test_connector(c)
                update_connector_test(connector_id,uid,bool(result.get("ok")),str(result.get("message") or ""),result.get("capabilities"))
                _flash(request,str(result.get("message") or "Test dokončen."))
            elif action in {"sync","load_wp_data"} and c.get("provider") == "wordpress":
                job_id = queue_job(uid, selected, "wordpress_sync", f"Synchronizuji WordPress: {c.get('account_name') or c.get('endpoint_url')}", {"connector_id": connector_id, "manual": True}, worker_group="maintenance", priority=40, notify_user=True, dedupe_key=f"wordpress_sync:{selected}")
                _flash(request, f"Fresh Core synchronizace je ve frontě #{job_id}.")
            return _redirect("connectors", f"&project_id={selected}&provider={focus}" if selected else "")
        return _render(request, "connectors.html", "Konektory", page, projects=project_options(uid), selected_project=selected, focus=focus,
                       connectors=list_connectors(uid, selected or None), fresh_key_ready=fresh_key.configured(), fresh_key_error=fresh_key_error,
                       meta_ready=fresh_key.configured(), linkedin_ready=fresh_key.configured(), google_business_ready=fresh_key.configured())

    if page in {"fresh_key_oauth_start", "meta_oauth_start", "linkedin_oauth_start", "google_business_oauth_start"}:
        pid = int(request.query_params.get("project_id") or 0)
        if not project(uid, pid):
            _flash(request, "Neplatný projekt."); return _redirect("connectors")
        requested = str(request.query_params.get("provider") or "")
        if page == "meta_oauth_start": requested = "meta"
        elif page == "linkedin_oauth_start": requested = "linkedin"
        elif page == "google_business_oauth_start": requested = "google_business"
        provider = "meta" if requested in {"facebook","instagram","meta"} else requested
        if provider not in {"meta","linkedin","google_business"}:
            _flash(request, "Fresh Key tento provider nepodporuje."); return _redirect("connectors", f"&project_id={pid}")
        try:
            url = fresh_key.start_oauth(uid, pid, provider)
            if not url: raise RuntimeError("Fresh Key nevrátil autorizační URL.")
            return RedirectResponse(url=url, status_code=302)
        except Exception as exc:
            _flash(request, f"Fresh Key: {exc}")
            focus_provider = "facebook" if provider == "meta" else provider
            return _redirect("connectors", f"&project_id={pid}&provider={focus_provider}")

    if page == "fresh_key_return":
        pid = int(request.query_params.get("project_id") or 0)
        if not project(uid, pid):
            _flash(request, "Projekt pro návrat z Fresh Key nebyl nalezen."); return _redirect("projects")
        try:
            fresh_key.ensure_project(uid, pid, str(request.query_params.get("fresh_key_link_id") or request.query_params.get("fresh_key_project_id") or ""))
            remote = fresh_key.sync_local_connectors(uid, pid)
            _flash(request, f"Fresh Key připojení je hotové. Projekt má {len(remote)} sdílených externích účtů.")
        except Exception as exc:
            _flash(request, f"Fresh Key spojení se nepodařilo načíst: {exc}")
        return _redirect("connectors", f"&project_id={pid}")

    if page == "diagnostics":
        if user["role"] not in {"admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="Admin only")
        return _render(
            request, "diagnostics.html", "Diagnostika", page,
            db_health=db_ping(),
            schema=schema_report(),
            engine_health=engine_client.health(),
            manifest=build_manifest(),
        )

    if page == "activities":
        selected = int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        return _redirect("dashboard", f"&project_id={selected}" if selected else "")

    if page in {"content", "approvals", "calendar"}:
        selected = int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        if request.method == "POST":
            form=await request.form(); verify_csrf(request,str(form.get("csrf") or "")); action=str(form.get("action") or "")
            if page=="calendar" and action=="reschedule_overdue":
                start=_parse_dt(str(form.get("start_date") or "")+"T00:00") if form.get("start_date") else None
                changed=_reschedule_overdue_posts(uid,selected,start)
                _flash(request,f"Přeplánováno {changed} starších nepublikovaných položek.")
                return _redirect("calendar",f"&project_id={selected}")
            if page=="calendar" and action=="calendar_move":
                pid_move=int(form.get("post_id") or 0); row_move=post_row(uid,pid_move) if pid_move else None
                if not row_move:
                    return JSONResponse({"ok":False,"error":"post_not_found"},status_code=404)
                target_date=str(form.get("date") or ""); target_platform=str(form.get("platform") or row_move.get("platform") or "facebook")
                try:
                    base=datetime.fromisoformat(target_date)
                    old_dt=row_move.get("scheduled_at")
                    if old_dt:
                        target=base.replace(hour=old_dt.hour,minute=old_dt.minute)
                    else:
                        hh,mm=_default_publish_time(target_platform);target=base.replace(hour=hh,minute=mm)
                    update_post(uid,pid_move,{"platform":target_platform,"status":"scheduled","scheduled_at":target})
                    return JSONResponse({"ok":True,"scheduled_at":target.isoformat(timespec="minutes"),"platform":target_platform})
                except Exception as exc:
                    return JSONResponse({"ok":False,"error":str(exc)[:300]},status_code=400)
            if action in {"publish_blog","social_from_blog"}:
                bid=int(form.get("blog_id") or 0); b=blog_row(uid,bid)
                if b:
                    selected=int(b.get("project_id") or selected)
                    if action=="publish_blog":
                        jid=queue_job(uid,selected,"wp_publish_blog",f"WordPress: {b.get('title')}",{"blog_id":bid,"force_publish":True},worker_group="publication",priority=90,notify_user=True,dedupe_key=f"wp-publish:blog:{bid}")
                        _flash(request,f"Publikace článku je ve frontě #{jid}.")
                    else:
                        jid=queue_job(uid,selected,"social_derivatives",f"Distribuce článku: {b.get('title')}",{"blog_id":bid},worker_group="text",priority=70,notify_user=True,dedupe_key=f"social:blog:{bid}")
                        _flash(request,f"Social distribuce je ve frontě #{jid}.")
                return _redirect(page,f"&project_id={selected}" if selected else "")
            post_id=int(form.get("post_id") or 0)
            p=post_row(uid,post_id) if post_id else None
            if action == "bulk":
                ids=[]
                try: ids=[int(x) for x in form.getlist("post_ids") if str(x).isdigit()]
                except Exception: ids=[]
                bulk_action=str(form.get("bulk_action") or "")
                changed=0
                for pid_ in ids:
                    row=post_row(uid,pid_)
                    if not row: continue
                    if bulk_action=="approve":
                        scheduled=row.get("scheduled_at") or datetime.now()
                        update_post(uid,pid_,{"status":"scheduled","scheduled_at":scheduled});changed+=1
                    elif bulk_action=="images":
                        queue_job(uid,int(row.get("project_id") or 0),"image_generate_post",f"Obrázek: {row.get('title')}",{"post_id":pid_},worker_group="media",priority=60,notify_user=False,dedupe_key=f"image:post:{pid_}");changed+=1
                    elif bulk_action=="discard":
                        update_post(uid,pid_,{"status":"draft","scheduled_at":None});changed+=1
                    elif bulk_action=="delete":
                        delete_post(uid,pid_);changed+=1
                _flash(request,f"Hromadná akce dokončena: {changed} položek.")
                return _redirect(page,f"&project_id={selected}" if selected else "")
            if not p:
                _flash(request,"Příspěvek nebyl nalezen.");return _redirect(page,f"&project_id={selected}" if selected else "")
            selected=int(p.get("project_id") or selected)
            try:
                if action == "edit_post":
                    values={"title":str(form.get("title") or "").strip(),"platform":str(form.get("platform") or p.get("platform") or "facebook"),"post_type":str(form.get("post_type") or p.get("post_type") or "single_post"),"body":str(form.get("body") or ""),"image_prompt":str(form.get("image_prompt") or ""),"hashtags":str(form.get("hashtags") or ""),"status":str(form.get("status") or p.get("status") or "draft"),"scheduled_at":_parse_dt(form.get("scheduled_at")),"destination_url":str(form.get("destination_url") or "").strip() or None,"notes":str(form.get("notes") or "").strip() or None}
                    update_post(uid,post_id,values);_flash(request,"Příspěvek uložen.")
                elif action == "delete":
                    delete_post(uid,post_id);_flash(request,"Příspěvek smazán.")
                elif action == "duplicate":
                    nid=duplicate_post(uid,post_id);_flash(request,f"Vytvořena kopie #{nid}.")
                elif action in {"approve","schedule"}:
                    when=_parse_dt(form.get("scheduled_at")) or p.get("scheduled_at") or datetime.now()
                    update_post(uid,post_id,{"status":"scheduled","scheduled_at":when});_flash(request,"Příspěvek schválen a naplánován.")
                elif action == "publish_post":
                    update_post(uid,post_id,{"status":"scheduled","scheduled_at":datetime.now(),"publication_hold":None,"publication_hold_at":None,"publication_hold_message":None})
                    if str(p.get("platform") or "") == "blog":
                        jid=queue_job(uid,selected,"wp_publish_post",f"WordPress: {p.get('title')}",{"post_id":post_id,"force_publish":True},worker_group="publication",priority=100,notify_user=True,dedupe_key=f"wp-publish:post:{post_id}")
                    else:
                        jid=queue_job(uid,selected,"social_publish_post",f"Publikace: {p.get('title')}",{"post_id":post_id},worker_group="publication",priority=100,notify_user=True,dedupe_key=f"publish:post:{post_id}")
                    _flash(request,f"Publikace je ve frontě #{jid}.")
                elif action == "generate_image":
                    jid=queue_job(uid,selected,"image_generate_post",f"Obrázek: {p.get('title')}",{"post_id":post_id},worker_group="media",priority=60,notify_user=True,dedupe_key=f"image:post:{post_id}")
                    _flash(request,f"Generování obrázku je ve frontě #{jid}.")
                elif action == "generate_carousel":
                    slides=json.loads(str(p.get("carousel_slides") or "[]")) if p.get("carousel_slides") else []
                    count=0
                    if isinstance(slides,list):
                        for idx,slide in enumerate(slides[:8],1):
                            queue_job(uid,selected,"carousel_slide",f"Carousel {idx}: {p.get('title')}",{"post_id":post_id,"slide_index":idx,"slide":slide},worker_group="media",priority=55,notify_user=False,dedupe_key=f"carousel:{post_id}:{idx}");count+=1
                    _flash(request,f"Do fronty přidáno {count} slidů.")
                elif action == "regenerate_link":
                    url=regenerate_post_link(uid,post_id);_flash(request,f"Nový cílový odkaz: {url}")
            except Exception as exc:
                _flash(request,f"Akce se nepodařila: {exc}")
            return _redirect(page,f"&project_id={selected}" if selected else "")
        content_view = str(request.query_params.get("view") or ("approval" if page=="approvals" else "all"))
        data=content_rows(uid,selected or None,250)
        if page=="approvals" or (page=="content" and content_view=="approval"):
            data=[x for x in data if str(x.get("status")) in {"draft","ready"}]
        article_data = blog_rows(uid,selected or None,120) if page!="calendar" else []
        if page=="content":
            if content_view=="articles":
                data=[]
            elif content_view=="posts":
                article_data=[]
            elif content_view=="approval":
                article_data=[x for x in article_data if str(x.get("status") or "") in {"draft","ready"}]
        calendar_platforms: list[str] = []
        active_networks: list[str] = []
        calendar_dates: list[Any] = []
        calendar_matrix: dict[str, dict[str, list[dict[str, Any]]]] = {}
        overdue_count = 0
        calendar_scope = str(request.query_params.get("scope") or "14d")
        if page=="calendar":
            all_scheduled=sorted([x for x in data if x.get("scheduled_at")],key=lambda x:x.get("scheduled_at") or datetime.max)
            today=datetime.now().replace(hour=0,minute=0,second=0,microsecond=0)
            overdue_count=sum(1 for x in all_scheduled if x.get("scheduled_at") and x.get("scheduled_at")<today and not x.get("published_at") and str(x.get("status") or "") != "published")
            connected = connected_platforms(uid, selected) if selected else []
            from_posts = [str(x.get("platform") or "") for x in all_scheduled if x.get("platform")]
            preferred = ["facebook","instagram","linkedin","google_business","x","tiktok","blog"]
            calendar_platforms = [x for x in preferred if x in set(connected + from_posts)]
            for x in connected + from_posts:
                if x and x not in calendar_platforms:
                    calendar_platforms.append(x)
            requested = [str(x) for x in request.query_params.getlist("network") if str(x)]
            active_networks = [x for x in requested if x in calendar_platforms] or list(calendar_platforms)
            if calendar_scope == "all":
                visible=all_scheduled
            else:
                end=today+timedelta(days=14)
                visible=[x for x in all_scheduled if x.get("scheduled_at") and today <= x.get("scheduled_at") < end]
            filtered = [x for x in visible if str(x.get("platform") or "") in active_networks]
            for row in filtered:
                dt=row.get("scheduled_at"); key=dt.strftime("%Y-%m-%d") if dt else ""
                if not key: continue
                calendar_matrix.setdefault(key,{}); calendar_matrix[key].setdefault(str(row.get("platform") or "other"),[]).append(row)
            if calendar_scope == "all":
                calendar_dates=sorted(calendar_matrix.keys())
            else:
                calendar_dates=[(today+timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14)]
            data=filtered
        post_jobs = _active_post_jobs(uid, selected or None)
        _decorate_post_jobs(data, post_jobs)
        if page == "calendar":
            for day_map in calendar_matrix.values():
                for slot_rows in day_map.values():
                    _decorate_post_jobs(slot_rows, post_jobs)
        template="calendar.html" if page=="calendar" else "content.html"
        approval_mode = page=="approvals" or (page=="content" and content_view=="approval")
        approval_blogs = article_data if approval_mode else []
        return _render(request,template,{"content":"Obsah","approvals":"Obsah","calendar":"Kalendář"}[page],page,rows=data,articles=article_data,content_view=content_view,projects=project_options(uid),selected_project=selected,approval_mode=approval_mode,approval_blogs=approval_blogs,calendar_platforms=calendar_platforms,active_networks=active_networks,calendar_dates=calendar_dates,calendar_matrix=calendar_matrix,overdue_count=overdue_count,calendar_scope=calendar_scope,now_date=datetime.now().strftime("%Y-%m-%d"))

    if page == "blog":
        selected=int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        if request.method=="POST":
            form=await request.form();verify_csrf(request,str(form.get("csrf") or ""));action=str(form.get("action") or "");bid=int(form.get("blog_id") or 0);b=blog_row(uid,bid)
            if not b:_flash(request,"Článek nenalezen.");return _redirect("blog")
            selected=int(b.get("project_id") or selected)
            try:
                if action=="edit_blog":
                    update_blog(uid,bid,{"title":str(form.get("title") or ""),"slug":str(form.get("slug") or ""),"excerpt":str(form.get("excerpt") or ""),"body":str(form.get("body") or ""),"seo_title":str(form.get("seo_title") or ""),"seo_description":str(form.get("seo_description") or ""),"social_teaser":str(form.get("social_teaser") or ""),"image_prompt":str(form.get("image_prompt") or ""),"wp_category_id":int(form.get("wp_category_id") or 0) or None,"wp_category_name":str(form.get("wp_category_name") or "") or None});_flash(request,"Článek uložen.")
                elif action=="publish_blog":
                    jid=queue_job(uid,selected,"wp_publish_blog",f"WordPress: {b.get('title')}",{"blog_id":bid,"force_publish":True},worker_group="publication",priority=90,notify_user=True,dedupe_key=f"wp-publish:blog:{bid}");_flash(request,f"Publikace článku je ve frontě #{jid}.")
                elif action=="queue_web":
                    jid=queue_job(uid,selected,"wp_publish_blog",f"WordPress draft: {b.get('title')}",{"blog_id":bid,"force_publish":False},worker_group="publication",priority=80,notify_user=True,dedupe_key=f"wp-draft:blog:{bid}");_flash(request,f"Uložení do WordPressu je ve frontě #{jid}.")
                elif action=="regenerate_blog_links":
                    links=refresh_blog_links(uid,bid);_flash(request,f"Interní odkazy obnoveny: {len(links)}.")
                elif action=="generate_image":
                    jid=queue_job(uid,selected,"image_generate_blog",f"Náhled článku: {b.get('title')}",{"blog_id":bid},worker_group="media",priority=60,notify_user=True,dedupe_key=f"image:blog:{bid}");_flash(request,f"Obrázek je ve frontě #{jid}.")
            except Exception as exc:_flash(request,f"Článek: {exc}")
            return _redirect("blog",f"&project_id={selected}")
        return _render(request,"articles.html","Články",page,rows=blog_rows(uid,selected or None,200),projects=project_options(uid),selected_project=selected)

    if page == "knowledge":
        selected = int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        if request.method=="POST":
            form=await request.form();verify_csrf(request,str(form.get("csrf") or ""));action=str(form.get("action") or "");pid=int(form.get("project_id") or selected)
            if not project(uid,pid):_flash(request,"Projekt nenalezen.");return _redirect("knowledge")
            if action in {"wordpress","load_wp_data","sync"}:
                jid=queue_job(uid,pid,"wordpress_sync","Synchronizuji znalosti z webu",{"manual":True},worker_group="maintenance",priority=50,notify_user=True,dedupe_key=f"wordpress_sync:{pid}");_flash(request,f"Synchronizace je ve frontě #{jid}.")
            elif action in {"profile","refresh_profile"}:
                jid=queue_job(uid,pid,"project_profile","Linda aktualizuje profil firmy",{"manual":True},worker_group="text",priority=80,notify_user=True,dedupe_key=f"project_profile:{pid}");_flash(request,f"Profil je ve frontě #{jid}.")
            return _redirect("knowledge",f"&project_id={pid}")
        return _render(request,"knowledge.html","Znalostní báze",page,rows=knowledge_rows(uid,selected or None,250),projects=project_options(uid),selected_project=selected)

    if page in {"media", "images"}:
        selected=int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        if request.method=="POST":
            form=await request.form(); verify_csrf(request,str(form.get("csrf") or "")); pid=int(form.get("project_id") or selected); action=str(form.get("action") or "generate")
            if not project(uid,pid):
                _flash(request,"Vyber platný projekt."); return _redirect("media")
            if action=="upload":
                upload=form.get("file"); title=str(form.get("title") or getattr(upload,"filename","") or "Nahrané médium").strip()
                try:
                    path=await _save_upload(upload,"user-media",120*1024*1024)
                    suffix=Path(path).suffix.lower()
                    kind="user_video" if suffix in {".mp4",".mov",".m4v",".webm",".avi"} else "user_image"
                    with db() as conn:
                        ins=conn.execute(text("INSERT INTO media_assets(project_id,prompt,file_path,status,kind,title,provider) VALUES(:pid,:prompt,:path,'generated',:kind,:title,'upload')"),{"pid":pid,"prompt":"Uživatelský upload","path":path,"kind":kind,"title":title[:190]}); asset_id=int(ins.lastrowid)
                    media_path=resolve_upload_path(path,allow_scan=False)
                    if kind=="user_image" and media_path:
                        thumbnail_path(asset_id,media_path)
                    _flash(request,f"Médium #{asset_id} je v knihovně, optimalizované a připravené k použití.")
                except Exception as exc:
                    _flash(request,f"Nahrání média: {exc}")
                return _redirect("media",f"&project_id={pid}")
            title=str(form.get("title") or "Volná kreativita").strip(); prompt=str(form.get("prompt") or "").strip()
            if not prompt:
                _flash(request,"Napiš zadání obrázku."); return _redirect("media",f"&project_id={pid}")
            with db() as conn:
                ins=conn.execute(text("INSERT INTO media_assets(project_id,prompt,status,kind,title,provider) VALUES(:pid,:prompt,'prompt','free_image',:title,'fresh-ai-engine')"),{"pid":pid,"prompt":prompt,"title":title[:190]}); asset_id=int(ins.lastrowid)
            jid=queue_job(uid,pid,"free_image_generate",f"Obrázek: {title}",{"asset_id":asset_id},worker_group="media",priority=60,notify_user=True,dedupe_key=f"free-image:{asset_id}"); _flash(request,f"Obrázek je ve frontě #{jid}."); return _redirect("media",f"&project_id={pid}")
        return _render(request,"media.html","Média",page,rows=media_rows(uid,selected or None,240),projects=project_options(uid),selected_project=selected)

    if page == "references":
        selected=int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        if request.method=="POST":
            form=await request.form();verify_csrf(request,str(form.get("csrf") or ""));action=str(form.get("action") or "")
            if action=="upload":
                pid=int(form.get("project_id") or selected)
                try:
                    upload=form.get("file");path=await _save_upload(upload,"references");rid=save_reference(uid,pid,str(form.get("title") or getattr(upload,"filename","") or "Reference"),str(form.get("notes") or ""),path);_flash(request,f"Reference #{rid} nahrána.")
                except Exception as exc:_flash(request,f"Nahrání reference: {exc}")
                return _redirect("references",f"&project_id={pid}" if pid else "")
            if action=="delete":
                rid=int(form.get("reference_id") or 0);ref=delete_reference(uid,rid)
                if ref:
                    target=_safe_upload_path(str(ref.get("file_path") or ""))
                    try:
                        if target:target.unlink()
                    except Exception:pass
                    _flash(request,"Reference smazána.")
                return _redirect("references",f"&project_id={selected}" if selected else "")
            if action=="analyze_brand":
                pid=int(form.get("project_id") or selected);jid=queue_job(uid,pid,"brand_analyze","Linda analyzuje Brand kit",{},worker_group="media",priority=75,notify_user=True,dedupe_key=f"brand_analyze:{pid}");_flash(request,f"Brand analýza je ve frontě #{jid}.");return _redirect("references",f"&project_id={pid}")
        return _render(request,"references.html","Brand kit",page,rows=reference_rows(uid,selected or None,200),projects=project_options(uid),selected_project=selected)

    if page == "analytics":
        selected=int(request.query_params.get("project_id") or request.session.get("current_project_id") or 0)
        if request.method=="POST":
            form=await request.form();verify_csrf(request,str(form.get("csrf") or ""));pid=int(form.get("project_id") or selected)
            jid=queue_job(uid,pid,"analytics_sync","Synchronizuji analytiku",{},worker_group="maintenance",priority=30,notify_user=True,dedupe_key=f"analytics:{pid}");_flash(request,f"Analytika je ve frontě #{jid}.");return _redirect("analytics",f"&project_id={pid}")
        return _render(request,"analytics.html","Analytika",page,rows=recent_metrics(uid,selected or None),projects=project_options(uid),selected_project=selected)

    if page == "notifications":
        if request.method=="POST":
            form=await request.form();verify_csrf(request,str(form.get("csrf") or ""));mark_notifications_read(uid);_flash(request,"Upozornění označena jako přečtená.");return _redirect("notifications")
        return _render(request,"notifications.html","Upozornění",page,rows=notification_rows(uid))

    if page == "account":
        if request.method=="POST":
            form=await request.form();verify_csrf(request,str(form.get("csrf") or ""))
            if str(form.get("account_action") or "profile") == "notifications":
                save_notification_preferences(uid,{
                    "email_group_done":1 if form.get("email_group_done") else 0,
                    "email_failed":1 if form.get("email_failed") else 0,
                    "email_plan_ready":1 if form.get("email_plan_ready") else 0,
                    "email_media_batch":1 if form.get("email_media_batch") else 0,
                })
                _flash(request,"Nastavení oznámení uloženo.");return _redirect("account")
            name=str(form.get("name") or "").strip();email=str(form.get("email") or "").strip().lower();password=str(form.get("password") or "")
            if not name or not email:_flash(request,"Doplň jméno a e-mail.");return _redirect("account")
            with db() as conn:
                if password:
                    if len(password)<8:_flash(request,"Nové heslo musí mít alespoň 8 znaků.");return _redirect("account")
                    conn.execute(text("UPDATE users SET name=:name,email=:email,password_hash=:hash WHERE id=:id"),{"name":name,"email":email,"hash":hash_password(password),"id":uid})
                else:conn.execute(text("UPDATE users SET name=:name,email=:email WHERE id=:id"),{"name":name,"email":email,"id":uid})
            request.session["user"].update({"name":name,"email":email});_flash(request,"Účet uložen.");return _redirect("account")
        return _render(request,"account.html","Účet",page,account=user_by_id(uid),preferences=notification_preferences(uid))

    if page == "impersonate":
        if not is_superadmin(user):raise HTTPException(status_code=403,detail="Superadmin only")
        target=user_by_id(int(request.query_params.get("id") or 0))
        if target:
            request.session["actor_user"]=dict(user);request.session["user"]={"id":int(target["id"]),"name":str(target["name"]),"email":str(target["email"]),"role":str(target["role"])}
        return _redirect("dashboard")

    if page == "stop_impersonate":
        actor=request.session.pop("actor_user",None)
        if actor:request.session["user"]=actor
        return _redirect("users")

    if page == "users":
        if not is_superadmin(user):raise HTTPException(status_code=403, detail="Superadmin only")
        if request.method=="POST":
            form=await request.form();verify_csrf(request,str(form.get("csrf") or ""));action=str(form.get("action") or "");target=int(form.get("user_id") or 0)
            if action=="create":
                name=str(form.get("name") or "").strip();email=str(form.get("email") or "").strip().lower();password=str(form.get("password") or "");role=str(form.get("role") or "user")
                if name and email and len(password)>=8:
                    with db() as conn:conn.execute(text("INSERT INTO users(name,email,password_hash,role,status,created_by) VALUES(:name,:email,:hash,:role,'active',:creator)"),{"name":name,"email":email,"hash":hash_password(password),"role":role if role in {"user","admin","superadmin"} else "user","creator":uid});_flash(request,"Uživatel vytvořen.")
                else:_flash(request,"Vyplň jméno, e-mail a heslo min. 8 znaků.")
            elif target:
                if action=="toggle_status" and target!=uid:
                    with db() as conn:conn.execute(text("UPDATE users SET status=IF(status='active','inactive','active') WHERE id=:id"),{"id":target})
                elif action=="set_role":
                    role=str(form.get("role") or "user");
                    if role in {"user","admin","superadmin"}:
                        with db() as conn:conn.execute(text("UPDATE users SET role=:role WHERE id=:id"),{"role":role,"id":target})
                elif action=="reset_password":
                    pw=str(form.get("password") or "");
                    if len(pw)>=8:
                        with db() as conn:conn.execute(text("UPDATE users SET password_hash=:hash WHERE id=:id"),{"hash":hash_password(pw),"id":target})
            return _redirect("users")
        data=rows("SELECT id,name,email,role,status,created_at FROM users ORDER BY id DESC")
        return _render(request,"users.html","Správa účtů",page,rows=data)

    if page == "about":
        return _render(request, "about.html", "O aplikaci", page)

    if page in {"wizard", "creator"}:
        return _redirect("linda", f"&project_id={request.query_params.get('project_id')}" if request.query_params.get("project_id") else "")

    return HTMLResponse("404", status_code=404)
