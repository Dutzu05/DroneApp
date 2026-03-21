from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import unquote
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / ".data"
SESSION_SECRET_FILE = DATA_DIR / "session_secret"
SESSION_COOKIE_NAME = "drone_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}


def _environment_name() -> str:
    return (os.environ.get("DRONE_ENV") or "development").strip().lower() or "development"


def _configured_session_secret() -> bytes | None:
    raw_value = os.environ.get("DRONE_SESSION_SECRET")
    if raw_value is None or not raw_value.strip():
        return None
    return raw_value.strip().encode("utf-8")


def _load_session_secret() -> bytes:
    configured_secret = _configured_session_secret()
    if configured_secret is not None:
        return configured_secret

    if _environment_name() == "production":
        raise RuntimeError("DRONE_SESSION_SECRET is required when DRONE_ENV=production")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SESSION_SECRET_FILE.exists():
        return SESSION_SECRET_FILE.read_bytes()

    secret = secrets.token_bytes(32)
    SESSION_SECRET_FILE.write_bytes(secret)
    return secret


def create_session_token(user: dict[str, Any], *, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    now = int(time.time())
    payload = {
        "email": (user.get("email") or "").strip().lower(),
        "display_name": user.get("display_name") or "",
        "google_user_id": user.get("google_user_id") or "",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_token = _b64url_encode(payload_raw)
    signature = hmac.new(_load_session_secret(), payload_token.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_token}.{_b64url_encode(signature)}"


def decode_session_token(token: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None

    payload_part, sig_part = token.split(".", 1)
    expected_sig = _b64url_encode(
        hmac.new(_load_session_secret(), payload_part.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig_part, expected_sig):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None
    if not payload.get("email"):
        return None
    return payload


def _cookie_attributes(*, max_age: int) -> str:
    attributes = [
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    cookie_domain = (os.environ.get("DRONE_COOKIE_DOMAIN") or "").strip()
    if cookie_domain:
        attributes.append(f"Domain={cookie_domain}")
    if _env_flag("DRONE_COOKIE_SECURE", default=_environment_name() == "production"):
        attributes.append("Secure")
    return "; ".join(attributes)


def session_cookie_header(token: str, *, max_age: int = SESSION_TTL_SECONDS) -> str:
    return f"{SESSION_COOKIE_NAME}={token}; {_cookie_attributes(max_age=max_age)}"


def clear_session_cookie_header() -> str:
    return f"{SESSION_COOKIE_NAME}=; {_cookie_attributes(max_age=0)}"


def _extract_cookie_value(raw_cookie: str, cookie_name: str) -> str | None:
    for chunk in raw_cookie.split(";"):
        part = chunk.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == cookie_name:
            return unquote(value.strip())
    return None


def session_user_from_headers(headers: Any) -> dict[str, Any] | None:
    raw_cookie = headers.get("Cookie") if headers else None
    if not raw_cookie:
        return None

    token = _extract_cookie_value(raw_cookie, SESSION_COOKIE_NAME)
    if token:
        return decode_session_token(token)

    cookie = SimpleCookie()
    cookie.load(raw_cookie)
    morsel = cookie.get(SESSION_COOKIE_NAME)
    if morsel is None:
        return None
    return decode_session_token(morsel.value)
