from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

import bcrypt
from fastapi import HTTPException, Request, status


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not submitted or not hmac.compare_digest(str(expected), str(submitted)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Neplatný CSRF token.")


def verify_php_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    # PHP bcrypt commonly uses $2y$; Python bcrypt accepts $2b$.
    candidate = stored_hash
    if candidate.startswith("$2y$"):
        candidate = "$2b$" + candidate[4:]
    try:
        return bcrypt.checkpw(password.encode("utf-8"), candidate.encode("utf-8"))
    except Exception:
        return False


def hash_password(password: str) -> str:
    # $2b$ hashes are accepted by PHP password_verify as well.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def current_user(request: Request) -> dict[str, Any] | None:
    user = request.session.get("user")
    return user if isinstance(user, dict) else None


def require_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Přihlášení je vyžadováno.")
    return user


def is_admin(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("role") in {"admin", "superadmin"})


def is_superadmin(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("role") == "superadmin")


def safe_developer_token(value: str, expected: str) -> bool:
    return bool(value and expected and hmac.compare_digest(value, expected))


def media_signature(path: str, expiry: int, key: str) -> str:
    msg = f"{path}|{expiry}".encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()
