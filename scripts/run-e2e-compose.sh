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
export DRONE_APP_PORT="${DRONE_APP_PORT:-4174}"
export DRONE_AIRSPACE_API_PORT="${DRONE_AIRSPACE_API_PORT:-18080}"
cleanup() {
  "$container_cli" compose down -v || true
}
trap cleanup EXIT

"$container_cli" compose up -d --build
base_url="${E2E_BASE_URL:-http://127.0.0.1:${DRONE_APP_PORT}}"
./scripts/wait-for-http.sh "${base_url%/}/healthz" 90 2
AIRSPACE_SMOKE_BASE_URL="$base_url" CONTAINER_CLI="$container_cli" ./scripts/run-airspace-compose-smoke.sh
DRONE_TELEMETRY_SMOKE_BASE_URL="$base_url" CONTAINER_CLI="$container_cli" ./scripts/run-drone-telemetry-compose-smoke.sh
npm ci
npx playwright install chromium
E2E_BASE_URL="$base_url" npx playwright test --grep @compose
