from __future__ import annotations

from typing import Any, Callable


class AppUserRepository:
    def __init__(self, upsert_user: Callable[[dict[str, Any], str], dict[str, Any]]):
        self._upsert_user = upsert_user

    def upsert(self, user: dict[str, Any], app_name: str) -> dict[str, Any]:
        return self._upsert_user(user, app_name)
