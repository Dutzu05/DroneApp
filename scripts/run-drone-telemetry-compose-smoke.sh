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

app_service="${DRONE_TELEMETRY_SMOKE_APP_SERVICE:-app}"
db_service="${DRONE_TELEMETRY_SMOKE_DB_SERVICE:-db}"
smoke_email="${DRONE_TELEMETRY_SMOKE_EMAIL:-drone-telemetry-smoke@example.com}"

"$container_cli" compose exec -T "$app_service" python3 - <<'PY'
import time
import urllib.request

deadline = time.time() + 90
last_error = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen("http://127.0.0.1:5174/healthz", timeout=5) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception as exc:
        last_error = exc
    time.sleep(2)
raise SystemExit(f"Timed out waiting for app health: {last_error}")
PY

echo "[drone-telemetry-smoke] Exercising login -> flight plan -> mock telemetry flow"

smoke_output="$("$container_cli" compose exec -T "$app_service" python3 - <<'PY'
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import http.cookiejar
import urllib.request

BASE_URL = "http://127.0.0.1:5174"
SMOKE_EMAIL = "drone-telemetry-smoke@example.com"
SMOKE_NAME = "Drone Telemetry Smoke"


def post_json(opener, path, payload):
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with opener.open(req, timeout=20) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def get_json(opener, path):
    with opener.open(BASE_URL + path, timeout=20) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


cookie_jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

status, login = post_json(
    opener,
    "/api/auth/google-session",
    {
        "email": SMOKE_EMAIL,
        "display_name": SMOKE_NAME,
        "google_user_id": "mock-drone-telemetry-smoke",
        "app": "drone_telemetry_compose_smoke",
    },
)
assert status == 200, f"Unexpected login status: {status}"

now_local = datetime.now(ZoneInfo("Europe/Bucharest"))
start_local = now_local - timedelta(minutes=10)
end_local = now_local + timedelta(minutes=50)

status, created = post_json(
    opener,
    "/api/flight-plans",
    {
        "operator_name": SMOKE_NAME,
        "operator_contact": "0712345678",
        "contact_person": SMOKE_NAME,
        "phone_landline": "-",
        "phone_mobile": "0712345678",
        "fax": "-",
        "operator_email": SMOKE_EMAIL,
        "uas_registration": "YR-SMOKE",
        "uas_class_code": "C2",
        "category": "A2",
        "operation_mode": "VLOS",
        "mtom_kg": "1",
        "pilot_name": SMOKE_NAME,
        "pilot_phone": "0712345678",
        "purpose": "compose telemetry smoke",
        "location_name": "PETROSANI",
        "area_kind": "circle",
        "center_lon": 24.0271,
        "center_lat": 45.444717,
        "radius_m": 200,
        "max_altitude_m": 120,
        "selected_twr": "LRAR",
        "start_date": start_local.strftime("%Y-%m-%d"),
        "end_date": end_local.strftime("%Y-%m-%d"),
        "start_time": start_local.strftime("%H:%M"),
        "end_time": end_local.strftime("%H:%M"),
        "timezone": "Europe/Bucharest",
        "created_from_app": "drone_telemetry_compose_smoke",
    },
)
assert status == 201, f"Unexpected create status: {status}"
flight_plan = created["flight_plan"]
public_id = flight_plan["public_id"]

time.sleep(5)

status, mine = get_json(opener, "/api/drones/live")
assert status == 200, f"Unexpected /api/drones/live status: {status}"
my_drones = mine.get("drones", [])
assert any(item.get("flight_plan_public_id") == public_id for item in my_drones), "User endpoint did not include created plan"

status, admin = get_json(opener, "/api/admin/drones/live")
assert status == 200, f"Unexpected /api/admin/drones/live status: {status}"
admin_drones = admin.get("drones", [])
matching_admin = [item for item in admin_drones if item.get("flight_plan_public_id") == public_id]
assert matching_admin, "Admin endpoint did not include ongoing drone"
assert all(item.get("status") == "flying" for item in matching_admin), "Admin endpoint returned non-flying state"

print(
    json.dumps(
        {
            "public_id": public_id,
            "mine_count": len(my_drones),
            "admin_count": len(admin_drones),
            "matching_admin_count": len(matching_admin),
            "drone_id": matching_admin[0]["drone_id"],
        }
    )
)
PY
)"

public_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["public_id"])' "$smoke_output")"
drone_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["drone_id"])' "$smoke_output")"

db_checks="$("$container_cli" compose exec -T "$db_service" \
  psql -U drone -d drone_app -At -F '|' -c "
    SELECT
      (SELECT COUNT(*) FROM flight_plans WHERE public_id = '${public_id}') AS plan_count,
      (SELECT COUNT(*) FROM drone_devices WHERE flight_plan_public_id = '${public_id}') AS device_count,
      (SELECT COUNT(*) FROM drone_telemetry WHERE flight_plan_public_id = '${public_id}') AS telemetry_count
  ")"

IFS='|' read -r plan_count device_count telemetry_count <<<"$db_checks"

if [ "${plan_count:-0}" -lt 1 ]; then
  echo "[drone-telemetry-smoke] Flight plan was not stored in PostgreSQL." >&2
  exit 1
fi
if [ "${device_count:-0}" -lt 1 ]; then
  echo "[drone-telemetry-smoke] Drone device row was not stored in PostgreSQL." >&2
  exit 1
fi
if [ "${telemetry_count:-0}" -lt 1 ]; then
  echo "[drone-telemetry-smoke] Drone telemetry row was not stored in PostgreSQL." >&2
  exit 1
fi

echo "[drone-telemetry-smoke] public_id=$public_id drone_id=$drone_id telemetry_rows=$telemetry_count"

"$container_cli" compose exec -T "$db_service" \
  psql -U drone -d drone_app -c "
    DELETE FROM drone_telemetry WHERE flight_plan_public_id = '${public_id}';
    DELETE FROM drone_devices WHERE flight_plan_public_id = '${public_id}';
    DELETE FROM flight_plans WHERE public_id = '${public_id}';
    DELETE FROM app_users WHERE email = '${smoke_email}';
  " >/dev/null

echo "[drone-telemetry-smoke] Compose telemetry smoke passed."
