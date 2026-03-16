#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

container_cli="${CONTAINER_CLI:-}"
if [ -z "$container_cli" ]; then
  if command -v docker >/dev/null 2>&1; then
    container_cli="docker"
  elif command -v podman >/dev/null 2>&1; then
    container_cli="podman"
  else
    echo "Neither docker nor podman is available." >&2
    exit 1
  fi
fi

export COMPOSE_PROJECT_NAME="drone-e2e-${COMPOSE_PROJECT_SUFFIX:-local}"
cleanup() {
  "$container_cli" compose down -v || true
}
trap cleanup EXIT

"$container_cli" compose up -d --build
./scripts/wait-for-http.sh "${E2E_BASE_URL:-http://127.0.0.1:5174/healthz}" 90 2
npm ci
npx playwright install --with-deps chromium
E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:5174}" npx playwright test --grep @compose
