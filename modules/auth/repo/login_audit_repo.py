from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class LoginAuditRepository:
    def __init__(self, file_path: Path):
        self._file_path = file_path
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._file_path.exists():
            return {}
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        accounts = data if isinstance(data, list) else data.get("accounts", [])
        loaded: dict[str, dict[str, Any]] = {}
        for row in accounts:
            if not isinstance(row, dict):
                continue
            email = (row.get("email") or "").strip().lower()
            if email:
                loaded[email] = row
        return loaded

    def _persist(self) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "accounts": sorted(
                self._rows.values(),
                key=lambda row: row.get("last_seen", ""),
                reverse=True,
            )
        }
        tmp_path = self._file_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self._file_path)

    def record_login(self, *, email: str, display_name: str, google_user_id: str, source_ip: str, app_name: str, now_utc_iso: str) -> dict[str, Any]:
        normalized_email = email.strip().lower()
        if not normalized_email:
            raise ValueError("email is required")
        with self._lock:
            existing = self._rows.get(normalized_email)
            if existing is None:
                existing = {
                    "email": normalized_email,
                    "display_name": display_name,
                    "google_user_id": google_user_id,
                    "first_seen": now_utc_iso,
                }
                self._rows[normalized_email] = existing
            existing["display_name"] = display_name or existing.get("display_name", "")
            existing["google_user_id"] = google_user_id or existing.get("google_user_id", "")
            existing["last_seen"] = now_utc_iso
            existing["last_ip"] = source_ip
            existing["last_app"] = app_name
            self._persist()
            return dict(existing)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._rows.values())
        rows.sort(key=lambda row: row.get("last_seen", ""), reverse=True)
        return rows
