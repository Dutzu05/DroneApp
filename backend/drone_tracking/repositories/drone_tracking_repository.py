from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.airspace.repositories.db import get_connection


def _runtime_state_sql(alias: str = 'fp') -> str:
    return f"""
    CASE
      WHEN {alias}.workflow_status = 'cancelled' THEN 'cancelled'
      WHEN {alias}.scheduled_start_at <= NOW() AND {alias}.scheduled_end_at >= NOW() THEN 'ongoing'
      WHEN {alias}.scheduled_start_at > NOW() THEN 'upcoming'
      ELSE 'completed'
    END
    """


def _approval_active_sql(alias: str = 'fp') -> str:
    return f"COALESCE({alias}.approval_status, 'not_required') IN ('not_required', 'approved')"


class DroneTrackingRepository:
    def get_live_drone(
        self,
        drone_id: str,
        *,
        owner_email: str | None = None,
        include_upcoming: bool = False,
        only_ongoing: bool = False,
    ) -> dict[str, Any] | None:
        drones = self.list_live_drones(
            owner_email=owner_email,
            include_upcoming=include_upcoming,
            only_ongoing=only_ongoing,
        )
        for drone in drones:
            if str(drone.get('drone_id') or '') == drone_id:
                return drone
        return None

    def list_mock_candidate_plans(self, *, include_upcoming: bool = True) -> list[dict[str, Any]]:
        runtime_state = _runtime_state_sql('fp')
        where = ["fp.workflow_status = 'planned'", "fp.scheduled_end_at >= NOW()", _approval_active_sql('fp')]
        if not include_upcoming:
            where.append("fp.scheduled_start_at <= NOW()")
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                  fp.id AS flight_plan_id,
                  fp.public_id,
                  fp.owner_user_id,
                  fp.owner_email,
                  fp.owner_display_name,
                  fp.location_name,
                  fp.selected_twr,
                  fp.area_kind,
                  fp.center_lon,
                  fp.center_lat,
                  fp.radius_m,
                  fp.area_geojson,
                  fp.max_altitude_m,
                  fp.scheduled_start_at,
                  fp.scheduled_end_at,
                  {runtime_state} AS runtime_state,
                  dd.drone_id,
                  dd.id AS drone_device_id,
                  dd.label AS drone_label
                FROM flight_plans fp
                LEFT JOIN drone_devices dd ON dd.flight_plan_public_id = fp.public_id
                WHERE {' AND '.join(where)}
                ORDER BY
                  CASE {runtime_state}
                    WHEN 'ongoing' THEN 0
                    WHEN 'upcoming' THEN 1
                    ELSE 2
                  END,
                  fp.scheduled_start_at ASC
                """
            )
            return list(cur.fetchall())

    def upsert_drone_device(
        self,
        *,
        drone_id: str,
        owner_user_id: int | None,
        owner_email: str,
        owner_display_name: str,
        flight_plan_public_id: str,
        label: str,
        is_mock: bool = True,
    ) -> dict[str, Any]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO drone_devices (
                  drone_id,
                  owner_user_id,
                  owner_email,
                  owner_display_name,
                  flight_plan_public_id,
                  label,
                  is_mock
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (drone_id) DO UPDATE SET
                  owner_user_id = EXCLUDED.owner_user_id,
                  owner_email = EXCLUDED.owner_email,
                  owner_display_name = EXCLUDED.owner_display_name,
                  flight_plan_public_id = EXCLUDED.flight_plan_public_id,
                  label = EXCLUDED.label,
                  is_mock = EXCLUDED.is_mock,
                  updated_at = NOW()
                RETURNING *
                """,
                (
                    drone_id,
                    owner_user_id,
                    owner_email,
                    owner_display_name,
                    flight_plan_public_id,
                    label,
                    is_mock,
                ),
            )
            return dict(cur.fetchone())

    def insert_telemetry(
        self,
        *,
        drone_device_id: int,
        drone_id: str,
        flight_plan_public_id: str,
        latitude: float,
        longitude: float,
        altitude: float,
        heading: float,
        pitch: float,
        roll: float,
        speed: float,
        telemetry_timestamp: datetime,
        battery_level: float,
        status: str,
        source: str = 'mock',
    ) -> dict[str, Any]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO drone_telemetry (
                  drone_device_id,
                  drone_id,
                  flight_plan_public_id,
                  latitude,
                  longitude,
                  altitude,
                  heading,
                  pitch,
                  roll,
                  speed,
                  telemetry_timestamp,
                  battery_level,
                  status,
                  source
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    drone_device_id,
                    drone_id,
                    flight_plan_public_id,
                    latitude,
                    longitude,
                    altitude,
                    heading,
                    pitch,
                    roll,
                    speed,
                    telemetry_timestamp,
                    battery_level,
                    status,
                    source,
                ),
            )
            inserted = dict(cur.fetchone())
            cur.execute(
                """
                DELETE FROM drone_telemetry
                WHERE drone_id = %s
                  AND id NOT IN (
                    SELECT id FROM drone_telemetry
                    WHERE drone_id = %s
                    ORDER BY telemetry_timestamp DESC
                    LIMIT 120
                  )
                """,
                (drone_id, drone_id),
            )
            return inserted

    def list_live_drones(
        self,
        *,
        owner_email: str | None = None,
        include_upcoming: bool = False,
        only_ongoing: bool = False,
    ) -> list[dict[str, Any]]:
        runtime_state = _runtime_state_sql('fp')
        where = ["fp.workflow_status = 'planned'", "fp.scheduled_end_at >= NOW()", _approval_active_sql('fp')]
        if owner_email:
            where.append("dd.owner_email = %s")
        if only_ongoing:
            where.append(f"{runtime_state} = 'ongoing'")
        elif not include_upcoming:
            where.append(f"{runtime_state} = 'ongoing'")

        params: list[Any] = []
        if owner_email:
            params.append(owner_email.strip().lower())

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                WITH latest AS (
                  SELECT DISTINCT ON (dt.drone_id)
                    dt.drone_id,
                    dt.drone_device_id,
                    dt.flight_plan_public_id,
                    dt.latitude,
                    dt.longitude,
                    dt.altitude,
                    dt.heading,
                    dt.pitch,
                    dt.roll,
                    dt.speed,
                    dt.telemetry_timestamp,
                    dt.battery_level,
                    dt.status,
                    dt.source
                  FROM drone_telemetry dt
                  ORDER BY dt.drone_id, dt.telemetry_timestamp DESC
                )
                SELECT
                  latest.drone_id,
                  latest.latitude,
                  latest.longitude,
                  latest.altitude,
                  latest.heading,
                  latest.pitch,
                  latest.roll,
                  latest.speed,
                  latest.telemetry_timestamp AS timestamp,
                  latest.battery_level,
                  latest.status,
                  latest.source,
                  dd.label,
                  dd.is_mock,
                  dd.owner_email,
                  dd.owner_display_name,
                  fp.public_id AS flight_plan_public_id,
                  fp.location_name,
                  fp.selected_twr,
                  fp.scheduled_start_at,
                  fp.scheduled_end_at,
                  {runtime_state} AS runtime_state
                FROM latest
                JOIN drone_devices dd ON dd.id = latest.drone_device_id
                JOIN flight_plans fp ON fp.public_id = latest.flight_plan_public_id
                WHERE {' AND '.join(where)}
                ORDER BY latest.telemetry_timestamp DESC, latest.drone_id ASC
                """,
                params,
            )
            return list(cur.fetchall())

    def telemetry_history(self, drone_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  drone_id,
                  latitude,
                  longitude,
                  altitude,
                  heading,
                  pitch,
                  roll,
                  speed,
                  telemetry_timestamp AS timestamp,
                  battery_level,
                  status,
                  source
                FROM drone_telemetry
                WHERE drone_id = %s
                ORDER BY telemetry_timestamp DESC
                LIMIT %s
                """,
                (drone_id, limit),
            )
            rows = list(cur.fetchall())
        rows.reverse()
        return rows

    def count_live_drones(self, *, only_ongoing: bool = True) -> int:
        with get_connection() as conn, conn.cursor() as cur:
            runtime_state = _runtime_state_sql('fp')
            where = ["fp.workflow_status = 'planned'", "fp.scheduled_end_at >= NOW()", _approval_active_sql('fp')]
            if only_ongoing:
                where.append(f"{runtime_state} = 'ongoing'")
            cur.execute(
                f"""
                WITH latest AS (
                  SELECT DISTINCT ON (dt.drone_id) dt.drone_id, dt.flight_plan_public_id
                  FROM drone_telemetry dt
                  ORDER BY dt.drone_id, dt.telemetry_timestamp DESC
                )
                SELECT COUNT(*) AS count
                FROM latest
                JOIN flight_plans fp ON fp.public_id = latest.flight_plan_public_id
                WHERE {' AND '.join(where)}
                """
            )
            row = cur.fetchone()
            return int((row or {}).get('count') or 0)

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)
