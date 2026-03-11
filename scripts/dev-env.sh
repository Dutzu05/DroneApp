#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export FLUTTER_HOME="$ROOT_DIR/.tooling/flutter"
export PATH="$FLUTTER_HOME/bin:$PATH"

export PG_BIN="/usr/lib/postgresql/16/bin"
export PGDATA="$ROOT_DIR/.postgres/data"
export PGHOST="$ROOT_DIR/.postgres/run"
export PGPORT="5433"

cat <<ENV
Loaded development environment:
- FLUTTER_HOME=$FLUTTER_HOME
- PGDATA=$PGDATA
- PGHOST=$PGHOST
- PGPORT=$PGPORT
ENV
