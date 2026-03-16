from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from modules.auth.schemas.requests import GoogleSessionRequest


def register_google_session(
    request: GoogleSessionRequest,
    *,
    login_audit_repo,
    app_user_repo,
    session_gateway,
    token_payload_decoder: Callable[[str], dict[str, Any]],
    app_user_upsert_errors: tuple[type[BaseException], ...],
) -> dict[str, Any]:
    token_claims = token_payload_decoder(request.id_token) if request.id_token else {}
    user = {
        "email": (token_claims.get("email") or request.email or "").strip().lower(),
        "display_name": (token_claims.get("name") or request.display_name or "").strip(),
        "google_user_id": (token_claims.get("sub") or request.google_user_id or "").strip(),
    }
    if not user["email"]:
        raise ValueError("email is required")

    login_audit_repo.record_login(
        email=user["email"],
        display_name=user["display_name"],
        google_user_id=user["google_user_id"],
        source_ip=request.source_ip,
        app_name=request.app or "drone_frontend",
        now_utc_iso=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    try:
        app_user_repo.upsert(user, request.app or "drone_frontend")
    except app_user_upsert_errors:
        pass

    return {
        "user": user,
        "set_cookie": session_gateway.issue_cookie(user),
    }
