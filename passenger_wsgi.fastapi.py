import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
os.chdir(APP_ROOT)

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from dotenv import load_dotenv
load_dotenv(APP_ROOT / ".env")

from a2wsgi import ASGIMiddleware
from app.main import app

application = ASGIMiddleware(app)
