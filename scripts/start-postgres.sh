#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_BIN="/usr/lib/postgresql/16/bin"
DATA_DIR="$ROOT_DIR/.postgres/data"
SOCKET_DIR="$ROOT_DIR/.postgres/run"
LOG_FILE="$ROOT_DIR/.postgres/postgres.log"
PORT="5433"

mkdir -p "$ROOT_DIR/.postgres" "$SOCKET_DIR"

if [ ! -f "$DATA_DIR/PG_VERSION" ]; then
  "$PG_BIN/initdb" -D "$DATA_DIR" --auth-local=trust --auth-host=trust
fi

if "$PG_BIN/pg_isready" -h "$SOCKET_DIR" -p "$PORT" >/dev/null 2>&1; then
  echo "PostgreSQL is already running on socket $SOCKET_DIR port $PORT"
  exit 0
fi

"$PG_BIN/pg_ctl" -D "$DATA_DIR" -l "$LOG_FILE" -o "-c listen_addresses='' -p $PORT -k $SOCKET_DIR" start
"$PG_BIN/pg_isready" -h "$SOCKET_DIR" -p "$PORT"
