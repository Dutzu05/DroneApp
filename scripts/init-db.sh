#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_BIN="/usr/lib/postgresql/16/bin"
SOCKET_DIR="$ROOT_DIR/.postgres/run"
PORT="5433"

export PGHOST="$SOCKET_DIR" PGPORT="$PORT"

if ! "$PG_BIN/psql" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='drone_app'" | grep -q 1; then
  "$PG_BIN/createdb" drone_app
fi

"$PG_BIN/psql" -d drone_app -f "$ROOT_DIR/sql/restriction_zones_schema.sql"
"$PG_BIN/psql" -d drone_app -f "$ROOT_DIR/sql/flight_plans_schema.sql"
