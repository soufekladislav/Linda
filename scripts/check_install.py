from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.config import settings
from app.db import ping, schema_report
from app.engine import engine_client
from app.media_tools import ffmpeg_available

security = {
    "session_secret": bool(settings.session_secret and not settings.session_secret.startswith("CHANGE_ME")),
    "cron_token": bool(settings.cron_token and not settings.cron_token.startswith("CHANGE_ME")),
    "developer_token": bool(settings.developer_token and not settings.developer_token.startswith("CHANGE_ME")),
    "media_signing_key": bool(settings.media_signing_key and not settings.media_signing_key.startswith("CHANGE_ME")),
    "engine_api_key": bool(settings.engine_api_key),
}
try:
    import PIL  # type: ignore
    pillow_ok = True
except Exception:
    pillow_ok = False

security["pillow"] = pillow_ok

result = {
    "app": "Linda",
    "version": settings.app_version,
    "database": ping(),
    "schema": schema_report(),
    "engine": engine_client.health(),
    "configuration": security,
    "capabilities": {"ffmpeg": ffmpeg_available(), "video_optimization": "enabled_if_ffmpeg_available"},
}
result["ok"] = bool(
    result["database"].get("ok")
    and result["schema"].get("ok")
    and all(security.values())
)
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
raise SystemExit(0 if result["ok"] else 1)
