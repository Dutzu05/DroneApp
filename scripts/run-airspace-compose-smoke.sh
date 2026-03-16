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

base_url="${AIRSPACE_SMOKE_BASE_URL:-http://127.0.0.1:5174}"
app_service="${AIRSPACE_SMOKE_APP_SERVICE:-app}"
db_service="${AIRSPACE_SMOKE_DB_SERVICE:-db}"
ingest_sources="${AIRSPACE_SMOKE_SOURCES:-restriction_zones_json}"

./scripts/wait-for-http.sh "${base_url%/}/healthz" 90 2

echo "[airspace-smoke] Running real ingestion for: ${ingest_sources}"
ingest_args=()
for source in ${ingest_sources//,/ }; do
  ingest_args+=(--source "$source")
done
"$container_cli" compose exec -T "$app_service" python3 scripts/ingest_airspace.py "${ingest_args[@]}"

sample_row="$("$container_cli" compose exec -T "$db_service" \
  psql -U drone -d drone_app -At -F '|' -c "
    SELECT
      zone_id,
      ST_X(ST_PointOnSurface(geometry)) AS lon,
      ST_Y(ST_PointOnSurface(geometry)) AS lat,
      ST_XMin(geometry) AS min_lon,
      ST_YMin(geometry) AS min_lat,
      ST_XMax(geometry) AS max_lon,
      ST_YMax(geometry) AS max_lat
    FROM airspace_zones_active
    WHERE source = 'restriction_zones_json'
      AND (lower_altitude_m IS NULL OR lower_altitude_m <= 120)
      AND (upper_altitude_m IS NULL OR upper_altitude_m >= 120)
    ORDER BY zone_id
    LIMIT 1
  ")"

if [ -z "$sample_row" ]; then
  echo "[airspace-smoke] No active restriction zone found after ingestion." >&2
  exit 1
fi

IFS='|' read -r zone_id point_lon point_lat min_lon min_lat max_lon max_lat <<<"$sample_row"

active_count="$("$container_cli" compose exec -T "$db_service" \
  psql -U drone -d drone_app -At -c "
    SELECT COUNT(*)
    FROM airspace_zones z
    JOIN airspace_versions v ON v.version_id = z.version_id
    WHERE v.is_active = TRUE
      AND z.source = 'restriction_zones_json'
  ")"

if [ "${active_count:-0}" -le 0 ]; then
  echo "[airspace-smoke] No active restriction zones were stored in PostGIS." >&2
  exit 1
fi

echo "[airspace-smoke] Active restriction zones: $active_count"

BASE_URL="$base_url" \
ZONE_ID="$zone_id" \
POINT_LON="$point_lon" \
POINT_LAT="$point_lat" \
MIN_LON="$min_lon" \
MIN_LAT="$min_lat" \
MAX_LON="$max_lon" \
MAX_LAT="$max_lat" \
python3 - <<'PY'
import json
import os
import urllib.parse
import urllib.request


def get_json(url: str):
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def post_json(url: str, payload: dict):
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as response:
        return json.load(response)


base_url = os.environ['BASE_URL'].rstrip('/')
zone_id = os.environ['ZONE_ID']
point_lon = float(os.environ['POINT_LON'])
point_lat = float(os.environ['POINT_LAT'])
min_lon = float(os.environ['MIN_LON'])
min_lat = float(os.environ['MIN_LAT'])
max_lon = float(os.environ['MAX_LON'])
max_lat = float(os.environ['MAX_LAT'])

bbox = ','.join(f'{value:.6f}' for value in (min_lon, min_lat, max_lon, max_lat))
zones = get_json(
    f"{base_url}/airspace/zones?{urllib.parse.urlencode({'bbox': bbox, 'categories': 'restricted'})}"
)
assert zones['count'] > 0, 'Expected viewport query to return at least one restricted zone'
assert any(zone.get('zone_id') == zone_id for zone in zones.get('zones', [])), 'Viewport query did not include sampled zone'

point_result = get_json(
    f"{base_url}/airspace/check-point?{urllib.parse.urlencode({'lon': point_lon, 'lat': point_lat, 'alt_m': 120})}"
)
assert point_result['count'] > 0, 'Expected point check to return at least one zone'
assert any(zone.get('zone_id') == zone_id for zone in point_result.get('zones', [])), 'Point check did not include sampled zone'

route_result = post_json(
    f"{base_url}/airspace/check-route",
    {
        'path': [
            {'lon': min_lon, 'lat': min_lat, 'alt_m': 120},
            {'lon': max_lon, 'lat': max_lat, 'alt_m': 120},
        ]
    },
)
assert route_result['count'] > 0, 'Expected route check to return at least one zone'
assert any(zone.get('zone_id') == zone_id for zone in route_result.get('zones', [])), 'Route check did not include sampled zone'

print(json.dumps({
    'sample_zone_id': zone_id,
    'viewport_count': zones['count'],
    'point_count': point_result['count'],
    'route_count': route_result['count'],
}, indent=2))
PY

echo "[airspace-smoke] Compose/PostGIS ingestion smoke passed."
