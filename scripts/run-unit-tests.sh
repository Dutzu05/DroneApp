#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

PYTHON_BIN="python3"
if [ -x "$repo_root/.venv/bin/python" ]; then
  PYTHON_BIN="$repo_root/.venv/bin/python"
fi

if ! "$PYTHON_BIN" -m coverage --version >/dev/null 2>&1; then
  echo "coverage is not installed for $PYTHON_BIN" >&2
  echo "Install dev dependencies with: $PYTHON_BIN -m pip install -r requirements-dev.txt" >&2
  exit 1
fi

echo "[unit] Running backend unit tests with coverage gate..."
"$PYTHON_BIN" -m coverage erase
"$PYTHON_BIN" -m coverage run -m unittest discover -s tests -v
"$PYTHON_BIN" -m coverage report
"$PYTHON_BIN" -m coverage xml -o coverage.xml
