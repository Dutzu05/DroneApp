from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_NAME = os.environ.get("DRONE_DB_NAME", "drone_app")
DEFAULT_PGHOST = os.environ.get("PGHOST", str(ROOT_DIR / ".postgres" / "run"))
DEFAULT_PGPORT = os.environ.get("PGPORT", "5433")


class FlightPlanRepositoryError(RuntimeError):
    pass


def _psql_binary() -> str:
    configured = os.environ.get("PSQL_BIN")
    if configured:
        return configured

    pg_bin = os.environ.get("PG_BIN", "/usr/lib/postgresql/16/bin")
    candidate = Path(pg_bin)
    if candidate.is_dir():
        return str(candidate / "psql")
    return pg_bin


def _db_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PGHOST", DEFAULT_PGHOST)
    env.setdefault("PGPORT", DEFAULT_PGPORT)
    return env


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _sql_jsonb(value: Any) -> str:
    return f"{_sql_literal(json.dumps(value, ensure_ascii=False))}::jsonb"


def _run_sql(sql: str) -> str:
    proc = subprocess.run(
        [
            _psql_binary(),
            "-X",
            "-d",
            DEFAULT_DB_NAME,
            "-v",
            "ON_ERROR_STOP=1",
            "-tA",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        env=_db_env(),
        check=False,
    )
    if proc.returncode != 0:
        raise FlightPlanRepositoryError(proc.stderr.strip() or "psql query failed")
    return proc.stdout.strip()


def _run_json_query(sql: str) -> Any:
    raw = _run_sql(sql)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FlightPlanRepositoryError(f"Invalid JSON returned by psql: {raw}") from exc


def upsert_app_user(user: dict[str, Any], last_app: str) -> dict[str, Any]:
    email = (user.get("email") or "").strip().lower()
    if not email:
        raise FlightPlanRepositoryError("app user email is required")

    sql = f"""
    WITH upserted AS (
      INSERT INTO app_users (
        email,
        display_name,
        google_user_id,
        last_app
      )
      VALUES (
        {_sql_literal(email)},
        {_sql_literal(user.get("display_name") or "")},
        {_sql_literal(user.get("google_user_id") or "")},
        {_sql_literal(last_app)}
      )
      ON CONFLICT (email) DO UPDATE SET
        display_name = EXCLUDED.display_name,
        google_user_id = CASE
          WHEN EXCLUDED.google_user_id = '' THEN app_users.google_user_id
          ELSE EXCLUDED.google_user_id
        END,
        last_app = EXCLUDED.last_app,
        last_seen_at = NOW(),
        updated_at = NOW()
      RETURNING
        id,
        email,
        display_name,
        google_user_id,
        first_seen_at,
        last_seen_at,
        last_app
    )
    SELECT row_to_json(upserted) FROM upserted;
    """
    result = _run_json_query(sql)
    if not isinstance(result, dict):
        raise FlightPlanRepositoryError("failed to upsert app user")
    return result


def create_flight_plan(owner: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    owner_row = upsert_app_user(owner, last_app=plan.get("created_from_app", "visualise_zones_web"))

    sql = f"""
    WITH inserted AS (
      INSERT INTO flight_plans (
        public_id,
        owner_user_id,
        owner_email,
        owner_display_name,
        operator_name,
        operator_contact,
        contact_person,
        phone_landline,
        phone_mobile,
        fax,
        operator_email,
        uas_registration,
        uas_class_code,
        uas_class_label,
        category,
        operation_mode,
        mtom_kg,
        pilot_name,
        pilot_phone,
        purpose,
        local_timezone,
        scheduled_start_at,
        scheduled_end_at,
        location_name,
        area_kind,
        center_lon,
        center_lat,
        radius_m,
        polygon_points,
        area_geojson,
        max_altitude_m,
        selected_twr,
        risk_level,
        risk_summary,
        airspace_assessment,
        anexa_payload,
        pdf_rel_path
      )
      VALUES (
        {_sql_literal(plan["public_id"])},
        {owner_row["id"]},
        {_sql_literal(owner_row["email"])},
        {_sql_literal(owner_row.get("display_name") or "")},
        {_sql_literal(plan["operator_name"])},
        {_sql_literal(plan["operator_contact"])},
        {_sql_literal(plan["contact_person"])},
        {_sql_literal(plan.get("phone_landline"))},
        {_sql_literal(plan["phone_mobile"])},
        {_sql_literal(plan.get("fax"))},
        {_sql_literal(plan["operator_email"])},
        {_sql_literal(plan["uas_registration"])},
        {_sql_literal(plan["uas_class_code"])},
        {_sql_literal(plan["uas_class_label"])},
        {_sql_literal(plan["category"])},
        {_sql_literal(plan["operation_mode"])},
        {_sql_literal(plan["mtom_kg"])},
        {_sql_literal(plan["pilot_name"])},
        {_sql_literal(plan["pilot_phone"])},
        {_sql_literal(plan["purpose"])},
        {_sql_literal(plan["local_timezone"])},
        {_sql_literal(plan["scheduled_start_at"])},
        {_sql_literal(plan["scheduled_end_at"])},
        {_sql_literal(plan["location_name"])},
        {_sql_literal(plan["area_kind"])},
        {_sql_literal(plan.get("center_lon"))},
        {_sql_literal(plan.get("center_lat"))},
        {_sql_literal(plan.get("radius_m"))},
        {_sql_jsonb(plan.get("polygon_points")) if plan.get("polygon_points") is not None else "NULL"},
        {_sql_jsonb(plan["area_geojson"])},
        {_sql_literal(plan["max_altitude_m"])},
        {_sql_literal(plan["selected_twr"])},
        {_sql_literal(plan["risk_level"])},
        {_sql_literal(plan["risk_summary"])},
        {_sql_jsonb(plan["airspace_assessment"])},
        {_sql_jsonb(plan["anexa_payload"])},
        {_sql_literal(plan["pdf_rel_path"])}
      )
      RETURNING *
    )
    SELECT row_to_json(t) FROM (
      SELECT
        public_id,
        owner_email,
        owner_display_name,
        operator_name,
        location_name,
        area_kind,
        selected_twr,
        max_altitude_m,
        risk_level,
        risk_summary,
        workflow_status,
        scheduled_start_at,
        scheduled_end_at,
        pdf_rel_path
      FROM inserted
    ) AS t;
    """
    result = _run_json_query(sql)
    if not isinstance(result, dict):
        raise FlightPlanRepositoryError("failed to create flight plan")
    return result


def _runtime_state_sql(alias: str = "fp") -> str:
    return f"""
    CASE
      WHEN {alias}.workflow_status = 'cancelled' THEN 'cancelled'
      WHEN {alias}.scheduled_start_at <= NOW() AND {alias}.scheduled_end_at >= NOW() THEN 'ongoing'
      WHEN {alias}.scheduled_start_at > NOW() THEN 'upcoming'
      ELSE 'completed'
    END
    """


def list_flight_plans(
    *,
    owner_email: str | None = None,
    include_past: bool = True,
    include_cancelled: bool = True,
    limit: int = 250,
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    if owner_email:
        where.append(f"fp.owner_email = {_sql_literal(owner_email.strip().lower())}")
    if not include_past:
        where.append("fp.scheduled_end_at >= NOW()")
    if not include_cancelled:
        where.append("fp.workflow_status <> 'cancelled'")

    sql = f"""
    SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json) FROM (
      SELECT
        fp.public_id,
        fp.owner_email,
        fp.owner_display_name,
        fp.operator_name,
        fp.location_name,
        fp.area_kind,
        fp.selected_twr,
        fp.max_altitude_m,
        fp.risk_level,
        fp.risk_summary,
        fp.workflow_status,
        {_runtime_state_sql("fp")} AS runtime_state,
        fp.scheduled_start_at,
        fp.scheduled_end_at,
        TO_CHAR(fp.scheduled_start_at AT TIME ZONE fp.local_timezone, 'DD.MM.YYYY HH24:MI') AS scheduled_start_local,
        TO_CHAR(fp.scheduled_end_at AT TIME ZONE fp.local_timezone, 'DD.MM.YYYY HH24:MI') AS scheduled_end_local,
        fp.local_timezone,
        fp.created_at,
        fp.updated_at,
        fp.pdf_rel_path
      FROM flight_plans fp
      WHERE {" AND ".join(where)}
      ORDER BY
        CASE {_runtime_state_sql("fp")}
          WHEN 'ongoing' THEN 0
          WHEN 'upcoming' THEN 1
          WHEN 'completed' THEN 2
          ELSE 3
        END,
        fp.scheduled_start_at ASC
      LIMIT {max(1, min(limit, 1000))}
    ) AS t;
    """
    result = _run_json_query(sql)
    return result if isinstance(result, list) else []


def get_flight_plan(
    public_id: str,
    *,
    owner_email: str | None = None,
) -> dict[str, Any] | None:
    where = [f"fp.public_id = {_sql_literal(public_id)}"]
    if owner_email:
        where.append(f"fp.owner_email = {_sql_literal(owner_email.strip().lower())}")

    sql = f"""
    SELECT row_to_json(t) FROM (
      SELECT
        fp.public_id,
        fp.owner_email,
        fp.owner_display_name,
        fp.operator_name,
        fp.operator_contact,
        fp.contact_person,
        fp.phone_landline,
        fp.phone_mobile,
        fp.fax,
        fp.operator_email,
        fp.uas_registration,
        fp.uas_class_code,
        fp.uas_class_label,
        fp.category,
        fp.operation_mode,
        fp.mtom_kg,
        fp.pilot_name,
        fp.pilot_phone,
        fp.purpose,
        fp.location_name,
        fp.area_kind,
        fp.center_lon,
        fp.center_lat,
        fp.radius_m,
        fp.polygon_points,
        fp.area_geojson,
        fp.max_altitude_m,
        fp.selected_twr,
        fp.risk_level,
        fp.risk_summary,
        fp.workflow_status,
        {_runtime_state_sql("fp")} AS runtime_state,
        fp.scheduled_start_at,
        fp.scheduled_end_at,
        TO_CHAR(fp.scheduled_start_at AT TIME ZONE fp.local_timezone, 'DD.MM.YYYY HH24:MI') AS scheduled_start_local,
        TO_CHAR(fp.scheduled_end_at AT TIME ZONE fp.local_timezone, 'DD.MM.YYYY HH24:MI') AS scheduled_end_local,
        fp.local_timezone,
        fp.airspace_assessment,
        fp.anexa_payload,
        fp.pdf_rel_path,
        fp.created_at,
        fp.updated_at
      FROM flight_plans fp
      WHERE {" AND ".join(where)}
      LIMIT 1
    ) AS t;
    """
    result = _run_json_query(sql)
    return result if isinstance(result, dict) else None
