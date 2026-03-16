#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [ -z "${E2E_BASE_URL:-}" ]; then
  echo "E2E_BASE_URL is required" >&2
  exit 1
fi

npm ci
npx playwright install --with-deps chromium
npx playwright test --grep @staging
