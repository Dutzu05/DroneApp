from __future__ import annotations

from datetime import datetime

from backend.airspace.repositories.db import get_connection


class AirspaceVersionRepository:
    def find_active_by_checksum(self, *, source: str, checksum: str, conn=None) -> dict | None:
        if conn is not None:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT version_id, source, imported_at, record_count, checksum, is_active
                FROM airspace_versions
                WHERE source = %s AND checksum = %s AND is_active = TRUE
                ORDER BY imported_at DESC
                LIMIT 1
                """,
                (source, checksum),
            )
            return cur.fetchone()
        with get_connection() as managed_conn:
            return self.find_active_by_checksum(source=source, checksum=checksum, conn=managed_conn)

    def create(self, *, version_id: str, source: str, imported_at: datetime, record_count: int, checksum: str, is_active: bool, conn=None) -> None:
        if conn is not None:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO airspace_versions (version_id, source, imported_at, record_count, checksum, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (version_id, source, imported_at, record_count, checksum, is_active),
            )
            return
        with get_connection() as managed_conn:
            self.create(
                version_id=version_id,
                source=source,
                imported_at=imported_at,
                record_count=record_count,
                checksum=checksum,
                is_active=is_active,
                conn=managed_conn,
            )

    def activate(self, *, source: str, version_id: str, conn=None) -> None:
        if conn is not None:
            cur = conn.cursor()
            cur.execute('UPDATE airspace_versions SET is_active = FALSE WHERE source = %s', (source,))
            cur.execute(
                'UPDATE airspace_versions SET is_active = TRUE WHERE source = %s AND version_id = %s',
                (source, version_id),
            )
            return
        with get_connection() as managed_conn:
            self.activate(source=source, version_id=version_id, conn=managed_conn)
