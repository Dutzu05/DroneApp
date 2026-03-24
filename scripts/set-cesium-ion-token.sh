#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRET_DIR="$ROOT_DIR/.data/secrets"
TOKEN_PATH="$SECRET_DIR/drone-cesium-ion-token"

usage() {
  echo "Usage: scripts/set-cesium-ion-token.sh <token>|--show|--clear" >&2
}

if [ "${1:-}" = "--show" ]; then
  if [ -s "$TOKEN_PATH" ]; then
    echo "Cesium ion token is configured at $TOKEN_PATH"
  else
    echo "Cesium ion token is not configured."
  fi
  exit 0
fi

if [ "${1:-}" = "--clear" ]; then
  rm -f "$TOKEN_PATH"
  echo "Cleared Cesium ion token."
  exit 0
fi

if [ "$#" -ne 1 ] || [ -z "${1:-}" ]; then
  usage
  exit 1
fi

mkdir -p "$SECRET_DIR"
umask 177
printf '%s' "$1" > "$TOKEN_PATH"
chmod 600 "$TOKEN_PATH" 2>/dev/null || true
echo "Stored Cesium ion token at $TOKEN_PATH"
