from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from backend.airspace.repositories.db import get_connection


class RawSourceRepository:
    def create(self, *, source: str, fetched_at: datetime, payload_json: dict[str, Any], checksum: str, status: str, conn=None) -> int:
        if conn is not None:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO raw_airspace_sources (source, fetched_at, payload_json, checksum, status)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                RETURNING id
                """,
                (source, fetched_at, json.dumps(payload_json), checksum, status),
            )
            return int(cur.fetchone()['id'])
        with get_connection() as managed_conn:
            return self.create(
                source=source,
                fetched_at=fetched_at,
                payload_json=payload_json,
                checksum=checksum,
                status=status,
                conn=managed_conn,
            )

    def update_status(self, raw_id: int, *, status: str, conn=None) -> None:
        if conn is not None:
            cur = conn.cursor()
            cur.execute(
                'UPDATE raw_airspace_sources SET status = %s WHERE id = %s',
                (status, raw_id),
            )
            return
        with get_connection() as managed_conn:
            self.update_status(raw_id, status=status, conn=managed_conn)
