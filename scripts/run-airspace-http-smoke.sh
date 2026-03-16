#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

base_url="${AIRSPACE_SMOKE_BASE_URL:-${E2E_BASE_URL:-}}"
base_url="${base_url%/}"
if [ -z "$base_url" ]; then
  echo "AIRSPACE_SMOKE_BASE_URL or E2E_BASE_URL must be set." >&2
  exit 1
fi

./scripts/wait-for-http.sh "${base_url}/healthz" 90 2

BASE_URL="$base_url" python3 - <<'PY'
import json
import os
import urllib.error
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


def iter_points(coords):
    if not isinstance(coords, list):
        return
    if coords and isinstance(coords[0], (int, float)):
        yield float(coords[0]), float(coords[1])
        return
    for item in coords:
        yield from iter_points(item)


base_url = os.environ['BASE_URL'].rstrip('/')
bbox = '20,43,30,48'
zones = get_json(
    f"{base_url}/airspace/zones?{urllib.parse.urlencode({'bbox': bbox, 'categories': 'restricted,ctr,tma,notam'})}"
)
assert zones['count'] > 0, 'Expected at least one airspace zone in Romania viewport'

sample_zone = None
for zone in zones.get('zones', []):
    lower = zone.get('lower_altitude_m')
    upper = zone.get('upper_altitude_m')
    if lower is not None and lower > 120:
        continue
    if upper is not None and upper < 120:
        continue
    points = list(iter_points((zone.get('geometry') or {}).get('coordinates', [])))
    if points:
        sample_zone = zone
        break

assert sample_zone is not None, 'Expected a queryable zone with polygon coordinates'
points = list(iter_points(sample_zone['geometry']['coordinates']))
assert points, 'Sample zone geometry had no coordinates'
first_lon, first_lat = points[0]
all_lons = [point[0] for point in points]
all_lats = [point[1] for point in points]
min_lon, max_lon = min(all_lons), max(all_lons)
min_lat, max_lat = min(all_lats), max(all_lats)

point_result = get_json(
    f"{base_url}/airspace/check-point?{urllib.parse.urlencode({'lon': first_lon, 'lat': first_lat, 'alt_m': 120})}"
)
assert point_result['count'] > 0, 'Expected point check to return at least one zone'
assert any(zone.get('zone_id') == sample_zone.get('zone_id') for zone in point_result.get('zones', [])), 'Point check did not include sampled zone'

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
assert any(zone.get('zone_id') == sample_zone.get('zone_id') for zone in route_result.get('zones', [])), 'Route check did not include sampled zone'

overview_summary = {'available': False}
try:
    overview = get_json(f'{base_url}/api/admin/overview')
except urllib.error.HTTPError as exc:
    if exc.code not in {401, 403, 404}:
        raise
except urllib.error.URLError:
    pass
else:
    airspace = overview.get('airspace') or {}
    overview_summary = {
        'available': True,
        'sources': len(airspace.get('sources', [])),
        'active_versions': len(airspace.get('active_versions', [])),
        'recent_issues': len(airspace.get('recent_issues', [])),
    }
    assert overview_summary['sources'] > 0, 'Admin overview reported no airspace sources'
    assert overview_summary['active_versions'] > 0, 'Admin overview reported no active versions'

print(json.dumps({
    'sample_zone_id': sample_zone.get('zone_id'),
    'viewport_count': zones['count'],
    'point_count': point_result['count'],
    'route_count': route_result['count'],
    'admin_overview': overview_summary,
}, indent=2))
PY

echo "[airspace-smoke] HTTP airspace smoke passed for ${base_url}."
