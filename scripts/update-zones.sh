#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# update-zones.sh
# ─────────────────────────────────────────────────────────────────────
# Cron / systemd-timer wrapper around the Python fetcher.
# Runs fetch_restriction_zones.py and logs whether data changed.
#
# Install as a cron job (every 6 hours):
#   crontab -e
#   0 */6 * * * /home/vlad/Projects/Drone/scripts/update-zones.sh >> /home/vlad/Projects/Drone/scripts/zone-update.log 2>&1
#
# Or as a systemd timer (see README).
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "────────────────────────────────────────"
echo "[$(date -Iseconds)] Zone update started"

python3 "$SCRIPT_DIR/fetch_restriction_zones.py"

echo "[$(date -Iseconds)] Zone update finished"
echo ""
