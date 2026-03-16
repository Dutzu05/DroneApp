from __future__ import annotations


def get_current_session_user(headers, *, session_gateway):
    user = session_gateway.current_user(headers)
    if not user:
        return None
    return {
        "email": (user.get("email") or "").strip().lower(),
        "display_name": user.get("display_name") or "",
        "google_user_id": user.get("google_user_id") or "",
    }
