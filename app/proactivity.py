from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from .db import db, one, rows
from .workflows import planned_tasks
from .repositories import queue_job


def _meta_get(key: str) -> str:
    row = one("SELECT meta_value FROM app_meta WHERE meta_key=:k LIMIT 1", {"k": key})
    return str((row or {}).get("meta_value") or "")


def _meta_set(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(text(
            "INSERT INTO app_meta(meta_key,meta_value) VALUES(:k,:v) "
            "ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value),updated_at=CURRENT_TIMESTAMP"
        ), {"k": key, "v": value})


def _due(item: dict[str, Any], now: datetime, last: str) -> bool:
    if not item.get("enabled", True):
        return False
    try:
        hour = max(0, min(23, int(item.get("hour") or 0)))
    except Exception:
        hour = 0
    if now.hour < hour:
        return False
    cadence = str(item.get("cadence") or "daily")
    try:
        last_dt = datetime.fromisoformat(last) if last else None
    except Exception:
        last_dt = None
    if cadence == "weekly":
        try:
            weekday = max(0, min(6, int(item.get("weekday") or 0)))
        except Exception:
            weekday = 0
        if now.weekday() != weekday:
            return False
        return not last_dt or (now.date() - last_dt.date()).days >= 6
    return not last_dt or last_dt.date() != now.date()


def enqueue_due_planned_tasks() -> dict[str, Any]:
    now = datetime.now()
    enqueued: list[dict[str, Any]] = []
    projects = rows("SELECT id,user_id,name FROM projects ORDER BY id")
    for item in planned_tasks():
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        configured_pid = int(item.get("project_id") or 0)
        targets = [p for p in projects if not configured_pid or int(p.get("id") or 0) == configured_pid]
        for project in targets:
            pid = int(project.get("id") or 0); uid = int(project.get("user_id") or 0)
            if not pid or not uid:
                continue
            key = f"linda_schedule_last:{item.get('key')}:{pid}"
            if not _due(item, now, _meta_get(key)):
                continue
            # Skip tasks that would inevitably fail when their prerequisite is absent.
            if task in {"analytics_sync", "wordpress_sync"}:
                wp = one("SELECT id FROM social_connectors WHERE project_id=:pid AND user_id=:uid AND provider='wordpress' AND status='active' LIMIT 1", {"pid":pid,"uid":uid})
                if not wp:
                    _meta_set(key, now.isoformat(timespec="seconds")); continue
            if task in {"project_profile"}:
                k = one("SELECT COUNT(*) c FROM project_knowledge WHERE project_id=:pid AND status='active'", {"pid":pid})
                if not k or int(k.get("c") or 0) == 0:
                    _meta_set(key, now.isoformat(timespec="seconds")); continue
            if task in {"brand_analyze"}:
                k = one("SELECT (SELECT COUNT(*) FROM visual_references WHERE project_id=:pid)+(SELECT COUNT(*) FROM project_knowledge WHERE project_id=:pid AND featured_image_url IS NOT NULL AND featured_image_url<>'') c", {"pid":pid})
                if not k or int(k.get("c") or 0) == 0:
                    _meta_set(key, now.isoformat(timespec="seconds")); continue
            group = {
                "analytics_sync":"maintenance",
                "media_maintenance":"maintenance",
                "project_profile":"text",
                "brand_analyze":"media",
                "proactive_review":"maintenance",
            }.get(task, "maintenance")
            title = str(item.get("label") or "Plánovaná úloha Lindy")
            jid = queue_job(uid, pid if task != "media_maintenance" else pid, task, title,
                            {"scheduled":True,"schedule_key":str(item.get("key") or "")},
                            worker_group=group, priority=20 if task != "proactive_review" else 35,
                            notify_user=task not in {"media_maintenance"},
                            dedupe_key=f"schedule:{item.get('key')}:{pid}:{now.strftime('%Y%m%d')}")
            _meta_set(key, now.isoformat(timespec="seconds"))
            enqueued.append({"job_id":jid,"project_id":pid,"task":task,"label":title})
    return {"count":len(enqueued),"items":enqueued}
