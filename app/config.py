from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

# Make .env loading independent of Passenger/uvicorn entrypoint.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _app_version() -> str:
    try:
        value=(Path(__file__).resolve().parents[1] / "version").read_text(encoding="utf-8").strip()
        if value: return value
    except Exception:
        pass
    return _env("APP_VERSION", "0.4.6")


@dataclass(frozen=True)
class Settings:
    app_name: str = _env("APP_NAME", "Fresh AI Social")
    app_version: str = _app_version()
    app_url: str = _env("APP_URL", "https://socialmedia.freshcrackers.cz")
    timezone: str = _env("TIMEZONE", "Europe/Prague")

    session_secret: str = _env("SESSION_SECRET", "CHANGE_ME")
    cron_token: str = _env("CRON_TOKEN", "")
    media_signing_key: str = _env("MEDIA_SIGNING_KEY", "")
    developer_token: str = _env("DEVELOPER_TOKEN", "")
    registration_mode: str = _env("REGISTRATION_MODE", "open")

    engine_url: str = _env("FRESH_ENGINE_URL", "https://aiengine.freshcrackers.cz").rstrip("/")
    engine_api_key: str = _env("FRESH_ENGINE_API_KEY", "")
    engine_timeout_seconds: int = int(_env("FRESH_ENGINE_TIMEOUT_SECONDS", "180") or 180)
    engine_max_cost_usd: float | None = (
        float(_env("FRESH_ENGINE_MAX_COST_USD"))
        if _env("FRESH_ENGINE_MAX_COST_USD")
        else None
    )

    fresh_core_legacy_fallback: bool = _bool("FRESH_CORE_LEGACY_FALLBACK", False)

    media_optimize_images: bool = _bool("MEDIA_OPTIMIZE_IMAGES", True)
    media_image_quality: int = int(_env("MEDIA_IMAGE_QUALITY", "84") or 84)
    media_max_dimension: int = int(_env("MEDIA_MAX_DIMENSION", "1600") or 1600)
    media_thumbnail_dimension: int = int(_env("MEDIA_THUMBNAIL_DIMENSION", "720") or 720)
    media_optimize_videos: bool = _bool("MEDIA_OPTIMIZE_VIDEOS", True)
    media_video_crf: int = int(_env("MEDIA_VIDEO_CRF", "28") or 28)
    media_video_max_dimension: int = int(_env("MEDIA_VIDEO_MAX_DIMENSION", "1920") or 1920)
    media_maintenance_batch: int = int(_env("MEDIA_MAINTENANCE_BATCH", "12") or 12)
    media_maintenance_days: int = int(_env("MEDIA_MAINTENANCE_DAYS", "1") or 1)

    meta_app_id: str = _env("META_APP_ID", "")
    meta_app_secret: str = _env("META_APP_SECRET", "")
    meta_api_version: str = _env("META_API_VERSION", "v24.0")
    linkedin_version: str = _env("LINKEDIN_VERSION", "202609")
    linkedin_client_id: str = _env("LINKEDIN_CLIENT_ID", "")
    linkedin_client_secret: str = _env("LINKEDIN_CLIENT_SECRET", "")
    linkedin_scopes: str = _env("LINKEDIN_SCOPES", "openid profile email w_member_social")
    google_business_client_id: str = _env("GOOGLE_BUSINESS_CLIENT_ID", "")
    google_business_client_secret: str = _env("GOOGLE_BUSINESS_CLIENT_SECRET", "")

    email_notifications: bool = _bool("EMAIL_NOTIFICATIONS", False)
    mail_from: str = _env("MAIL_FROM", "")
    sendmail_path: str = _env("SENDMAIL_PATH", "/usr/sbin/sendmail")

    @property
    def database_url(self) -> str:
        explicit = _env("DATABASE_URL")
        if explicit:
            return explicit
        host = _env("DB_HOST", "localhost")
        port = int(_env("DB_PORT", "3306") or 3306)
        name = _env("DB_NAME", "fresh_ai_social")
        user = quote_plus(_env("DB_USER", "fresh_ai_social"))
        password = quote_plus(_env("DB_PASSWORD"))
        charset = _env("DB_CHARSET", "utf8mb4")
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset={charset}"


settings = Settings()
