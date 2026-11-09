import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
os.chdir(APP_ROOT)
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from dotenv import load_dotenv
load_dotenv(APP_ROOT / ".env")

_adapter = None
_adapter_pid = None

def application(environ, start_response):
    global _adapter, _adapter_pid
    current_pid = os.getpid()
    if _adapter is None or _adapter_pid != current_pid:
        from a2wsgi import ASGIMiddleware
        from app.main import app
        _adapter = ASGIMiddleware(app, wait_time=5.0)
        _adapter_pid = current_pid
    return _adapter(environ, start_response)
