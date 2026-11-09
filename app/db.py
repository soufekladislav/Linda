from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine

from .config import settings

engine: Engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    future=True,
)

REQUIRED_TABLES = {
    "users",
    "projects",
    "content_plans",
    "topics",
    "posts",
    "blog_posts",
    "media_assets",
    "visual_references",
    "social_connectors",
    "marketing_activities",
    "project_knowledge",
    "task_queue",
    "ai_events",
    "publication_log",
    "content_metrics",
    "admin_audit_log",
    "action_items",
    "notifications",
    "notification_preferences",
    "app_meta",
}

REQUIRED_38_COLUMNS = {
    "users": {"name", "email", "password_hash", "role", "status"},
    "projects": {"user_id", "name", "ai_profile", "brand_style_dna", "autopilot_mode", "web_publish_mode", "knowledge_synced_at"},
    "social_connectors": {
        "user_id", "project_id", "provider", "access_token", "client_secret", "endpoint_url",
        "last_test_status", "last_preflight_status", "sync_fingerprint",
        "categories_json", "last_content_change_at",
    },
    "project_knowledge": {
        "project_id", "source_type", "url", "title", "summary", "remote_id",
        "content_body", "meta_json", "featured_image_url", "modified_at", "traffic",
        "status", "last_seen_at",
    },
    "blog_posts": {
        "activity_id", "wp_post_id", "destination_url", "featured_media_id",
        "published_at", "wp_category_id", "internal_links_json", "planned_url",
    },
    "posts": {
        "activity_id", "external_id", "permalink", "destination_url",
        "destination_type", "destination_ref_id", "publication_hold",
    },
    "task_queue": {
        "user_id", "project_id", "type", "title", "payload", "status", "message", "progress",
        "worker_group", "priority", "notify_user", "parent_job_id",
        "depends_on_job_id", "attempts", "max_attempts", "available_at",
        "locked_at", "result_json", "dedupe_key",
    },
}


@contextmanager
def db() -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn


def scalar(sql: str, params: dict[str, Any] | None = None, default: Any = None) -> Any:
    with engine.connect() as conn:
        value = conn.execute(text(sql), params or {}).scalar()
        return default if value is None else value


def rows(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(r._mapping) for r in result]


def one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    items = rows(sql, params)
    return items[0] if items else None


def schema_report() -> dict[str, Any]:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    missing_tables = sorted(REQUIRED_TABLES - table_names)

    columns: dict[str, list[str]] = {}
    missing_columns: dict[str, list[str]] = {}
    for table, required in REQUIRED_38_COLUMNS.items():
        if table not in table_names:
            continue
        available = {c["name"] for c in inspector.get_columns(table)}
        columns[table] = sorted(available)
        diff = sorted(required - available)
        if diff:
            missing_columns[table] = diff

    fresh_key_columns = {}
    for table, names in {"users":{"fresh_key_identity_id"},"projects":{"fresh_key_project_id"},"social_connectors":{"fresh_key_connection_id","fresh_key_managed"}}.items():
        if table in table_names:
            available = {c["name"] for c in inspector.get_columns(table)}
            fresh_key_columns[table] = sorted(names & available)

    return {
        "ok": not missing_tables and not missing_columns,
        "mode": "legacy_3_8_in_place",
        "destructive_migrations": False,
        "auto_ddl": False,
        "table_count": len(table_names),
        "required_table_count": len(REQUIRED_TABLES),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "checked_tables": columns,
        "fresh_key_columns": fresh_key_columns,
        "fresh_key_project_members_table": "project_members" in table_names,
    }


def ping() -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar()
            current_db = conn.execute(text("SELECT DATABASE()")).scalar()
        return {"ok": True, "version": version, "database": current_db}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
