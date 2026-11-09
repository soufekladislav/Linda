from __future__ import annotations

import json
import os
import shutil
import resource
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import settings
from .db import ping as db_ping, rows, schema_report
from .engine import engine_client
from . import fresh_key
from .media_tools import UPLOAD_ROOT, resolve_upload_path, ffmpeg_available, pillow_available
from .workers import SUPPORTED_JOB_TYPES, TASK_ROUTING
from .workflows import planned_tasks




def _json_safe(value: Any) -> Any:
    """Convert DB/runtime values to strict JSON-compatible primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _runtime_report(root: Path) -> dict[str, Any]:
    try:
        disk = shutil.disk_usage(root)
        disk_data = {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free, "used_percent": round((disk.used / disk.total * 100), 2) if disk.total else 0}
    except Exception as exc:
        disk_data = {"error": str(exc)}
    try:
        loads = os.getloadavg()
        load_data = {"load_1m": loads[0], "load_5m": loads[1], "load_15m": loads[2], "cpu_count": os.cpu_count() or 0}
    except Exception as exc:
        load_data = {"error": str(exc)}
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        own_rss_kb = int(getattr(usage, "ru_maxrss", 0) or 0)
    except Exception:
        own_rss_kb = 0
    uid = os.getuid() if hasattr(os, "getuid") else None
    proc_count = 0
    rss_total_kb = 0
    linda_processes = 0
    process_groups: dict[str, int] = defaultdict(int)
    process_examples: list[dict[str, Any]] = []
    proc_root = Path('/proc')
    if uid is not None and proc_root.exists():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                status = (entry / 'status').read_text(errors='ignore')
                uid_line = next((x for x in status.splitlines() if x.startswith('Uid:')), '')
                if not uid_line:
                    continue
                real_uid = int(uid_line.split()[1])
                if real_uid != uid:
                    continue
                proc_count += 1
                rss_kb = 0
                rss_line = next((x for x in status.splitlines() if x.startswith('VmRSS:')), '')
                if rss_line:
                    rss_kb = int(rss_line.split()[1]); rss_total_kb += rss_kb
                cmd_raw = (entry / 'cmdline').read_bytes().replace(b'\x00', b' ').decode(errors='ignore').strip()
                cmd = cmd_raw or next((x.split(':',1)[1].strip() for x in status.splitlines() if x.startswith('Name:')), '')
                low = cmd.lower()
                if 'socialmedia.freshcrackers.cz' in low:
                    group = 'Linda'
                    linda_processes += 1
                elif 'aiengine.freshcrackers.cz' in low:
                    group = 'AI Engine'
                elif 'ai-coder.freshcrackers.cz' in low:
                    group = 'Karel'
                elif 'chat.freshcrackers.cz' in low:
                    group = 'Fresh AI Chat'
                elif 'webbooster.freshcrackers.cz' in low:
                    group = 'Web Booster'
                elif 'finance.freshcrackers.cz' in low:
                    group = 'Eda'
                elif 'lsphp' in low or 'php' in low:
                    group = 'PHP / WordPress'
                elif 'bash' in low or '/sh' in low:
                    group = 'Shell'
                else:
                    group = 'Ostatní'
                process_groups[group] += 1
                if len(process_examples) < 30:
                    process_examples.append({"pid": int(entry.name), "group": group, "rss_kb": rss_kb, "command": cmd[:240]})
            except Exception:
                continue
    return {
        "pid": os.getpid(),
        "user_processes_visible": proc_count,
        "linda_processes_visible": linda_processes,
        "user_processes_note": "Počet zahrnuje celý hostingový účet, nikoli počet úloh Lindy.",
        "process_groups": dict(process_groups),
        "process_examples": process_examples,
        "user_rss_kb_visible": rss_total_kb,
        "process_max_rss_kb": own_rss_kb,
        "load": load_data,
        "disk": disk_data,
        "ffmpeg_available": ffmpeg_available(),
    }

def _sha(path: Path) -> str | None:
    try:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None



def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            lk = str(k).lower()
            if any(word in lk for word in ("secret", "password", "api_key", "access_token", "refresh_token", "authorization")):
                out[k] = "<redacted>"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(x) for x in value]
    return value

def _safe_rows(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        return rows(sql, params or {})
    except Exception:
        return []


def _storage_report(root: Path) -> dict[str, Any]:
    base = UPLOAD_ROOT
    all_files = [p for p in base.rglob("*") if p.is_file() and p.name != ".gitkeep"] if base.exists() else []
    originals = [p for p in all_files if ".thumbs" not in p.parts]
    thumbs = [p for p in all_files if ".thumbs" in p.parts]
    basename_index: dict[str, Path] = {}
    for path in originals:
        basename_index.setdefault(path.name.lower(), path)
    ext_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
    for path in originals:
        ext = path.suffix.lower().lstrip(".") or "none"
        ext_stats[ext]["files"] += 1
        try:
            ext_stats[ext]["bytes"] += path.stat().st_size
        except Exception:
            pass

    media = _safe_rows(
        "SELECT id,project_id,post_id,blog_post_id,kind,provider,status,file_path FROM media_assets "
        "WHERE file_path IS NOT NULL AND file_path<>'' ORDER BY id DESC"
    )
    refs = _safe_rows("SELECT id,project_id,file_path FROM visual_references WHERE file_path IS NOT NULL AND file_path<>'' ORDER BY id DESC")
    missing: list[dict[str, Any]] = []
    resolved = 0
    for item in media:
        target = resolve_upload_path(str(item.get("file_path") or ""), allow_scan=False) or basename_index.get(Path(str(item.get("file_path") or "")).name.lower())
        if target:
            resolved += 1
        elif len(missing) < 30:
            missing.append({
                "media_id": item.get("id"),
                "project_id": item.get("project_id"),
                "post_id": item.get("post_id"),
                "blog_post_id": item.get("blog_post_id"),
                "kind": item.get("kind"),
                "provider": item.get("provider"),
                "stored_path": item.get("file_path"),
            })
    missing_refs: list[dict[str, Any]] = []
    for item in refs:
        if not (resolve_upload_path(str(item.get("file_path") or ""), allow_scan=False) or basename_index.get(Path(str(item.get("file_path") or "")).name.lower())) and len(missing_refs) < 20:
            missing_refs.append({"reference_id": item.get("id"), "project_id": item.get("project_id"), "stored_path": item.get("file_path")})

    largest = []
    for path in sorted(originals, key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)[:15]:
        try:
            largest.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "extension": path.suffix.lower()})
        except Exception:
            pass

    binding = _safe_rows(
        "SELECT "
        "(SELECT COUNT(*) FROM media_assets) media_total,"
        "(SELECT COUNT(*) FROM media_assets WHERE post_id IS NOT NULL) bound_to_posts,"
        "(SELECT COUNT(*) FROM media_assets WHERE blog_post_id IS NOT NULL) bound_to_articles,"
        "(SELECT COUNT(*) FROM media_assets WHERE post_id IS NULL AND blog_post_id IS NULL) free_assets,"
        "(SELECT COUNT(*) FROM posts p WHERE NOT EXISTS(SELECT 1 FROM media_assets m WHERE m.post_id=p.id AND m.file_path IS NOT NULL AND m.file_path<>'')) posts_without_media,"
        "(SELECT COUNT(*) FROM posts p WHERE EXISTS(SELECT 1 FROM media_assets m WHERE m.post_id=p.id AND m.file_path IS NOT NULL AND m.file_path<>'')) posts_with_media"
    )

    maintenance_meta = {str(x.get("meta_key")): str(x.get("meta_value") or "") for x in _safe_rows(
        "SELECT meta_key,meta_value FROM app_meta WHERE meta_key IN ("
        "'media_maintenance_baseline_bytes','media_maintenance_current_bytes','media_maintenance_last_saved_bytes',"
        "'media_maintenance_saved_total','media_maintenance_optimized_total','media_maintenance_remaining_candidates','media_maintenance_last_run')"
    )}

    projects = _safe_rows(
        "SELECT pr.id,pr.name,"
        "(SELECT COUNT(*) FROM posts p WHERE p.project_id=pr.id) posts,"
        "(SELECT COUNT(*) FROM blog_posts b WHERE b.project_id=pr.id) articles,"
        "(SELECT COUNT(*) FROM media_assets m WHERE m.project_id=pr.id) media_assets,"
        "(SELECT COUNT(*) FROM social_connectors c WHERE c.project_id=pr.id AND c.status IN ('connected','configured')) connected_connectors "
        "FROM projects pr ORDER BY pr.id"
    )

    return {
        "path": "storage/uploads",
        "exists": base.exists(),
        "files_on_disk": len(originals),
        "bytes_on_disk": sum(p.stat().st_size for p in originals if p.exists()),
        "thumbnail_files": len(thumbs),
        "thumbnail_bytes": sum(p.stat().st_size for p in thumbs if p.exists()),
        "by_extension": dict(sorted(ext_stats.items())),
        "largest_files": largest,
        "db_media_paths": len(media),
        "db_reference_paths": len(refs),
        "resolved_media_paths": resolved,
        "missing_referenced_files": max(0, len(media) - resolved),
        "missing_media_examples": missing,
        "missing_reference_examples": missing_refs,
        "bindings": binding[0] if binding else {},
        "projects": projects,
        "routes": {
            "private": "?page=file&type=media|reference&id=...&variant=thumb",
            "public": "?page=public_media&id=...&sig=...",
        },
        "maintenance": {
            "baseline_bytes": int(maintenance_meta.get("media_maintenance_baseline_bytes") or 0),
            "current_bytes": int(maintenance_meta.get("media_maintenance_current_bytes") or 0) or sum(p.stat().st_size for p in originals if p.exists()),
            "saved_bytes_total": int(maintenance_meta.get("media_maintenance_saved_total") or 0),
            "last_saved_bytes": int(maintenance_meta.get("media_maintenance_last_saved_bytes") or 0),
            "optimized_files_total": int(maintenance_meta.get("media_maintenance_optimized_total") or 0),
            "remaining_candidates": int(maintenance_meta.get("media_maintenance_remaining_candidates") or 0),
            "last_run": maintenance_meta.get("media_maintenance_last_run") or None,
            "next_scheduled": "daily 03:00 Europe/Prague",
        },
        "optimization": {
            "enabled": settings.media_optimize_images,
            "quality": settings.media_image_quality,
            "max_dimension": settings.media_max_dimension,
            "thumbnail_dimension": settings.media_thumbnail_dimension,
            "new_generated_images": "JPEG by default; optimized PNG only when alpha is required",
            "existing_assets": "automatic bounded maintenance batches; originals replaced only when optimized output is smaller",
            "video_optimization": "ffmpeg H.264 MP4 when ffmpeg is available",
            "ffmpeg_available": ffmpeg_available(),
        },
    }


def _ai_report() -> dict[str, Any]:
    recent = _safe_rows(
        "SELECT e.id,e.project_id,p.name project_name,e.event,e.message,e.context,e.created_at "
        "FROM ai_events e LEFT JOIN projects p ON p.id=e.project_id "
        "ORDER BY e.id DESC LIMIT 40"
    )
    audit: list[dict[str, Any]] = []
    for item in recent:
        ctx: dict[str, Any] = {}
        try:
            parsed = json.loads(str(item.get("context") or "{}"))
            if isinstance(parsed, dict):
                ctx = parsed
        except Exception:
            pass
        eng = ctx.get("engine") if isinstance(ctx.get("engine"), dict) else {}
        audit.append({
            "id": item.get("id"),
            "project_id": item.get("project_id"),
            "project": item.get("project_name"),
            "event": item.get("event"),
            "created_at": str(item.get("created_at") or ""),
            "message": str(item.get("message") or "")[:240],
            "engine": {
                "run_id": eng.get("run_id"),
                "task_type": eng.get("task_type"),
                "operation": eng.get("operation"),
                "provider": eng.get("provider"),
                "model": eng.get("model"),
                "status": eng.get("status"),
                "http_status": eng.get("http_status"),
                "input_tokens": eng.get("input_tokens"),
                "output_tokens": eng.get("output_tokens"),
                "credits_charged": eng.get("credits_charged"),
                "latency_ms": eng.get("latency_ms"),
                "fallback_used": eng.get("fallback_used"),
                "error": eng.get("error"),
            } if eng else None,
            "diagnostic": {
                "error": str(ctx.get("error") or "")[:1200] or None,
                "attempts": _redact(ctx.get("attempts") or []),
            } if (ctx.get("error") or ctx.get("attempts")) else None,
        })

    chat_jobs = _safe_rows("SELECT status,COUNT(*) c FROM task_queue WHERE type='linda_chat' GROUP BY status")
    last_chat = _safe_rows(
        "SELECT event,created_at,message FROM ai_events WHERE event IN ('chat_user','chat_linda','chat_error') ORDER BY id DESC LIMIT 8"
    )
    failed = _safe_rows(
        "SELECT id,project_id,type,title,message,attempts,updated_at FROM task_queue WHERE status='failed' ORDER BY id DESC LIMIT 20"
    )
    return {
        "interactive_chat": {
            "mode": "direct_engine_request_no_cron",
            "primary_task_type": "chat",
            "primary_operation": "chat.conversation",
            "fallback_task_type": "general",
            "queued_legacy_chat_jobs": {str(x.get("status")): int(x.get("c") or 0) for x in chat_jobs},
            "recent_chat_events": last_chat,
        },
        "recent_ai_events": audit,
        "recent_failed_jobs": failed,
    }


def _queue_report() -> dict[str, Any]:
    by_status = _safe_rows("SELECT status,COUNT(*) c FROM task_queue GROUP BY status ORDER BY status")
    by_type = _safe_rows("SELECT type,status,COUNT(*) c FROM task_queue GROUP BY type,status ORDER BY type,status")
    stale = _safe_rows("SELECT COUNT(*) c FROM task_queue WHERE status='running' AND COALESCE(locked_at,updated_at)<DATE_SUB(NOW(),INTERVAL 20 MINUTE)")
    oldest = _safe_rows("SELECT id,type,title,worker_group,created_at,updated_at FROM task_queue WHERE status='queued' ORDER BY id ASC LIMIT 5")
    cron = _safe_rows("SELECT meta_key,meta_value,updated_at FROM app_meta WHERE meta_key IN ('linda_cron_last_run','linda_cron_last_worker','linda_cron_last_result')")
    cron_map = {str(x.get("meta_key")): {"value": x.get("meta_value"), "updated_at": x.get("updated_at")} for x in cron}
    last_result_summary = None
    try:
        raw_result = (cron_map.get("linda_cron_last_result") or {}).get("value")
        parsed_result = json.loads(str(raw_result)) if raw_result else None
        if isinstance(parsed_result, dict):
            last_result_summary = {
                "ok": bool(parsed_result.get("ok")),
                "claimed": int(parsed_result.get("claimed") or 0),
                "done": int(parsed_result.get("done") or 0),
                "failed": int(parsed_result.get("failed") or 0),
                "recovered_stale": int(parsed_result.get("recovered_stale") or 0),
            }
    except Exception:
        last_result_summary = None
    last_row = next((x for x in cron if str(x.get('meta_key')) == 'linda_cron_last_run'), None)
    last_dt = (last_row or {}).get('updated_at')
    if isinstance(last_dt, str):
        try: last_dt = datetime.fromisoformat(last_dt)
        except Exception: last_dt = None
    age_seconds = None
    if isinstance(last_dt, datetime):
        try:
            now = datetime.now(last_dt.tzinfo) if last_dt.tzinfo else datetime.now()
            age_seconds = max(0, int((now - last_dt).total_seconds()))
        except Exception:
            age_seconds = None
    if last_dt is None:
        worker_status = 'missing'
    elif age_seconds is not None and age_seconds > 600:
        worker_status = 'stale'
    else:
        worker_status = 'ok'
    return {
        "by_status": {str(x.get("status")): int(x.get("c") or 0) for x in by_status},
        "by_type": by_type,
        "stale_running_over_20m": int(stale[0].get("c") or 0) if stale else 0,
        "oldest_queued": oldest,
        "cron": cron_map,
        "worker_health": {
            "status": worker_status,
            "last_heartbeat": last_dt,
            "age_seconds": age_seconds,
            "expected_interval_seconds": 120,
            "install_command": "python scripts/install_cron.py --install",
        },
        "last_run_summary": last_result_summary,
        "planned_tasks": planned_tasks(),
        "interactive_chat_queue_note": "linda_chat is synchronous; queued legacy linda_chat rows are auto-closed by queue reconciliation",
    }


def build_manifest() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    engine_health = engine_client.health()
    engine_manifest = engine_client.manifest()
    payload = {
        "app": {
            "name": settings.app_name,
            "product": "Linda",
            "version": settings.app_version,
            "runtime": "python-fastapi",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "architecture": {
            "ui": "project-first interactive Linda coworker UX",
            "database": "existing Linda 3.8 database in-place",
            "ai": "Fresh AI Engine only; interactive chat calls Engine immediately",
            "wordpress": "Fresh Core universal connector",
            "fresh_key": "shared identity, project linking, memberships and project connectors in Fresh AI Engine; Linda keeps its own work DB",
            "direct_provider_ai_keys_required": False,
        },
        "runtime": {
            "python": "3.11+",
            "passenger_entrypoint": "passenger_wsgi.py:application",
            "app_url": settings.app_url,
            "resources": _runtime_report(root),
        },
        "database": {
            "health": db_ping(),
            "schema": schema_report(),
            "migration_policy": {
                "auto_ddl": False,
                "destructive_changes": False,
                "preserve_existing_ids": True,
                "preserve_projects": True,
                "preserve_users": True,
                "preserve_content": True,
                "fresh_key_additive_columns": True,
                "local_project_membership_table": True,
            },
        },
        "ai_engine": {
            "base_url": settings.engine_url,
            "health": engine_health,
            "remote_manifest": _redact(engine_manifest),
            "task_types": {
                "router_model": "generic capability in task_type + concrete operation",
                "chat": ["chat", "chat.conversation"],
                "project_profile": ["analysis", "analysis.project_profile"],
                "activity_plan": ["marketing", "marketing.campaign.plan"],
                "article": ["marketing", "marketing.article.create"],
                "social_derivatives": ["marketing", "marketing.social.create"],
                "brand_vision": ["analysis", "analysis.brand_style"],
                "image_generate": ["general", "image.generate"],
            },
            "contract": {
                "endpoint": "/v1/engine/run",
                "auth": "Bearer <app-key>",
                "source": "linda",
                "request_fields": [
                    "source", "project_id", "task_type", "operation", "quality",
                    "prompt", "context", "input_data", "output_contract", "max_cost_usd",
                ],
                "response_normalization": [
                    "run_id", "status", "provider", "model", "result", "result_type",
                    "result_validated", "input_tokens", "output_tokens", "cost", "credits", "latency_ms",
                ],
            },
            "audit": _ai_report(),
        },
        "storage": _storage_report(root),
        "queue": _queue_report(),
        "workers": {
            "supported_now": sorted(SUPPORTED_JOB_TYPES),
            "routing": TASK_ROUTING,
            "interactive_chat": "synchronous; does not require cron",
            "background_groups": ["text", "media", "publication", "maintenance"],
            "cron_compatibility": "?page=cron_worker&token=...&worker=text|media|publication|maintenance",
            "cron_concurrency_lock": "MariaDB GET_LOCK prevents overlapping cron batches",
            "recommended_cli_cron": "*/2 * * * * cd <app-root> && <venv>/bin/python scripts/run_worker.py",
        },
        "fresh_core": {
            "minimum_reviewed_version": "0.4.6",
            "target": "0.5.2",
            "database_mapping": {
                "provider": "wordpress",
                "endpoint_url": "WordPress site root",
                "access_token": "Fresh Core read key",
                "client_secret": "Fresh Core write key",
            },
            "routes_used_from_0_5_x": [
                "/content/export", "/content/preview-url", "/content/status", "/write/content/upsert",
                "/analytics/summary", "/commerce/catalog", "/commerce/product/<id>",
            ],
            "separate_linda_plugin_required": False,
        },
        "ux": {
            "legacy_css_sha256": _sha(root / "assets/css/app.css"),
            "ux042_css_sha256": _sha(root / "assets/css/ux042.css"),
            "legacy_overlay_files_present": ["ux043.css", "ux043.js", "ux044.css", "ux044.js"],
            "ux045_css_sha256": _sha(root / "assets/css/ux045.css"),
            "ux045_js_sha256": _sha(root / "assets/js/ux045.js"),
            "ux046_css_sha256": _sha(root / "assets/css/ux046.css"),
            "ux046_js_sha256": _sha(root / "assets/js/ux046.js"),
            "loaded_css": ["app.css", "ux042.css", "ux045.css", "ux046.css"],
            "loaded_js": ["app.js", "ux042.js", "ux046.js"],
            "linda_image": "https://www.freshcrackers.cz/wp-content/uploads/2026/06/ai-social-media.png",
            "brand": {"green": "#9EC415", "pink": "#E40058", "font": "Poppins"},
            "model": "project-first interactive coworker",
            "project_navigation": ["Linda", "Kalendář", "Obsah", "Média", "Výsledky"],
            "hidden_legacy_views": ["Aktivity", "Články", "Ke schválení"],
            "global_navigation": ["Projekty", "Nastavení", "Admin konzole (superadmin)"],
            "fresh_key_enabled": fresh_key.configured(),
            "social_oauth_owner": "Fresh Key / Fresh AI Engine",
            "legacy_local_meta_oauth_configured": bool(settings.meta_app_id and settings.meta_app_secret),
            "legacy_local_linkedin_oauth_configured": bool(settings.linkedin_client_id and settings.linkedin_client_secret),
            "legacy_local_google_business_oauth_configured": bool(settings.google_business_client_id and settings.google_business_client_secret),
        },
        "feature_parity": {
            "interactive_chat": "implemented_router2_direct_engine_0_4_6_7_with_semantic_status_guard",
            "chat_engine_audit": "implemented",
            "media_path_recovery": "implemented_nested_legacy_resolver",
            "media_thumbnail_cache": "implemented",
            "new_image_optimization": "implemented_strict_validation_atomic_binding_jpeg_png_alpha_thumbnail",
            "expanded_manifest": "implemented_json_safe_media_ai_queue_project_runtime_diagnostics",
            "nonblocking_activity_feedback": "implemented_0_4_4",
            "automatic_media_housekeeping": "implemented_bounded_maintenance_worker_with_savings_metrics",
            "existing_projects": "implemented_crud_safe_delete",
            "calendar": "implemented_today_14d_fullboard_drag_drop_json_endpoint_bulk_reschedule_and_connected_filters",
            "article_workflow": "implemented_article_first",
            "social_publication": "implemented_facebook_instagram_linkedin_google_business_x_tiktok",
            "google_business_profile": "implemented_oauth_test_and_local_posts_requires_google_api_access",
            "navigation_cleanup": "implemented_linda_calendar_content_media_results_primary",
            "articles_and_approvals": "folded_into_content",
            "activities": "hidden_legacy_planning_object",
            "superadmin_console": "implemented_with_all_users_queue_management",
            "floating_notifications": "implemented_polling_dropdown_and_toasts",
            "contextual_help": "implemented_info_popovers",
            "connector_wizard": "implemented_simplified_test_and_wp_autosync",
            "legacy_blog_post_publish": "implemented_direct_fresh_core_upsert",
            "chat_context_budget": "implemented_relevance_first_knowledge_slice",
            "post_editor": "implemented_shared_editor_media_ai_rewrite_link_validation_wp_category",
            "wordpress_featured_image": "implemented_extension_safe_signed_media_url_and_sideload_validation",
            "legacy_web_post_rewrite": "implemented_auto_expand_short_blog_rows_with_safe_content_hold",
            "wordpress_category_selection": "implemented_fresh_core_categories_auto_suggestion_with_editor_override",
            "queue_reconciliation": "implemented_stale_running_recovery_legacy_chat_cleanup_worker_heartbeat_cron_watchdog",
            "engine_semantic_failures": "implemented_http_200_failed_and_invalid_json_guard",
            "publication_dedupe": "implemented_skip_active_and_hold_previous_failed_publication",
            "publication_failure_hold": "implemented_worker_error_and_content_short_holds_manual_retry",
            "worker_health_semantics": "implemented_worker_health_separate_from_business_job_failures",
            "planned_proactivity": "implemented_admin_editable_daily_weekly_tasks",
            "manual_calendar_content": "implemented_topic_to_post_or_article_via_linda",
            "engine_image_diagnostic": "implemented_direct_image_generate_smoke_test",
            "fresh_key_identity": "implemented_central_identity_binding_by_app_and_verified_local_user",
            "fresh_key_projects": "implemented_global_project_binding_and_existing-project_attach",
            "fresh_key_project_members": "read_only_cross_app_overview_local_linda_acl_authoritative",
            "fresh_key_social_connectors": "implemented_meta_linkedin_google_business_project_connections_with_runtime_token_hydration",
            "fresh_key_oauth": "implemented_engine_broker_one_button_start_and_provider_callback_asset_selection",
            "fresh_key_sso": "implemented_verified_central_one_button_identity_login_and_safe_local_binding",
        },
        "security": {
            "provider_ai_keys_in_linda": False,
            "developer_manifest_exposes_secrets": False,
            "legacy_media_routes": True,
            "storage_uploads_must_be_preserved": True,
            "media_signing_key_configured": bool(settings.media_signing_key and not settings.media_signing_key.startswith("CHANGE_ME")),
            "connector_secret_encryption": "Fresh Key OAuth secrets encrypted centrally in AI Engine; Linda legacy secret columns retained only for backward compatibility",
            "pillow_available": pillow_available(),
            "media_pipeline_ready": pillow_available(),
        },
        "known_issues": [
            "Legacy media is optimized only in bounded maintenance batches and only when the replacement is smaller; new uploads are optimized immediately.",
            "FFmpeg is optional; when unavailable uploaded videos are preserved without server-side transcode.",
            "Chat uses Router 2.x generic capability task_type=chat with operation=chat.conversation; failed attempts expose HTTP diagnostics in the manifest.",
            "Direct social publication still requires valid provider permissions/tokens for each connected account; Google Business Profile also requires Google API access for the OAuth client/project.",
            "Legacy manually configured connectors can still contain secrets in social_connectors for backward compatibility; new Fresh Key-managed social connectors store only non-secret pointers in Linda.",
        ],
    }
    return _json_safe(payload)
