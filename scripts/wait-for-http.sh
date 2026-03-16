#!/usr/bin/env bash
set -euo pipefail

url="${1:-}"
attempts="${2:-60}"
sleep_seconds="${3:-2}"

if [ -z "$url" ]; then
  echo "Usage: $0 <url> [attempts] [sleep_seconds]" >&2
  exit 1
fi

for _ in $(seq 1 "$attempts"); do
  if curl -fsS "$url" >/dev/null 2>&1; then
    exit 0
  fi
  sleep "$sleep_seconds"
done

echo "Timed out waiting for $url" >&2
exit 1
