#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CESIUM_TOKEN_PATH="$ROOT_DIR/.data/secrets/drone-cesium-ion-token"

export FLUTTER_HOME="$ROOT_DIR/.tooling/flutter"
export PATH="$FLUTTER_HOME/bin:$PATH"

export PG_BIN="/usr/lib/postgresql/16/bin"
export PGDATA="$ROOT_DIR/.postgres/data"
export PGHOST="$ROOT_DIR/.postgres/run"
export PGPORT="5433"

loaded_cesium_token=0
if [ -z "${DRONE_CESIUM_ION_TOKEN:-}" ] && [ -f "$CESIUM_TOKEN_PATH" ]; then
  export DRONE_CESIUM_ION_TOKEN="$(tr -d '\r' < "$CESIUM_TOKEN_PATH")"
  if [ -n "$DRONE_CESIUM_ION_TOKEN" ]; then
    loaded_cesium_token=1
  fi
fi

cat <<ENV
Loaded development environment:
- FLUTTER_HOME=$FLUTTER_HOME
- PGDATA=$PGDATA
- PGHOST=$PGHOST
- PGPORT=$PGPORT
- DRONE_CESIUM_ION_TOKEN=$(if [ -n "${DRONE_CESIUM_ION_TOKEN:-}" ]; then printf '%s' configured; else printf '%s' missing; fi)
ENV

if [ "$loaded_cesium_token" -eq 1 ]; then
  echo "Loaded Cesium ion token from $CESIUM_TOKEN_PATH"
fi
