from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GoogleSessionRequest:
    email: str
    display_name: str
    google_user_id: str
    id_token: str
    app: str
    source_ip: str
