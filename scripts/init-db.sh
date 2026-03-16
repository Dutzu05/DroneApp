#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_NAME="${DRONE_DB_NAME:-drone_app}"
DEFAULT_SOCKET_DIR="$ROOT_DIR/.postgres/run"

export PGHOST="${PGHOST:-$DEFAULT_SOCKET_DIR}"
export PGPORT="${PGPORT:-5433}"

resolve_pg_bin() {
  local tool="$1"
  if command -v "$tool" >/dev/null 2>&1; then
    command -v "$tool"
    return
  fi

  local pg_bin="${PG_BIN:-/usr/lib/postgresql/16/bin}"
  if [ -x "$pg_bin/$tool" ]; then
    echo "$pg_bin/$tool"
    return
  fi

  echo "Missing PostgreSQL client tool: $tool" >&2
  exit 1
}

PSQL_BIN="${PSQL_BIN:-$(resolve_pg_bin psql)}"
CREATEDB_BIN="${CREATEDB_BIN:-$(resolve_pg_bin createdb)}"

if ! "$PSQL_BIN" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  "$CREATEDB_BIN" "$DB_NAME"
fi

"$PSQL_BIN" -d "$DB_NAME" -f "$ROOT_DIR/sql/restriction_zones_schema.sql"
"$PSQL_BIN" -d "$DB_NAME" -f "$ROOT_DIR/sql/flight_plans_schema.sql"
if "$PSQL_BIN" -d "$DB_NAME" -tAc "SELECT 1 FROM pg_available_extensions WHERE name='postgis'" | grep -q 1; then
  "$PSQL_BIN" -d "$DB_NAME" -f "$ROOT_DIR/sql/airspace_schema.sql"
else
  echo "PostGIS extension not available in this database; skipping sql/airspace_schema.sql" >&2
fi
