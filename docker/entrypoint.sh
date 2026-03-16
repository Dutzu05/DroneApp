#!/usr/bin/env bash
set -euo pipefail

cd /app

if [ "${DRONE_RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Waiting for PostgreSQL at ${PGHOST:-db}:${PGPORT:-5432}..."
  for _ in $(seq 1 60); do
    if python3 - <<'PY' >/dev/null 2>&1
import os
import socket

host = os.environ.get("PGHOST", "db")
socket.gethostbyname(host)
PY
    then
      break
    fi
    sleep 1
  done

  for _ in $(seq 1 60); do
    if psql -d postgres -tAc 'SELECT 1' >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  ./scripts/init-db.sh
fi

if [ ! -f "${DRONE_ANEXA1_TEMPLATE_PATH:-/app/assets/templates/ANEXA1.pdf}" ]; then
  echo "ANEXA 1 template missing at ${DRONE_ANEXA1_TEMPLATE_PATH:-/app/assets/templates/ANEXA1.pdf}" >&2
fi

exec "$@"
