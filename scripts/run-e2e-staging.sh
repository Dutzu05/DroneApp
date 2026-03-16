#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [ -z "${E2E_BASE_URL:-}" ]; then
  echo "E2E_BASE_URL is required" >&2
  exit 1
fi

AIRSPACE_SMOKE_BASE_URL="$E2E_BASE_URL" ./scripts/run-airspace-http-smoke.sh
npm ci
npx playwright install chromium
npx playwright test --grep @staging
