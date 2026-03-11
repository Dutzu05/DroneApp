#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_BIN="/usr/lib/postgresql/16/bin"
DATA_DIR="$ROOT_DIR/.postgres/data"

if [ -f "$DATA_DIR/PG_VERSION" ]; then
  "$PG_BIN/pg_ctl" -D "$DATA_DIR" stop
else
  echo "No local PostgreSQL data directory found at $DATA_DIR"
fi
