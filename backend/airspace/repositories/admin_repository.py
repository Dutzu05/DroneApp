from __future__ import annotations

from backend.airspace.repositories.db import get_connection


class AirspaceAdminRepository:
    def list_active_versions(self) -> list[dict]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.source,
                       v.version_id,
                       v.imported_at,
                       v.record_count,
                       v.checksum,
                       v.is_active,
                       COUNT(z.zone_id) AS zone_count
                FROM airspace_versions v
                LEFT JOIN airspace_zones z ON z.version_id = v.version_id
                WHERE v.is_active = TRUE
                GROUP BY v.source, v.version_id, v.imported_at, v.record_count, v.checksum, v.is_active
                ORDER BY v.source
                """
            )
            return list(cur.fetchall())

    def list_source_status(self) -> list[dict]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                WITH latest_raw AS (
                  SELECT DISTINCT ON (source)
                         source,
                         id,
                         fetched_at,
                         status,
                         checksum
                  FROM raw_airspace_sources
                  ORDER BY source, fetched_at DESC, id DESC
                ),
                recent_errors AS (
                  SELECT source,
                         COUNT(*) FILTER (WHERE status NOT IN ('activated', 'duplicate')) AS error_count,
                         MAX(fetched_at) FILTER (WHERE status NOT IN ('activated', 'duplicate')) AS last_error_at
                  FROM raw_airspace_sources
                  GROUP BY source
                )
                SELECT v.source,
                       v.version_id,
                       v.imported_at AS last_ingested_at,
                       v.record_count,
                       v.checksum,
                       lr.status AS last_status,
                       lr.fetched_at AS last_fetch_at,
                       COALESCE(re.error_count, 0) AS error_count,
                       re.last_error_at
                FROM airspace_versions v
                LEFT JOIN latest_raw lr ON lr.source = v.source
                LEFT JOIN recent_errors re ON re.source = v.source
                WHERE v.is_active = TRUE
                ORDER BY v.source
                """
            )
            return list(cur.fetchall())

    def list_recent_raw_events(self, *, limit: int = 20) -> list[dict]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, fetched_at, checksum, status, created_at
                FROM raw_airspace_sources
                ORDER BY fetched_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cur.fetchall())

    def list_recent_issues(self, *, limit: int = 20) -> list[dict]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, fetched_at, checksum, status, created_at
                FROM raw_airspace_sources
                WHERE status NOT IN ('activated', 'duplicate')
                ORDER BY fetched_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cur.fetchall())
