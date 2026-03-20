#!/usr/bin/env python3
"""
visualise_zones.py  -  ROMATSA Multi-Layer Map Server
─────────────────────────────────────────────────────
Interactive Leaflet map showing all seven ROMATSA aeronautical data layers:

  1. UAS restriction zones   (permanent polygons)
  2. NOTAM UAS zones         (temporary drone-specific NOTAMs)
  3. All active NOTAMs        (full LRBB NOTAM set - point markers)
  4. CTR airspace             (control zones around airports)
  5. TMA airspace             (terminal manoeuvring areas)
  6. Airports                 (Romanian airports - markers)
  7. Lower ATS routes         (IFR route segments)

Features
--------
  * Layer toggle panel  -  show/hide each layer independently
  * Drone / GA mode     -  drone mode highlights UAS &amp; NOTAM layers,
                           GA mode highlights routes &amp; airspace
  * Altitude slider     -  filters zones that DON'T cover the set altitude
  * Click cross-check   -  click the map to list every zone that contains
                           that point at the current altitude
  * Zone search         -  jump to any zone / NOTAM / airport by ID

Usage
-----
  python3 scripts/visualise_zones.py                 # port 5174
  python3 scripts/visualise_zones.py --port 9000
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import threading
import time
import sys
import uuid
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modules.auth.module import build_auth_module
from backend.airspace.ingestion.pipeline import SOURCES as AIRSPACE_INGESTION_SOURCES
from backend.airspace.repositories.admin_repository import AirspaceAdminRepository
from backend.airspace.services.admin_overview_service import AirspaceAdminOverviewService
from backend.drone_tracking.repositories.drone_tracking_repository import DroneTrackingRepository
from backend.drone_tracking.services.mock_telemetry_service import DroneMockTelemetryService
from backend.drone_tracking.services.scene_3d_service import Drone3DSceneService
from backend.airspace.services.airspace_query_service import (
    build_airspace_query_service,
    normalize_categories as _normalize_airspace_categories,
)
from modules.flight_plans.module import build_flight_plans_module
from backend_auth import (
    clear_session_cookie_header,
    create_session_token,
    session_cookie_header,
    session_user_from_headers,
)
from flight_plan_repository import (
    FlightPlanRepositoryError,
    cancel_flight_plan as _cancel_flight_plan_db,
    create_flight_plan as _store_flight_plan,
    get_flight_plan as _get_flight_plan,
    list_flight_plans as _list_flight_plans_db,
    upsert_app_user as _upsert_app_user,
)

# Flight plan manager (PDF + contacts)
_FM_PATH = Path(__file__).resolve().parent / "flight_plan_manager.py"
if _FM_PATH.exists():
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("flight_plan_manager", _FM_PATH)
    _fm_mod = _ilu.module_from_spec(_spec)          # type: ignore[arg-type]
    _spec.loader.exec_module(_fm_mod)               # type: ignore[union-attr]
    import sys as _sys
    _sys.modules["flight_plan_manager"] = _fm_mod
    import flight_plan_manager as _fm
    TOWER_CONTACTS = _fm.TOWER_CONTACTS
    _area_check    = _fm.area_check
    _assess_flight_area = _fm.assess_flight_area
    _crosscheck_point = _fm.crosscheck_point
    _check_point = _fm.check_point
    _check_route = _fm.check_route
    _build_circle_area = _fm.build_circle_area
    _build_polygon_area = _fm.build_polygon_area
    _build_flight_plan = _fm.validate_and_build_flight_plan
    _generate_anexa1_pdf = _fm.generate_anexa1_pdf
    _twr_options = _fm.available_twr_options
    _flight_plan_error = _fm.FlightPlanValidationError
else:
    TOWER_CONTACTS = {}
    _area_check    = None
    _assess_flight_area = None
    _crosscheck_point = None
    _check_point = None
    _check_route = None
    _build_circle_area = None
    _build_polygon_area = None
    _build_flight_plan = None
    _generate_anexa1_pdf = None
    _twr_options = lambda: []
    _flight_plan_error = ValueError

SCRIPT_DIR = Path(__file__).resolve().parent
ASSET_DIR  = SCRIPT_DIR.parent / "mobile_app" / "assets"
LOGGED_ACCOUNTS_FILE = SCRIPT_DIR.parent / ".data" / "logged_accounts.json"
FLIGHT_PLAN_PDF_DIR = SCRIPT_DIR.parent / ".data" / "flight_plans"
GOOGLE_WEB_CLIENT_ID = os.environ.get(
    "DRONE_GOOGLE_WEB_CLIENT_ID",
    "1082596673448-0k7mnlrj1vt9pkrs1vuh8ar68arsj6mt.apps.googleusercontent.com",
)
CESIUM_ION_TOKEN = os.environ.get("DRONE_CESIUM_ION_TOKEN", "").strip()
AIRSPACE_QUERY_SERVICE = build_airspace_query_service()
AIRSPACE_ADMIN_REPO = AirspaceAdminRepository()
AIRSPACE_ADMIN_OVERVIEW_SERVICE = AirspaceAdminOverviewService(
    admin_repo=AIRSPACE_ADMIN_REPO,
    sources=AIRSPACE_INGESTION_SOURCES,
)
DRONE_TRACKING_REPO = DroneTrackingRepository()
DRONE_MOCK_TELEMETRY_SERVICE = DroneMockTelemetryService(DRONE_TRACKING_REPO)
DRONE_3D_SCENE_SERVICE = Drone3DSceneService(
    drone_repo=DRONE_TRACKING_REPO,
    airspace_query_service=AIRSPACE_QUERY_SERVICE,
    cesium_ion_token=CESIUM_ION_TOKEN,
)
AUTO_DEMO_FLIGHT_PLAN_ENABLED = os.environ.get("DRONE_AUTO_DEMO_FLIGHT_PLAN", "1").strip().lower() not in {"0", "false", "no"}
MOCK_DRONE_ENABLED = os.environ.get("DRONE_ENABLE_MOCK_TELEMETRY", "1").strip().lower() not in {"0", "false", "no"}
MOCK_DRONE_INTERVAL_SECONDS = max(1.0, float(os.environ.get("DRONE_MOCK_TELEMETRY_INTERVAL", "3")))
_mock_drone_stop = threading.Event()
_mock_drone_thread: threading.Thread | None = None

LAYER_FILES = {
    "uas_zones":    ASSET_DIR / "restriction_zones.geojson",
    "notam":        ASSET_DIR / "notam_zones.geojson",
    "notam_all":    ASSET_DIR / "notam_all.geojson",
    "ctr":          ASSET_DIR / "airspace_ctr.geojson",
    "tma":          ASSET_DIR / "airspace_tma.geojson",
    "airports":     ASSET_DIR / "airports.geojson",
    "lower_routes": ASSET_DIR / "lower_routes.geojson",
}

ADMIN_HTML = """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Drone Backend - Logged Accounts</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #101218; color: #e8ecf1; }
    .toolbar { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; margin-bottom: 12px; }
    table { border-collapse: collapse; width: 100%; background: #181d27; }
    th, td { border: 1px solid #2f3747; padding: 10px; text-align: left; font-size: 14px; }
    th { background: #202838; }
    .muted { color: #98a2b3; margin-bottom: 10px; }
    .empty { color: #98a2b3; text-align: center; padding: 20px; }
  </style>
</head>
<body>
  <div class=\"toolbar\">
    <h1>Logged Google Accounts</h1>
    <div class=\"muted\" id=\"summary\">Auto-refresh every 8 seconds</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Email</th>
        <th>Name</th>
        <th>User ID</th>
        <th>First Seen (UTC)</th>
        <th>Last Seen (UTC)</th>
        <th>Last IP</th>
        <th>Source App</th>
      </tr>
    </thead>
    <tbody id=\"rows\"></tbody>
  </table>
  <script>
    async function loadRows() {
      const res = await fetch('/api/auth/sessions');
      if (!res.ok) return;
      const data = await res.json();
      const rows = document.getElementById('rows');
      const accounts = data.accounts || [];
      document.getElementById('summary').textContent =
        `${accounts.length} account${accounts.length === 1 ? '' : 's'} recorded`;
      rows.innerHTML = '';
      if (accounts.length === 0) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td class=\"empty\" colspan=\"7\">No Google logins recorded yet.</td>';
        rows.appendChild(tr);
        return;
      }
      for (const a of accounts) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${a.email || ''}</td>
          <td>${a.display_name || ''}</td>
          <td>${a.google_user_id || ''}</td>
          <td>${a.first_seen || ''}</td>
          <td>${a.last_seen || ''}</td>
          <td>${a.last_ip || ''}</td>
          <td>${a.last_app || ''}</td>
        `;
        rows.appendChild(tr);
      }
    }
    loadRows();
    setInterval(loadRows, 8000);
  </script>
</body>
</html>
"""

FLIGHT_PLAN_ADMIN_HTML = """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Drone Backend - Flight Plans</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #101218; color: #e8ecf1; }
    .toolbar { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; margin-bottom: 12px; }
    table { border-collapse: collapse; width: 100%; background: #181d27; }
    th, td { border: 1px solid #2f3747; padding: 10px; text-align: left; font-size: 14px; vertical-align: top; }
    th { background: #202838; }
    .muted { color: #98a2b3; }
    .badge { display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }
    .badge.ongoing { background: #1f6feb; color: #fff; }
    .badge.upcoming { background: #238636; color: #fff; }
    .badge.completed { background: #30363d; color: #fff; }
    .badge.cancelled { background: #da3633; color: #fff; }
    .badge.low { background: #238636; color: #fff; }
    .badge.medium { background: #d29922; color: #111; }
    .badge.high { background: #da3633; color: #fff; }
    .empty { color: #98a2b3; text-align: center; padding: 20px; }
    a { color: #58a6ff; }
  </style>
</head>
<body>
  <div class=\"toolbar\">
    <h1>Stored Flight Plans</h1>
    <div class=\"muted\" id=\"summary\">Auto-refresh every 10 seconds</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Plan</th>
        <th>Owner</th>
        <th>Schedule</th>
        <th>Location</th>
        <th>TWR</th>
        <th>Risk</th>
        <th>PDF</th>
      </tr>
    </thead>
    <tbody id=\"rows\"></tbody>
  </table>
  <script>
    function badge(label, klass) {
      return `<span class=\"badge ${klass}\">${label}</span>`;
    }

    async function loadRows() {
      const res = await fetch('/api/flight-plans?scope=all&include_past=1');
      if (!res.ok) return;
      const data = await res.json();
      const plans = data.flight_plans || [];
      document.getElementById('summary').textContent =
        `${plans.length} stored flight plan${plans.length === 1 ? '' : 's'}`;

      const rows = document.getElementById('rows');
      rows.innerHTML = '';
      if (plans.length === 0) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td class=\"empty\" colspan=\"7\">No flight plans stored yet.</td>';
        rows.appendChild(tr);
        return;
      }

      for (const plan of plans) {
        const tr = document.createElement('tr');
        const runtimeState = (plan.runtime_state || 'upcoming').toLowerCase();
        const riskState = (plan.risk_level || 'LOW').toLowerCase();
        const pdfLink = plan.public_id
          ? `<a href=\"/api/flight-plans/${plan.public_id}/pdf\" target=\"_blank\">Download PDF</a>`
          : '-';

        tr.innerHTML = `
          <td>
            <div><strong>${plan.public_id || ''}</strong></div>
            <div class=\"muted\">${badge(runtimeState, runtimeState)} ${badge(plan.workflow_status || 'planned', (plan.workflow_status || 'planned').toLowerCase())}</div>
          </td>
          <td>
            <div>${plan.owner_display_name || plan.owner_email || ''}</div>
            <div class=\"muted\">${plan.owner_email || ''}</div>
          </td>
          <td>
            <div>${plan.scheduled_start_local || ''}</div>
            <div class=\"muted\">until ${plan.scheduled_end_local || ''}</div>
          </td>
          <td>
            <div>${plan.location_name || ''}</div>
            <div class=\"muted\">${plan.area_kind || ''} / ${Math.round(plan.max_altitude_m || 0)} m</div>
          </td>
          <td>${plan.selected_twr || ''}</td>
          <td>
            <div>${badge(plan.risk_level || 'LOW', riskState)}</div>
            <div class=\"muted\">${plan.risk_summary || ''}</div>
          </td>
          <td>${pdfLink}</td>
        `;
        rows.appendChild(tr);
      }
    }

    loadRows();
    setInterval(loadRows, 10000);
  </script>
</body>
</html>
"""

ADMIN_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Drone Backend - Admin</title>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\"/>
  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
  <style>
    :root {
      --bg: #0f141c;
      --panel: #171d28;
      --panel-alt: #111722;
      --line: #2a3444;
      --text: #edf2f8;
      --muted: #93a1b5;
      --accent: #58a6ff;
      --ok: #238636;
      --warn: #d29922;
      --danger: #da3633;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Arial, sans-serif; background: var(--bg); color: var(--text); }
    .page { padding: 20px; max-width: 1800px; margin: 0 auto; }
    .toolbar { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
    .toolbar h1 { margin: 0 0 6px 0; font-size: 28px; }
    .muted { color: var(--muted); }
    .links a { color: var(--accent); text-decoration: none; margin-left: 12px; }
    .summary-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
    .summary-card { background: linear-gradient(180deg, #1a2230 0%, #141b26 100%); border: 1px solid var(--line); border-radius: 14px; padding: 14px; }
    .summary-card .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }
    .summary-card .value { font-size: 28px; font-weight: 700; }
    .summary-card .sub { font-size: 12px; color: var(--muted); margin-top: 6px; }
    .panel-grid { display: grid; grid-template-columns: 1.1fr 1.3fr 1.1fr; gap: 14px; align-items: start; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; min-height: 280px; }
    .panel .head { padding: 14px 16px; background: var(--panel-alt); border-bottom: 1px solid var(--line); }
    .panel .head h2 { margin: 0 0 4px 0; font-size: 18px; }
    .panel .body { padding: 0; }
    .table-wrap { overflow: auto; max-height: 540px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid var(--line); padding: 10px 12px; text-align: left; font-size: 13px; vertical-align: top; }
    th { position: sticky; top: 0; background: #1a2230; z-index: 1; }
    .empty { color: var(--muted); padding: 18px; text-align: center; }
    .badge { display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
    .badge.ok, .badge.ongoing, .badge.upcoming, .badge.low, .badge.activated { background: rgba(35, 134, 54, .18); color: #67d480; }
    .badge.warn, .badge.medium, .badge.duplicate, .badge.completed { background: rgba(210, 153, 34, .18); color: #f2c94c; }
    .badge.danger, .badge.high, .badge.cancelled, .badge.failed, .badge.error { background: rgba(218, 54, 51, .18); color: #ff8e8a; }
    .badge.info, .badge.planned { background: rgba(88, 166, 255, .18); color: #85c1ff; }
    .stack { display: flex; flex-direction: column; gap: 4px; }
    .small { font-size: 12px; color: var(--muted); }
    .bottom-grid { display: grid; grid-template-columns: 1fr 1fr 1.15fr; gap: 14px; margin-top: 14px; }
    .mono { font-family: monospace; word-break: break-all; }
    a { color: var(--accent); }
    .drone-map { height: 260px; border-top: 1px solid var(--line); }
    .drone-table { max-height: 280px; overflow: auto; border-top: 1px solid var(--line); }
    @media (max-width: 1280px) {
      .panel-grid, .summary-grid, .bottom-grid { grid-template-columns: 1fr; }
      .table-wrap { max-height: none; }
    }
  </style>
</head>
<body>
  <div class=\"page\">
    <div class=\"toolbar\">
      <div>
        <h1>Admin Dashboard</h1>
        <div class=\"muted\">Operational view of authenticated users, flight plans, and airspace ingestion state.</div>
      </div>
      <div class=\"links muted\">
        <span id=\"refreshLabel\">Auto-refresh every 10 seconds</span>
        <a href=\"/\">Map</a>
        <a href=\"/admin/logged-accounts\">Logins</a>
        <a href=\"/admin/flight-plans\">Flight Plans</a>
      </div>
    </div>

    <div class=\"summary-grid\">
      <div class=\"summary-card\">
        <div class=\"label\">Recorded Logins</div>
        <div class=\"value\" id=\"summaryAccounts\">0</div>
        <div class=\"sub\" id=\"summaryAccountsSub\">No logins recorded yet</div>
      </div>
      <div class=\"summary-card\">
        <div class=\"label\">Stored Flight Plans</div>
        <div class=\"value\" id=\"summaryPlans\">0</div>
        <div class=\"sub\" id=\"summaryPlansSub\">No flight plans stored yet</div>
      </div>
      <div class=\"summary-card\">
        <div class=\"label\">Active Airspace Sources</div>
        <div class=\"value\" id=\"summarySources\">0</div>
        <div class=\"sub\" id=\"summarySourcesSub\">No active airspace datasets</div>
      </div>
      <div class=\"summary-card\">
        <div class=\"label\">Recent Ingestion Issues</div>
        <div class=\"value\" id=\"summaryIssues\">0</div>
        <div class=\"sub\" id=\"summaryIssuesSub\">No recent issues</div>
      </div>
      <div class=\"summary-card\">
        <div class=\"label\">Live Drones</div>
        <div class=\"value\" id=\"summaryLiveDrones\">0</div>
        <div class=\"sub\" id=\"summaryLiveDronesSub\">No active drone telemetry</div>
      </div>
    </div>

    <div class=\"panel-grid\">
      <section class=\"panel\">
        <div class=\"head\">
          <h2>Logged Accounts</h2>
          <div class=\"muted\" id=\"accountsCaption\"></div>
        </div>
        <div class=\"body table-wrap\">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Last Seen</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody id=\"accountsRows\"></tbody>
          </table>
        </div>
      </section>

      <section class=\"panel\">
        <div class=\"head\">
          <h2>Flight Plans</h2>
          <div class=\"muted\" id=\"plansCaption\"></div>
        </div>
        <div class=\"body table-wrap\">
          <table>
            <thead>
              <tr>
                <th>Plan</th>
                <th>Owner</th>
                <th>Schedule</th>
                <th>Risk</th>
              </tr>
            </thead>
            <tbody id=\"plansRows\"></tbody>
          </table>
        </div>
      </section>

      <section class=\"panel\">
        <div class=\"head\">
          <h2>Airspace Datasets</h2>
          <div class=\"muted\" id=\"airspaceCaption\"></div>
        </div>
        <div class=\"body table-wrap\">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Last Ingestion</th>
                <th>Features</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody id=\"airspaceRows\"></tbody>
          </table>
        </div>
      </section>
    </div>

    <div class=\"bottom-grid\">
      <section class=\"panel\">
        <div class=\"head\">
          <h2>Active Airspace Versions</h2>
          <div class=\"muted\">Currently active dataset versions used by viewport rendering and checks.</div>
        </div>
        <div class=\"body table-wrap\">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Version</th>
                <th>Imported</th>
                <th>Features</th>
                <th>Checksum</th>
              </tr>
            </thead>
            <tbody id=\"versionsRows\"></tbody>
          </table>
        </div>
      </section>

      <section class=\"panel\">
        <div class=\"head\">
          <h2>Recent Ingestion Events</h2>
          <div class=\"muted\">Recent raw fetch attempts and any issues that need operator attention.</div>
        </div>
        <div class=\"body table-wrap\">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Fetched At</th>
                <th>Status</th>
                <th>Checksum</th>
              </tr>
            </thead>
            <tbody id=\"eventsRows\"></tbody>
          </table>
        </div>
      </section>

      <section class=\"panel\">
        <div class=\"head\">
          <h2>Live Drones</h2>
          <div class=\"muted\" id=\"liveDronesCaption\">Currently flying drones tied to active flight plans.</div>
        </div>
        <div id=\"adminDroneMap\" class=\"drone-map\"></div>
        <div class=\"drone-table\">
          <table>
            <thead>
              <tr>
                <th>Drone</th>
                <th>Flight Plan</th>
                <th>Position</th>
                <th>Telemetry</th>
              </tr>
            </thead>
            <tbody id=\"liveDroneRows\"></tbody>
          </table>
        </div>
      </section>
    </div>
  </div>

  <script>
    let adminDroneMap = null;
    let adminDroneLayer = null;

    function formatValue(value, fallback) {
      return value == null || value === '' ? (fallback || '-') : value;
    }

    function shortChecksum(value) {
      if (!value) return '-';
      return value.slice(0, 12);
    }

    function badge(label, klass) {
      return `<span class=\"badge ${klass}\">${label}</span>`;
    }

    function statusBadge(value) {
      const normalized = String(value || 'unknown').toLowerCase();
      if (normalized === 'activated' || normalized === 'ongoing' || normalized === 'upcoming' || normalized === 'low') {
        return badge(value, 'ok');
      }
      if (normalized === 'duplicate' || normalized === 'completed' || normalized === 'medium') {
        return badge(value, 'warn');
      }
      if (normalized === 'failed' || normalized === 'error' || normalized === 'cancelled' || normalized === 'high') {
        return badge(value, 'danger');
      }
      return badge(value, 'info');
    }

    function renderEmpty(tbody, colspan, message) {
      tbody.innerHTML = `<tr><td class=\"empty\" colspan=\"${colspan}\">${message}</td></tr>`;
    }

    function renderAccounts(accounts) {
      document.getElementById('summaryAccounts').textContent = String(accounts.length);
      document.getElementById('summaryAccountsSub').textContent =
        accounts.length ? `Last seen ${formatValue(accounts[0].last_seen_at, 'n/a')}` : 'No logins recorded yet';
      document.getElementById('accountsCaption').textContent =
        `${accounts.length} account${accounts.length === 1 ? '' : 's'} recorded`;
      const rows = document.getElementById('accountsRows');
      if (!accounts.length) {
        renderEmpty(rows, 4, 'No Google logins recorded yet.');
        return;
      }
      rows.innerHTML = accounts.map(function(a) {
        return `
          <tr>
            <td class=\"stack\"><strong>${formatValue(a.email)}</strong><span class=\"small\">${formatValue(a.google_user_id)}</span></td>
            <td>${formatValue(a.display_name)}</td>
            <td class=\"stack\"><span>${formatValue(a.last_seen_at)}</span><span class=\"small\">IP ${formatValue(a.last_ip)}</span></td>
            <td>${formatValue(a.source_app)}</td>
          </tr>
        `;
      }).join('');
    }

    function renderFlightPlans(plans) {
      document.getElementById('summaryPlans').textContent = String(plans.length);
      const activeCount = plans.filter(function(plan) {
        const state = String(plan.runtime_state || '').toLowerCase();
        return state === 'ongoing' || state === 'upcoming';
      }).length;
      document.getElementById('summaryPlansSub').textContent =
        `${activeCount} active or upcoming`;
      document.getElementById('plansCaption').textContent =
        `${plans.length} stored flight plan${plans.length === 1 ? '' : 's'}`;
      const rows = document.getElementById('plansRows');
      if (!plans.length) {
        renderEmpty(rows, 4, 'No flight plans stored yet.');
        return;
      }
      rows.innerHTML = plans.map(function(plan) {
        const runtimeState = String(plan.runtime_state || 'upcoming').toLowerCase();
        const workflowState = String(plan.workflow_status || 'planned').toLowerCase();
        const riskLevel = String(plan.risk_level || 'LOW');
        const pdfLink = plan.public_id
          ? `<a href=\"/api/flight-plans/${plan.public_id}\" target=\"_blank\"></a>`
          : '';
        return `
          <tr>
            <td class=\"stack\">
              <strong>${formatValue(plan.public_id)}</strong>
              <span>${statusBadge(runtimeState)} ${statusBadge(workflowState)}</span>
              <span class=\"small\">${formatValue(plan.location_name)}</span>
            </td>
            <td class=\"stack\">
              <span>${formatValue(plan.owner_display_name || plan.owner_email)}</span>
              <span class=\"small\">${formatValue(plan.owner_email)}</span>
            </td>
            <td class=\"stack\">
              <span>${formatValue(plan.scheduled_start_local)}</span>
              <span class=\"small\">until ${formatValue(plan.scheduled_end_local)}</span>
            </td>
            <td class=\"stack\">
              <span>${statusBadge(riskLevel)}</span>
              <span class=\"small\">${formatValue(plan.risk_summary)}</span>
            </td>
          </tr>
        `;
      }).join('');
    }

    function ensureAdminDroneMap() {
      if (adminDroneMap) return;
      adminDroneMap = L.map('adminDroneMap', { zoomControl: true }).setView([45.9, 25.0], 6);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap',
        maxZoom: 19,
      }).addTo(adminDroneMap);
      adminDroneLayer = L.layerGroup().addTo(adminDroneMap);
    }

    function droneBadgeClass(status) {
      const normalized = String(status || 'unknown').toLowerCase();
      if (normalized === 'flying') return 'ok';
      if (normalized === 'scheduled') return 'warn';
      return 'info';
    }

    function renderLiveDrones(drones) {
      ensureAdminDroneMap();
      document.getElementById('summaryLiveDrones').textContent = String(drones.length);
      document.getElementById('summaryLiveDronesSub').textContent =
        drones.length ? `Latest ping ${formatValue(drones[0].timestamp)}` : 'No active drone telemetry';
      document.getElementById('liveDronesCaption').textContent =
        `${drones.length} drone${drones.length === 1 ? '' : 's'} currently airborne on ongoing flight plans`;

      const rows = document.getElementById('liveDroneRows');
      if (!drones.length) {
        renderEmpty(rows, 4, 'No live drones currently flying.');
      } else {
        rows.innerHTML = drones.map(function(drone) {
          return `
            <tr>
              <td class=\"stack\">
                <strong>${formatValue(drone.drone_id)}</strong>
                <span class=\"small\">${formatValue(drone.label)}</span>
                <span>${badge(formatValue(drone.status, 'unknown'), droneBadgeClass(drone.status))}</span>
              </td>
              <td class=\"stack\">
                <span>${formatValue(drone.flight_plan_public_id)}</span>
                <span class=\"small\">${formatValue(drone.location_name)}</span>
              </td>
              <td class=\"stack\">
                <span>${Number(drone.latitude || 0).toFixed(5)}, ${Number(drone.longitude || 0).toFixed(5)}</span>
                <span class=\"small\">Alt ${Number(drone.altitude || 0).toFixed(1)} m</span>
              </td>
              <td class=\"stack\">
                <span>SPD ${Number(drone.speed || 0).toFixed(1)} m/s | BAT ${Number(drone.battery_level || 0).toFixed(0)}%</span>
                <span class=\"small\">HDG ${Number(drone.heading || 0).toFixed(0)} | ${formatValue(drone.timestamp)}</span>
              </td>
            </tr>
          `;
        }).join('');
      }

      adminDroneLayer.clearLayers();
      if (!drones.length) return;

      const bounds = [];
      drones.forEach(function(drone) {
        const lat = Number(drone.latitude);
        const lon = Number(drone.longitude);
        const heading = Number(drone.heading || 0).toFixed(0);
        const icon = L.divIcon({
          className: 'admin-drone-marker',
          html: `<div style=\"background:#58a6ff;color:#071018;border:1px solid rgba(255,255,255,.25);border-radius:999px;padding:4px 8px;font-size:11px;font-weight:700;box-shadow:0 4px 12px rgba(0,0,0,.35)\">${drone.drone_id || 'DRONE'} | ${heading}&deg;</div>`,
          iconSize: [130, 28],
          iconAnchor: [65, 14],
        });
        L.marker([lat, lon], { icon: icon })
          .bindPopup(`<strong>${drone.drone_id || ''}</strong><br/>${formatValue(drone.location_name)}<br/>Alt ${Number(drone.altitude || 0).toFixed(1)} m<br/>Battery ${Number(drone.battery_level || 0).toFixed(0)}%`)
          .addTo(adminDroneLayer);
        bounds.push([lat, lon]);
      });
      if (bounds.length === 1) {
        adminDroneMap.setView(bounds[0], 13);
      } else {
        adminDroneMap.fitBounds(bounds, { padding: [24, 24], maxZoom: 14 });
      }
    }

    function renderAirspace(airspace) {
      const sources = airspace.sources || [];
      const versions = airspace.active_versions || [];
      const events = airspace.recent_events || [];
      const issues = airspace.recent_issues || [];
      const latestSource = sources.reduce(function(current, item) {
        if (!current) return item;
        return String(item.last_ingested_at || '') > String(current.last_ingested_at || '') ? item : current;
      }, null);

      document.getElementById('summarySources').textContent = String(sources.length);
      document.getElementById('summarySourcesSub').textContent =
        latestSource ? `Latest active import ${formatValue(latestSource.last_ingested_at, 'n/a')}` : 'No active airspace datasets';
      document.getElementById('summaryIssues').textContent = String(issues.length);
      document.getElementById('summaryIssuesSub').textContent =
        issues.length ? `Last issue ${formatValue(issues[0].fetched_at, 'n/a')}` : 'No recent issues';
      document.getElementById('airspaceCaption').textContent =
        `${sources.length} active source${sources.length === 1 ? '' : 's'}`;

      const sourceRows = document.getElementById('airspaceRows');
      if (!sources.length) {
        renderEmpty(sourceRows, 4, 'No active airspace datasets yet.');
      } else {
        sourceRows.innerHTML = sources.map(function(source) {
          const issueText = source.error_count > 0 ? ` / ${source.error_count} recent issue(s)` : '';
          return `
            <tr>
              <td class=\"stack\">
                <strong>${formatValue(source.label || source.source)}</strong>
                <span class=\"small mono\">${formatValue(source.source)}</span>
              </td>
              <td class=\"stack\">
                <span>${formatValue(source.last_ingested_at)}</span>
                <span class=\"small\">schedule ${formatValue(source.schedule_label)}</span>
              </td>
              <td class=\"stack\">
                <span>${formatValue(source.record_count, 0)}</span>
                <span class=\"small\">active features</span>
              </td>
              <td class=\"stack\">
                <span>${statusBadge(source.last_status || 'unknown')}</span>
                <span class=\"small\">${formatValue(source.last_fetch_at)}${issueText}</span>
              </td>
            </tr>
          `;
        }).join('');
      }

      const versionRows = document.getElementById('versionsRows');
      if (!versions.length) {
        renderEmpty(versionRows, 5, 'No active dataset versions yet.');
      } else {
        versionRows.innerHTML = versions.map(function(version) {
          return `
            <tr>
              <td>${formatValue(version.source)}</td>
              <td class=\"mono\">${formatValue(version.version_id)}</td>
              <td>${formatValue(version.imported_at)}</td>
              <td>${formatValue(version.zone_count, 0)}</td>
              <td class=\"mono\">${shortChecksum(version.checksum)}</td>
            </tr>
          `;
        }).join('');
      }

      const eventRows = document.getElementById('eventsRows');
      if (!events.length) {
        renderEmpty(eventRows, 4, 'No ingestion events stored yet.');
      } else {
        eventRows.innerHTML = events.map(function(event) {
          return `
            <tr>
              <td>${formatValue(event.source)}</td>
              <td>${formatValue(event.fetched_at)}</td>
              <td>${statusBadge(event.status || 'unknown')}</td>
              <td class=\"mono\">${shortChecksum(event.checksum)}</td>
            </tr>
          `;
        }).join('');
      }
    }

    async function loadDashboard() {
      const res = await fetch('/api/admin/overview');
      if (!res.ok) {
        throw new Error('Failed to load admin overview');
      }
      const data = await res.json();
      renderAccounts(data.accounts || []);
      renderFlightPlans(data.flight_plans || []);
      renderAirspace(data.airspace || {});
      renderLiveDrones(data.live_drones || []);
      document.getElementById('refreshLabel').textContent =
        `Auto-refresh every 10 seconds | Last refresh ${new Date().toLocaleTimeString()}`;
    }

    loadDashboard().catch(function(err) {
      console.error(err);
    });
    setInterval(function() {
      loadDashboard().catch(function(err) {
        console.error(err);
      });
    }, 10000);
  </script>
</body>
</html>
"""

# ──────────────────────────────────────────────────────────────────────────
# HTML page
# ──────────────────────────────────────────────────────────────────────────

HTML = b"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>ROMATSA Mirror - Multi-Layer Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {
    --bg: #0d1117; --bg2: #161b22; --border: #30363d;
    --accent: #e94560; --blue: #58a6ff; --green: #3fb950;
    --orange: #d29922; --purple: #bc8cff; --cyan: #39d2c0;
    --text: #c9d1d9; --muted: #8b949e;
    --sidebar-w: 364px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, sans-serif; background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }

  /* Auth gate */
  #authGate {
    position: fixed; inset: 0; z-index: 3000;
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
    background:
      radial-gradient(circle at top, rgba(88,166,255,.18), transparent 32%),
      linear-gradient(180deg, rgba(5,8,12,.88), rgba(5,8,12,.96));
    backdrop-filter: blur(10px);
  }
  .auth-card {
    width: min(460px, 100%); padding: 28px 24px;
    background: rgba(22,27,34,.96); border: 1px solid rgba(233,69,96,.3);
    border-radius: 20px; box-shadow: 0 24px 60px rgba(0,0,0,.55);
  }
  .auth-chip {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 12px; border-radius: 999px;
    background: rgba(233,69,96,.12); color: #ffb4ab;
    font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase;
    margin-bottom: 14px;
  }
  .auth-card h1 { font-size: 1.9rem; line-height: 1.05; margin-bottom: 12px; }
  .auth-card p { color: var(--muted); font-size: 0.95rem; line-height: 1.5; margin-bottom: 18px; }
  .auth-card code {
    padding: 2px 6px; border-radius: 6px;
    background: rgba(88,166,255,.12); color: #cfe7ff;
  }
  #googleLoginButton { min-height: 44px; display: flex; justify-content: center; }
  .auth-error {
    min-height: 20px; margin-top: 14px;
    color: #ffb4ab; font-size: 0.84rem; text-align: center;
  }
  .auth-note { margin-top: 16px; font-size: 0.78rem; color: var(--muted); text-align: center; }

  /* Sidebar */
  #sidebar {
    width: var(--sidebar-w); min-width: var(--sidebar-w); background: var(--bg2); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; overflow-y: auto; z-index: 1001;
  }
  .sb-header {
    padding: 14px 16px; border-bottom: 1px solid var(--border); display: flex;
    align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .sb-header h1 { font-size: 0.95rem; font-weight: 700; color: var(--accent); flex: 1; }
  .sb-header .flag { font-size: 1.2rem; }
  .auth-user {
    width: 100%; display: flex; align-items: center; justify-content: space-between;
    gap: 12px; padding-top: 8px; border-top: 1px solid rgba(48,54,61,.7);
  }
  .auth-user-meta { min-width: 0; }
  .auth-user-name {
    font-size: 0.82rem; font-weight: 700; color: #f0f6fc;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .auth-user-email {
    font-size: 0.72rem; color: var(--muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .auth-signout {
    padding: 6px 10px; border: 1px solid var(--border); border-radius: 999px;
    background: transparent; color: var(--text); font-size: 0.74rem; cursor: pointer;
  }
  .auth-signout:hover { border-color: var(--accent); color: #fff; }

  .sb-section { padding: 14px 18px; border-bottom: 1px solid var(--border); }
  .sb-section h2 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 8px; }
  .sb-caption { color: var(--muted); font-size: 0.73rem; line-height: 1.45; margin: 6px 0 0; }

  .mode-toggle { display: flex; gap: 4px; }
  .mode-btn {
    flex: 1; padding: 6px 0; border: 1px solid var(--border); border-radius: 6px;
    background: transparent; color: var(--muted); font-size: 0.78rem;
    cursor: pointer; text-align: center; transition: all .15s;
  }
  .mode-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

  .layer-item { display: flex; align-items: center; gap: 8px; padding: 5px 0; }
  .layer-dot { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
  .layer-name { font-size: 0.8rem; flex: 1; }
  .layer-count { font-size: 0.7rem; color: var(--muted); }
  .layer-cb { accent-color: var(--accent); }

  .alt-row { display: flex; align-items: center; gap: 8px; }
  #altSlider { flex: 1; accent-color: var(--accent); cursor: pointer; }
  #altValue { font-weight: 700; color: var(--accent); font-size: 0.9rem; min-width: 50px; text-align: right; }

  #searchBox {
    width: 100%; padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px;
    background: var(--bg); color: var(--text); font-size: 0.82rem;
  }
  #searchBox::placeholder { color: #484f58; }

  #stats { font-size: 0.72rem; color: var(--muted); padding: 10px 16px; margin-top: auto; border-top: 1px solid var(--border); }

  /* Cross-check panel */
  #crossPanel {
    display: none; position: absolute; bottom: 20px; right: 20px;
    width: 360px; max-height: 50vh; overflow-y: auto; z-index: 1000;
    background: var(--bg2); border: 1px solid var(--border); border-radius: 12px;
    padding: 14px; box-shadow: 0 8px 30px rgba(0,0,0,.5);
  }
  #crossPanel h3 { font-size: 0.85rem; color: var(--accent); margin-bottom: 6px; }
  #crossPanel .close-btn {
    position: absolute; top: 8px; right: 12px; background: none; border: none;
    color: var(--muted); font-size: 1.2rem; cursor: pointer;
  }
  .cross-item { padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.78rem; }
  .cross-item:last-child { border-bottom: none; }
  .cross-layer { font-size: 0.65rem; padding: 1px 6px; border-radius: 10px; color: #fff; margin-right: 6px; }
  .cross-name { font-weight: 600; }
  .cross-alt { color: var(--muted); font-size: 0.72rem; margin-left: 4px; }

  /* Map */
  #map { flex: 1; }

  /* Popups */
  .leaflet-popup-content-wrapper { background: var(--bg2); color: var(--text); border: 1px solid var(--accent); border-radius: 10px; }
  .leaflet-popup-tip { background: var(--bg2); }
  .popup-title { font-weight: 700; font-size: 0.95rem; color: var(--accent); margin-bottom: 4px; }
  .popup-row { display: flex; gap: 6px; font-size: 0.78rem; padding: 1px 0; }
  .popup-lbl { color: var(--muted); min-width: 80px; }
  .popup-val { color: var(--text); word-break: break-word; }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 12px; font-size: 0.65rem; font-weight: 700; color: #fff; }

  /* --- Flight Plan Manager --- */
  .fp-launch-btn {
    width: 100%; padding: 9px; background: linear-gradient(135deg,#e94560,#c0392b);
    color: #fff; border: none; border-radius: 7px; cursor: pointer;
    font-size: 0.85rem; font-weight: 700; letter-spacing:.03em;
    transition: opacity .15s; margin-top: 4px;
  }
  .fp-launch-btn:hover { opacity:.85; }
  .fp-secondary-btn {
    background: var(--bg); border: 1px solid var(--border);
    color: var(--text); margin-top: 10px;
  }
  .my-plans-list { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
  .my-plan-card {
    border: 1px solid var(--border); border-radius: 8px; background: var(--bg);
    padding: 10px; font-size: 0.77rem;
  }
  .my-plan-card .plan-top {
    display: flex; justify-content: space-between; gap: 8px; margin-bottom: 4px;
  }
  .my-plan-card .plan-id { font-weight: 700; color: #f0f6fc; }
  .my-plan-card .plan-meta { color: var(--muted); line-height: 1.45; }
  .plan-actions {
    margin-top: 8px; display: flex; gap: 8px; align-items: center; justify-content: space-between;
  }
  .plan-link {
    color: var(--blue); text-decoration: none; font-weight: 600;
  }
  .plan-link:hover { text-decoration: underline; }
  .mini-btn {
    padding: 5px 9px; border-radius: 999px; border: 1px solid var(--border);
    background: transparent; color: var(--text); font-size: 0.7rem; cursor: pointer;
  }
  .mini-btn:hover { border-color: var(--accent); }
  .mini-btn-danger {
    border-color: rgba(233,69,96,.5); color: #ffb4ab;
  }
  .mini-btn-danger:hover {
    border-color: #e94560; background: rgba(233,69,96,.12);
  }
  .status-pill {
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
  }
  .status-upcoming { background: rgba(63,185,80,.18); color: #8ef0a3; }
  .status-ongoing { background: rgba(88,166,255,.18); color: #b4d8ff; }
  .status-completed { background: rgba(139,148,158,.18); color: #c9d1d9; }
  .status-cancelled { background: rgba(233,69,96,.18); color: #ffb4ab; }
  .risk-pill {
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 0.68rem; font-weight: 700;
  }
  .risk-low { background: rgba(63,185,80,.18); color: #8ef0a3; }
  .risk-medium { background: rgba(210,153,34,.18); color: #ffd48a; }
  .risk-high { background: rgba(233,69,96,.18); color: #ffb4ab; }

  #fpOverlay {
    display: none; position: absolute; top: 0; left: var(--sidebar-w); right: 0; bottom: 0;
    z-index: 2000; background: rgba(0,0,0,.45);
  }
  #fpWizard {
    position: absolute; right: 20px; top: 20px;
    width: 400px; max-height: calc(100vh - 40px);
    background: var(--bg2); border: 1px solid var(--border); border-radius: 14px;
    box-shadow: 0 12px 40px rgba(0,0,0,.7);
    display: flex; flex-direction: column; overflow: hidden;
  }
  #fpWizard .wiz-head {
    padding: 14px 16px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px;
  }
  #fpWizard .wiz-head h2 { font-size:0.95rem; font-weight:700; color:var(--accent); flex:1; }
  #fpWizard .wiz-head .close-wiz {
    background:none; border:none; color:var(--muted); font-size:1.3rem;
    cursor:pointer; line-height:1;
  }
  .step-indicator {
    display: flex; padding: 10px 16px; gap: 4px; background: var(--bg);
    border-bottom: 1px solid var(--border);
  }
  .step-dot {
    flex: 1; height: 4px; border-radius: 2px; background: var(--border);
    transition: background .3s;
  }
  .step-dot.done  { background: var(--green); }
  .step-dot.active{ background: var(--accent); }
  .wiz-body { flex: 1; overflow-y: auto; padding: 14px 16px; }
  .wiz-step { display: none; }
  .wiz-step.active { display: block; }
  .wiz-step h3 { font-size:.82rem; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); margin-bottom:10px; }
  .fp-row { margin-bottom: 10px; }
  .fp-row label { display:block; font-size:.75rem; color:var(--muted); margin-bottom:3px; }
  .fp-row input, .fp-row select, .fp-row textarea {
    width: 100%; padding: 6px 9px; border: 1px solid var(--border);
    border-radius: 6px; background: var(--bg); color: var(--text);
    font-size: 0.82rem; font-family: inherit;
  }
  .fp-row textarea { resize: vertical; min-height: 56px; }
  .fp-row input:focus, .fp-row select:focus { outline: none; border-color: var(--accent); }
  .fp-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .fp-actions { padding: 12px 16px; border-top: 1px solid var(--border); display:flex; gap:8px; }
  .fp-actions button {
    flex:1; padding:8px; border-radius:7px; border:none; cursor:pointer;
    font-size:.82rem; font-weight:600; transition: opacity .15s;
  }
  .btn-primary   { background: var(--accent); color: #fff; }
  .btn-secondary { background: var(--bg); border: 1px solid var(--border) !important; color: var(--text); }
  .btn-success   { background: var(--green); color: #000; }
  .btn-danger    { background: #da3633; color: #fff; }
  .btn-primary:hover, .btn-secondary:hover, .btn-success:hover, .btn-danger:hover { opacity:.85; }
  .risk-badge {
    display:inline-block; padding:3px 10px; border-radius:12px;
    font-size:.75rem; font-weight:700; color:#fff; margin-bottom:8px;
  }
  .risk-LOW    { background:#238636; }
  .risk-MEDIUM { background:#d29922; color:#000; }
  .risk-HIGH   { background:#da3633; }
  .hit-list { font-size:.76rem; }
  .hit-item { padding:5px 8px; margin-bottom:4px; border-radius:6px; background:var(--bg); border:1px solid var(--border); }
  .hit-item .hit-layer { font-size:.65rem; padding:1px 6px; border-radius:9px; color:#fff; margin-right:5px; }
  .contact-card {
    background: var(--bg); border: 1px solid var(--border); border-radius:8px;
    padding: 10px 12px; margin-bottom:8px; font-size:.8rem;
  }
  .contact-card .cc-name { font-weight:700; color:var(--blue); margin-bottom:4px; }
  .contact-card .cc-row  { display:flex; gap:6px; padding:1px 0; }
  .contact-card .cc-lbl  { color:var(--muted); min-width:50px; font-size:.72rem; }
  .inline-btn-row { display:flex; gap:8px; margin-top:8px; }
  .inline-btn {
    flex:1; padding:8px 10px; border-radius:7px; border:1px solid var(--border);
    background: var(--bg); color: var(--text); cursor:pointer; font-size:.78rem;
  }
  .inline-btn:hover { border-color: var(--accent); }
  .warning-box {
    background: rgba(210,153,34,.12); border: 1px solid rgba(210,153,34,.4);
    color: #ffd48a; border-radius: 8px; padding: 10px 12px;
    font-size: .78rem; line-height: 1.45; margin-bottom: 10px;
  }
  .area-note {
    color: var(--muted); font-size: .74rem; line-height: 1.45; margin-top: 6px;
  }
  .saved-plan-card {
    border: 1px solid var(--border); border-radius: 10px; background: var(--bg);
    padding: 12px; margin-bottom: 10px; font-size: .8rem;
  }
  .saved-plan-card .saved-title { font-weight: 700; color: var(--blue); margin-bottom: 6px; }
  .saved-plan-card .saved-row { padding: 2px 0; color: var(--text); }
  .saved-plan-card .saved-row span { color: var(--muted); margin-right: 6px; }
  .drone-card {
    border: 1px solid var(--border); border-radius: 10px; background: rgba(88,166,255,.08);
    padding: 12px; margin-bottom: 10px; font-size: .8rem;
  }
  .drone-card .drone-top {
    display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:8px;
  }
  .drone-card .drone-id { font-weight:700; color: var(--blue); }
  .drone-card .drone-status {
    font-size:.68rem; padding:3px 8px; border-radius:999px; text-transform:uppercase; font-weight:700;
    background: rgba(35,134,54,.18); color:#8ae1a7;
  }
  .drone-card .drone-status.scheduled { background: rgba(210,153,34,.16); color:#ffd48a; }
  .drone-card .drone-status.offline { background: rgba(218,54,51,.16); color:#ff9893; }
  .drone-card .drone-grid {
    display:grid; grid-template-columns: 1fr 1fr; gap:6px 10px; color:var(--text);
  }
  .drone-card .drone-grid span { color: var(--muted); display:block; font-size:.68rem; margin-bottom:1px; }
  .drone-card .drone-actions { display:flex; gap:8px; margin-top:10px; }
  .drone-card .drone-actions button {
    flex:1; border:1px solid rgba(88,166,255,.45); border-radius:8px; background:rgba(88,166,255,.12); color:#d7ecff; padding:9px 12px; cursor:pointer; font-weight:700;
  }
  .drone-card .drone-actions button:hover { border-color: var(--accent); background: rgba(233,69,96,.12); color: #fff; }
  .drone-note {
    margin-top: 8px; padding: 9px 10px; border-radius: 8px;
    background: rgba(210,153,34,.1); border: 1px solid rgba(210,153,34,.35);
    color: #ffd48a; font-size: .73rem; line-height: 1.45;
  }
  #drone3dOverlay {
    display: none; position: absolute; top: 0; left: var(--sidebar-w); right: 0; bottom: 0;
    z-index: 2200; background: rgba(4, 7, 11, .78); backdrop-filter: blur(8px);
  }
  #drone3dShell {
    position: absolute; inset: 18px; display: flex; flex-direction: column;
    background: rgba(13,17,23,.96); border: 1px solid rgba(88,166,255,.18); border-radius: 18px;
    box-shadow: 0 20px 80px rgba(0,0,0,.6); overflow: hidden;
  }
  .drone3d-head {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;
    padding: 16px 18px 12px; border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, rgba(88,166,255,.08), rgba(13,17,23,0));
  }
  .drone3d-head h2 { font-size: 1rem; color: #d7ecff; margin-bottom: 4px; }
  .drone3d-head .sub { color: var(--muted); font-size: .77rem; line-height: 1.45; }
  .drone3d-head-actions {
    display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap;
  }
  .drone3d-close {
    border: 1px solid var(--border); border-radius: 999px; background: transparent;
    color: var(--text); padding: 7px 12px; cursor: pointer; font-weight: 700;
  }
  .drone3d-close:hover { border-color: var(--accent); color: #fff; }
  .drone3d-meta {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px;
    padding: 12px 18px; border-bottom: 1px solid var(--border); background: rgba(22,27,34,.88);
  }
  .drone3d-metric {
    border: 1px solid var(--border); border-radius: 12px; background: rgba(13,17,23,.9);
    padding: 10px 12px;
  }
  .drone3d-metric .label { color: var(--muted); font-size: .69rem; text-transform: uppercase; letter-spacing: .08em; }
  .drone3d-metric .value { color: #f0f6fc; font-size: .92rem; font-weight: 700; margin-top: 4px; }
  .drone3d-stage {
    position: relative; flex: 1; min-height: 320px; background: #05070a;
  }
  #drone3dCanvas { position: absolute; inset: 0; }
  .drone3d-status {
    position: absolute; left: 18px; bottom: 18px; max-width: 360px;
    padding: 10px 12px; border-radius: 12px; border: 1px solid rgba(88,166,255,.24);
    background: rgba(13,17,23,.82); color: var(--text); font-size: .77rem; line-height: 1.5;
    box-shadow: 0 10px 24px rgba(0,0,0,.35);
  }
  .drone3d-status.warn { border-color: rgba(210,153,34,.45); color: #ffd48a; }
  .drone3d-status.error { border-color: rgba(233,69,96,.45); color: #ffb4ab; }
  .drone3d-status.ok { border-color: rgba(63,185,80,.45); color: #a7f3b7; }
  .draw-hint {
    background: rgba(233,69,96,.12); border: 1px dashed var(--accent);
    border-radius:8px; padding:10px 12px; font-size:.8rem;
    color:var(--text); margin-bottom:10px; text-align:center;
  }
  .draw-hint .hint-icon { font-size:1.4rem; display:block; margin-bottom:4px; }
  #fpCircleInfo { font-size:.76rem; color:var(--muted); margin-top:6px; }

  @media (max-width: 1100px) {
    :root { --sidebar-w: 320px; }
  }
  @media (max-width: 860px) {
    .drone3d-meta { grid-template-columns: 1fr 1fr; }
  }
</style>
<script src="https://accounts.google.com/gsi/client" async defer></script>
</head>
<body>

<div id="authGate">
  <div class="auth-card">
    <div class="auth-chip">Google Login Required</div>
    <h1>Sign in before opening the map</h1>
    <p>
      Use your Google account to access the frontend. Every successful login is
      recorded by the backend and appears in <code>/admin/logged-accounts</code>.
    </p>
    <div id="googleLoginButton"></div>
    <div class="auth-error" id="authError"></div>
    <div class="auth-note">Authorized local origin: <code>http://localhost:5174</code></div>
  </div>
</div>

<div id="sidebar">
  <div class="sb-header">
    <span class="flag">&#127479;&#127476;</span>
    <h1>ROMATSA Mirror</h1>
    <div class="auth-user" id="authUser" hidden>
      <div class="auth-user-meta">
        <div class="auth-user-name" id="authUserName"></div>
        <div class="auth-user-email" id="authUserEmail"></div>
      </div>
      <button class="auth-signout" id="signOutBtn" type="button">Sign out</button>
    </div>
  </div>

  <div class="sb-section">
    <h2>View Mode</h2>
    <div class="mode-toggle">
      <button class="mode-btn active" id="btnDrone" onclick="setMode('drone')">&#128681; Drone</button>
      <button class="mode-btn" id="btnGA" onclick="setMode('ga')">&#9992;&#65039; GA</button>
    </div>
  </div>

  <div class="sb-section">
    <h2>Layers</h2>
    <div id="layerToggles"></div>
  </div>

  <div class="sb-section">
    <h2>Altitude Filter</h2>
    <div class="alt-row">
      <input id="altSlider" type="range" min="0" max="500" step="5" value="120"/>
      <span id="altValue">120 m</span>
    </div>
    <div style="font-size:.68rem;color:var(--muted);margin-top:4px;">Zones outside this altitude are dimmed</div>
  </div>

  <div class="sb-section">
    <h2>Search</h2>
    <input id="searchBox" type="search" placeholder="Zone ID, ICAO, NOTAM..." autocomplete="off"/>
  </div>

  <div class="sb-section">
    <h2>Flight Plan</h2>
    <button class="fp-launch-btn" onclick="launchFlightPlan()">&#9992; New UAS Notification</button>
    <button class="fp-launch-btn fp-secondary-btn" onclick="loadMyFlightPlans(true)">Refresh My Plans</button>
    <div class="my-plans-list" id="myPlansList">
      <div class="muted">Sign in to load your saved plans.</div>
    </div>
  </div>

  <div class="sb-section">
    <h2>Live Drone</h2>
    <div class="sb-caption">Only drones on ongoing flight plans appear here. Scheduled flights stay in your flight plan list until takeoff.</div>
    <button class="fp-launch-btn fp-secondary-btn" onclick="loadMyDrones(true)">Refresh My Drone</button>
    <div class="my-plans-list" id="myDroneList">
      <div class="muted">Sign in to load your live drone telemetry.</div>
    </div>
  </div>

  <div id="stats">Loading layers...</div>
</div>

<div id="map"></div>

<div id="crossPanel">
  <button class="close-btn" onclick="closeCross()">&times;</button>
  <h3 id="crossTitle">Cross-check</h3>
  <div id="crossResults"></div>
</div>

<div id="drone3dOverlay">
  <div id="drone3dShell">
    <div class="drone3d-head">
      <div>
        <h2>3D Drone View</h2>
        <div class="sub" id="drone3dSubtitle">Lazy-loaded Cesium scene around the active drone. 2D map remains unchanged underneath.</div>
      </div>
      <div class="drone3d-head-actions">
        <button class="drone3d-close" type="button" onclick="refreshDrone3DAirspaces()">Refresh Airspace</button>
        <button class="drone3d-close" type="button" onclick="closeDrone3D()">Close 3D</button>
      </div>
    </div>
    <div class="drone3d-meta" id="drone3dMeta">
      <div class="drone3d-metric"><div class="label">Drone</div><div class="value">-</div></div>
      <div class="drone3d-metric"><div class="label">Terrain</div><div class="value">-</div></div>
      <div class="drone3d-metric"><div class="label">Airspace Volumes</div><div class="value">-</div></div>
      <div class="drone3d-metric"><div class="label">Nearby Aircraft</div><div class="value">-</div></div>
    </div>
    <div class="drone3d-stage">
      <div id="drone3dCanvas"></div>
      <div class="drone3d-status" id="drone3dStatus">Select an active drone to open a 3D view around it.</div>
    </div>
  </div>
</div>

<script>
// ========================================================================
// CONFIG
// ========================================================================
const LAYERS_CFG = {
  uas_zones:    { label: 'UAS Zones',    color: '#e94560', type: 'polygon', droneDefault: true,  gaDefault: false },
  notam:        { label: 'NOTAM UAS',    color: '#ff9800', type: 'polygon', droneDefault: true,  gaDefault: false },
  notam_all:    { label: 'All NOTAMs',   color: '#d29922', type: 'point',   droneDefault: false, gaDefault: true  },
  ctr:          { label: 'CTR Airspace',  color: '#58a6ff', type: 'polygon', droneDefault: true,  gaDefault: true  },
  tma:          { label: 'TMA Airspace',  color: '#3fb950', type: 'polygon', droneDefault: false, gaDefault: true  },
  airports:     { label: 'Airports',      color: '#39d2c0', type: 'point',   droneDefault: true,  gaDefault: true  },
  lower_routes: { label: 'ATS Routes',    color: '#bc8cff', type: 'line',    droneDefault: false, gaDefault: true  },
};

const GOOGLE_CLIENT_ID = '__GOOGLE_CLIENT_ID__';
const CESIUM_ION_TOKEN = '__CESIUM_ION_TOKEN__';
const TOWER_DATA = __TOWER_CONTACTS_JSON__;
window._towerData = TOWER_DATA;

let currentMode = 'drone';
let mapLayers = {};
let rawData   = {};
let allFeatureIndex = [];
let layersLoaded = false;
let authenticatedUser = null;
let authWorkspaceRefreshPromise = null;
let myDroneRefreshTimer = null;
let latestMyDrones = [];
let myDroneLayer = null;
let myDroneMarkers = {};
let drone3dViewer = null;
let drone3dLoadPromise = null;
let drone3dTerrainMode = '';
let drone3dImageryMode = '';
let drone3dBuildingsMode = '';
let drone3dBuildingsTileset = null;
let drone3dRefreshTimer = null;
let drone3dActiveDroneId = '';
let drone3dFetchInFlight = false;
let drone3dLastScene = null;
let drone3dRenderedZoneSignature = '';
let drone3dZonesVisible = false;
const CENTER_BLOCKING_LAYER_KEYS = ['ctr', 'uas_zones', 'notam', 'tma'];

function setMyPlansContent(html) {
  document.getElementById('myPlansList').innerHTML = html;
}

function setMyDronesContent(html) {
  document.getElementById('myDroneList').innerHTML = html;
}

function canCancelPlan(plan) {
  const runtimeState = (plan.runtime_state || '').toLowerCase();
  const workflowStatus = (plan.workflow_status || '').toLowerCase();
  return workflowStatus !== 'cancelled' && runtimeState !== 'completed';
}

function renderMyFlightPlans(plans) {
  if (!authenticatedUser) {
    setMyPlansContent('<div class="muted">Sign in to load your saved plans.</div>');
    return;
  }
  if (!plans || !plans.length) {
    setMyPlansContent('<div class="muted">No saved flight plans yet.</div>');
    return;
  }

  const html = plans.slice(0, 6).map(function(plan) {
    const runtimeState = (plan.runtime_state || 'upcoming').toLowerCase();
    const riskState = (plan.risk_level || 'LOW').toLowerCase();
    const cancelButton = canCancelPlan(plan)
      ? `<button class="mini-btn mini-btn-danger" type="button" onclick="cancelFlightPlan('${plan.public_id || ''}')">Cancel</button>`
      : '';
    return (
      '<div class="my-plan-card">' +
        '<div class="plan-top">' +
          '<span class="plan-id">' + (plan.public_id || '') + '</span>' +
          '<span class="status-pill status-' + runtimeState + '">' + runtimeState + '</span>' +
        '</div>' +
        '<div class="plan-meta">' +
          (plan.location_name || '') + '<br/>' +
          (plan.scheduled_start_local || '') + ' -> ' + (plan.scheduled_end_local || '') + '<br/>' +
          (plan.selected_twr || '') + ' / ' + Math.round(plan.max_altitude_m || 0) + ' m' +
        '</div>' +
        '<div class="plan-actions">' +
          '<span class="risk-pill risk-' + riskState + '">' + (plan.risk_level || 'LOW') + '</span>' +
          '<div style="display:flex;gap:8px;align-items:center">' +
            '<a class="plan-link" href="' + (plan.download_url || '#') + '" target="_blank">PDF</a>' +
            cancelButton +
          '</div>' +
        '</div>' +
      '</div>'
    );
  }).join('');
  setMyPlansContent(html);
}

function ensureMyDroneLayer() {
  if (!myDroneLayer) {
    myDroneLayer = L.layerGroup().addTo(map);
  }
}

function droneMarkerIcon(drone) {
  var heading = Math.round(Number(drone.heading || 0));
  return L.divIcon({
    className: 'my-drone-marker',
    html:
      '<div style="background:#58a6ff;color:#071018;border:1px solid rgba(255,255,255,.2);border-radius:999px;padding:5px 9px;font-size:11px;font-weight:700;box-shadow:0 5px 14px rgba(0,0,0,.35)">' +
      (drone.drone_id || 'DRONE') + ' | ' + heading + '&deg;' +
      '</div>',
    iconSize: [132, 28],
    iconAnchor: [66, 14],
  });
}

function focusDrone(droneId) {
  var drone = latestMyDrones.find(function(item) { return item.drone_id === droneId; });
  if (!drone) return;
  map.setView([Number(drone.latitude), Number(drone.longitude)], 15);
  var marker = myDroneMarkers[droneId];
  if (marker) {
    marker.openPopup();
  }
}

function drone3dStatus(message, kind) {
  var status = document.getElementById('drone3dStatus');
  status.className = 'drone3d-status' + (kind ? ' ' + kind : '');
  status.textContent = message;
}

function setDrone3DMeta(scene) {
  var buildingsProvider = scene.scene && scene.scene.buildings && scene.scene.buildings.provider ? scene.scene.buildings.provider : 'none';
  var imageryProvider = scene.scene && scene.scene.imagery && scene.scene.imagery.provider ? scene.scene.imagery.provider : '-';
  var zoneCount = (scene.zones || []).length;
  var metrics = [
    { label: 'Drone', value: (scene.drone && scene.drone.drone_id) || '-' },
    { label: 'Radius', value: String(Number(scene.scene && scene.scene.radius_km || 0).toFixed(0)) + ' km' },
    { label: 'Surface', value: imageryProvider + ' + ' + buildingsProvider },
    { label: 'Airspace Volumes', value: String(zoneCount) + ' visible' },
    { label: 'Nearby Aircraft', value: String((scene.nearby_aircraft || []).length) },
  ];
  document.getElementById('drone3dMeta').innerHTML = metrics.map(function(item) {
    return '<div class="drone3d-metric"><div class="label">' + item.label + '</div><div class="value">' + item.value + '</div></div>';
  }).join('');
}

function zoneSceneSignature(scene) {
  return (scene && scene.zones ? scene.zones : []).map(function(zone) {
    var geometry = zone.geometry || {};
    var coords = geometry.coordinates || [];
    return [
      String(zone.zone_id || ''),
      String(zone.lower_altitude_m || ''),
      String(zone.upper_altitude_m || ''),
      String(zone.category || ''),
      String(geometry.type || ''),
      String(JSON.stringify(coords).length),
    ].join(':');
  }).join('|');
}

function clearDrone3DAirspaces(viewer) {
  if (!viewer) return;
  removeDrone3DEntities(viewer, ['zone:']);
  drone3dRenderedZoneSignature = '';
  drone3dZonesVisible = false;
}

function syncDrone3DAirspaces(CesiumRef, viewerRef, sceneRef, forceRender) {
  var Cesium = CesiumRef || window.Cesium;
  var viewer = viewerRef || drone3dViewer;
  var scene = sceneRef || drone3dLastScene;
  if (!viewer) return;
  var signature = zoneSceneSignature(scene);
  if (!Cesium || !scene || !scene.zones || !scene.zones.length) {
    clearDrone3DAirspaces(viewer);
  } else if (forceRender || !drone3dZonesVisible || drone3dRenderedZoneSignature !== signature) {
    clearDrone3DAirspaces(viewer);
    renderZones3D(Cesium, viewer, scene.zones, scene);
    drone3dRenderedZoneSignature = signature;
    drone3dZonesVisible = true;
  }
  if (viewer.scene && typeof viewer.scene.requestRender === 'function') {
    viewer.scene.requestRender();
  }
  if (scene) {
    setDrone3DMeta(scene);
  }
}

function refreshDrone3DAirspaces() {
  if (!window.Cesium || !drone3dViewer || !drone3dLastScene) return;
  syncDrone3DAirspaces(window.Cesium, drone3dViewer, drone3dLastScene, true);
  drone3dStatus('Airspace volumes refreshed for the current 3D scene.', 'info');
}

function closeDrone3D() {
  if (drone3dRefreshTimer) {
    clearInterval(drone3dRefreshTimer);
    drone3dRefreshTimer = null;
  }
  drone3dActiveDroneId = '';
  if (drone3dViewer) {
    drone3dViewer.trackedEntity = undefined;
    clearDrone3DAirspaces(drone3dViewer);
  }
  document.getElementById('drone3dOverlay').style.display = 'none';
}

function loadScriptOnce(src) {
  return new Promise(function(resolve, reject) {
    var existing = Array.from(document.querySelectorAll('script')).find(function(node) { return node.src === src; });
    if (existing) {
      if (window.Cesium) {
        resolve();
        return;
      }
      existing.addEventListener('load', function() { resolve(); }, { once: true });
      existing.addEventListener('error', function(err) { reject(err); }, { once: true });
      return;
    }
    var script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = function() { resolve(); };
    script.onerror = function(err) { reject(err); };
    document.head.appendChild(script);
  });
}

function loadCssOnce(id, href) {
  if (document.getElementById(id)) return;
  var link = document.createElement('link');
  link.id = id;
  link.rel = 'stylesheet';
  link.href = href;
  document.head.appendChild(link);
}

async function ensureCesiumLoaded() {
  if (window.Cesium) return window.Cesium;
  if (drone3dLoadPromise) return drone3dLoadPromise;
  drone3dLoadPromise = (async function() {
    var base = 'https://cesium.com/downloads/cesiumjs/releases/1.126/Build/Cesium';
    loadCssOnce('cesiumWidgetCss', base + '/Widgets/widgets.css');
    await loadScriptOnce(base + '/Cesium.js');
    return window.Cesium;
  })();
  try {
    return await drone3dLoadPromise;
  } finally {
    drone3dLoadPromise = null;
  }
}

function polygonHierarchyFromRings(Cesium, rings, baseHeight) {
  if (!rings || !rings.length) return null;
  var outer = rings[0].map(function(coord) {
    return Cesium.Cartesian3.fromDegrees(Number(coord[0]), Number(coord[1]), baseHeight);
  });
  var holes = rings.slice(1).map(function(ring) {
    return new Cesium.PolygonHierarchy(ring.map(function(coord) {
      return Cesium.Cartesian3.fromDegrees(Number(coord[0]), Number(coord[1]), baseHeight);
    }));
  });
  return new Cesium.PolygonHierarchy(outer, holes);
}

function zoneColor(Cesium, hex, alpha) {
  try {
    return Cesium.Color.fromCssColorString(hex).withAlpha(alpha);
  } catch (err) {
    return Cesium.Color.CYAN.withAlpha(alpha);
  }
}

async function ensureDrone3DViewer(scene) {
  var Cesium = await ensureCesiumLoaded();
  var requestedTerrain = scene.scene && scene.scene.terrain && scene.scene.terrain.provider ? scene.scene.terrain.provider : 'ellipsoid';
  var requestedImagery = scene.scene && scene.scene.imagery && scene.scene.imagery.provider ? scene.scene.imagery.provider : 'openstreetmap';
  var accessToken = (scene.scene && scene.scene.terrain && scene.scene.terrain.ion_token) || CESIUM_ION_TOKEN || '';
  if (accessToken && Cesium.Ion) {
    Cesium.Ion.defaultAccessToken = accessToken;
  }
  if (!drone3dViewer || drone3dTerrainMode !== requestedTerrain || drone3dImageryMode !== requestedImagery) {
    if (drone3dViewer) {
      clearDrone3DAirspaces(drone3dViewer);
      drone3dViewer.destroy();
      drone3dViewer = null;
      drone3dBuildingsTileset = null;
    }
    var terrainProvider = new Cesium.EllipsoidTerrainProvider();
    if (requestedTerrain === 'ion') {
      try {
        if (typeof Cesium.createWorldTerrainAsync === 'function') {
          terrainProvider = await Cesium.createWorldTerrainAsync({
            requestWaterMask: true,
            requestVertexNormals: true,
          });
        } else if (typeof Cesium.createWorldTerrain === 'function') {
          terrainProvider = Cesium.createWorldTerrain();
        }
      } catch (err) {
        requestedTerrain = 'ellipsoid';
        terrainProvider = new Cesium.EllipsoidTerrainProvider();
      }
    }
    var imageryProvider = undefined;
    if (requestedImagery === 'openstreetmap' && Cesium.OpenStreetMapImageryProvider) {
      imageryProvider = new Cesium.OpenStreetMapImageryProvider({
        url: scene.scene && scene.scene.imagery && scene.scene.imagery.url ? scene.scene.imagery.url : 'https://tile.openstreetmap.org/',
      });
    }
    drone3dViewer = new Cesium.Viewer('drone3dCanvas', {
      terrainProvider: terrainProvider,
      imageryProvider: imageryProvider,
      animation: false,
      timeline: false,
      baseLayerPicker: false,
      geocoder: false,
      sceneModePicker: false,
      homeButton: false,
      navigationHelpButton: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: false,
      shouldAnimate: false,
    });
    drone3dViewer.scene.globe.depthTestAgainstTerrain = true;
    drone3dViewer.scene.globe.enableLighting = requestedTerrain === 'ion';
    drone3dViewer.scene.skyAtmosphere.show = true;
    drone3dViewer.shadows = requestedTerrain === 'ion';
    var controller = drone3dViewer.scene.screenSpaceCameraController;
    controller.enableRotate = true;
    controller.enableTranslate = true;
    controller.enableZoom = true;
    controller.enableTilt = true;
    controller.enableLook = true;
    controller.inertiaSpin = 0.12;
    controller.inertiaTranslate = 0.12;
    controller.inertiaZoom = 0.08;
    controller.minimumZoomDistance = 12;
    controller.maximumZoomDistance = 2500000;
    controller.rotateEventTypes = [Cesium.CameraEventType.LEFT_DRAG];
    controller.translateEventTypes = [Cesium.CameraEventType.MIDDLE_DRAG];
    controller.zoomEventTypes = [Cesium.CameraEventType.WHEEL, Cesium.CameraEventType.RIGHT_DRAG];
    controller.tiltEventTypes = [
      { eventType: Cesium.CameraEventType.MIDDLE_DRAG, modifier: Cesium.KeyboardEventModifier.CTRL },
      { eventType: Cesium.CameraEventType.LEFT_DRAG, modifier: Cesium.KeyboardEventModifier.CTRL },
    ];
    drone3dTerrainMode = requestedTerrain;
    drone3dImageryMode = requestedImagery;
  }
  return { Cesium: Cesium, viewer: drone3dViewer, terrainMode: drone3dTerrainMode };
}

function removeDrone3DEntities(viewer, prefixes) {
  viewer.entities.values.slice().forEach(function(entity) {
    var id = String(entity.id || '');
    if (prefixes.some(function(prefix) { return id.indexOf(prefix) === 0; })) {
      viewer.entities.remove(entity);
    }
  });
}

function metersPerLonDegree(lat) {
  return Math.max(Math.cos((Number(lat) || 0) * Math.PI / 180), 0.25) * 111320.0;
}

function offsetPoint(lat, lon, distanceM, bearingDeg) {
  var bearingRad = (Number(bearingDeg) || 0) * Math.PI / 180;
  return {
    latitude: Number(lat) + Math.cos(bearingRad) * Number(distanceM || 0) / 111320.0,
    longitude: Number(lon) + Math.sin(bearingRad) * Number(distanceM || 0) / metersPerLonDegree(lat),
  };
}

function droneBillboardImage() {
  return 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' +
      '<circle cx="32" cy="32" r="18" fill="#08111c" fill-opacity="0.78" stroke="#d7ecff" stroke-width="3"/>' +
      '<circle cx="32" cy="32" r="8.5" fill="#58a6ff" stroke="#ffffff" stroke-width="2"/>' +
      '<path d="M32 12 V18 M32 46 V52 M12 32 H18 M46 32 H52" stroke="#d7ecff" stroke-width="2.5" stroke-linecap="round"/>' +
    '</svg>'
  );
}

function droneHeadingTipImage() {
  return 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">' +
      '<path d="M20 3 L34 30 H24 L24 37 H16 L16 30 H6 Z" fill="#9ce7ff" stroke="#ffffff" stroke-width="2" stroke-linejoin="round"/>' +
    '</svg>'
  );
}

async function resolveDronePose(Cesium, viewer, drone, scene) {
  var lat = Number(drone.latitude || 0);
  var lon = Number(drone.longitude || 0);
  var aglHeight = Math.max(Number(drone.altitude || 0), 12);
  var pose = {
    position: Cesium.Cartesian3.fromDegrees(lon, lat, aglHeight),
    groundHeight: null,
    absoluteHeight: aglHeight,
    heightReference: Cesium.HeightReference ? Cesium.HeightReference.NONE : undefined,
    sampledTerrain: false,
  };
  var terrainMode = scene && scene.scene && scene.scene.terrain && scene.scene.terrain.provider;
  if (terrainMode === 'ion' && viewer && viewer.terrainProvider && typeof Cesium.sampleTerrainMostDetailed === 'function') {
    try {
      var sample = await Cesium.sampleTerrainMostDetailed(
        viewer.terrainProvider,
        [Cesium.Cartographic.fromDegrees(lon, lat)]
      );
      var terrainHeight = Number(sample && sample[0] && sample[0].height);
      if (Number.isFinite(terrainHeight)) {
        pose.groundHeight = terrainHeight;
        pose.absoluteHeight = terrainHeight + aglHeight;
        pose.position = Cesium.Cartesian3.fromDegrees(lon, lat, pose.absoluteHeight);
        pose.sampledTerrain = true;
      }
    } catch (err) {}
  }
  if (!pose.sampledTerrain && terrainMode === 'ion' && viewer && viewer.scene && viewer.scene.globe && typeof viewer.scene.globe.getHeight === 'function') {
    try {
      var fallbackHeight = Number(viewer.scene.globe.getHeight(Cesium.Cartographic.fromDegrees(lon, lat)));
      if (Number.isFinite(fallbackHeight)) {
        pose.groundHeight = fallbackHeight;
        pose.absoluteHeight = fallbackHeight + aglHeight;
        pose.position = Cesium.Cartesian3.fromDegrees(lon, lat, pose.absoluteHeight);
        pose.sampledTerrain = true;
      }
    } catch (err) {}
  }
  if (!pose.sampledTerrain && terrainMode === 'ion' && Cesium.HeightReference) {
    pose.heightReference = Cesium.HeightReference.RELATIVE_TO_GROUND;
  }
  return pose;
}

function renderDroneHeadingIndicator(Cesium, viewer, drone, pose) {
  var entity = viewer.entities.getById('focus-drone-heading');
  var tipEntity = viewer.entities.getById('focus-drone-heading-tip');
  if (!pose || !pose.position) {
    if (entity) viewer.entities.remove(entity);
    if (tipEntity) viewer.entities.remove(tipEntity);
    return;
  }
  var arrowLength = Math.max(Number(drone.speed || 0) * 10.0, 120.0);
  var target = offsetPoint(
    Number(drone.latitude || 0),
    Number(drone.longitude || 0),
    arrowLength,
    Number(drone.heading || 0)
  );
  var arrowAltitude = pose.sampledTerrain
    ? Math.max(pose.absoluteHeight, pose.groundHeight + 45)
    : Math.max(Number(drone.altitude || 0), 45);
  var targetPosition = Cesium.Cartesian3.fromDegrees(
    Number(target.longitude || 0),
    Number(target.latitude || 0),
    arrowAltitude
  );
  var arrow = {
    positions: [
      pose.position,
      targetPosition,
    ],
    width: 8,
    material: new Cesium.PolylineArrowMaterialProperty(
      Cesium.Color.fromCssColorString('#8cd6ff').withAlpha(0.96)
    ),
    depthFailMaterial: Cesium.Color.fromCssColorString('#dff8ff').withAlpha(0.96),
  };
  if (entity) {
    entity.polyline = arrow;
  } else {
    viewer.entities.add({
      id: 'focus-drone-heading',
      polyline: arrow,
    });
  }
  var tipState = {
    position: targetPosition,
    billboard: {
      image: droneHeadingTipImage(),
      width: 24,
      height: 24,
      rotation: Cesium.Math.toRadians(Number(drone.heading || 0)),
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
      scaleByDistance: new Cesium.NearFarScalar(250, 1.0, 24000, 0.78),
      eyeOffset: new Cesium.Cartesian3(0, 0, 120),
    },
  };
  if (tipEntity) {
    tipEntity.position = tipState.position;
    tipEntity.billboard = tipState.billboard;
  } else {
    viewer.entities.add(Object.assign({ id: 'focus-drone-heading-tip' }, tipState));
  }
  if (viewer.scene && typeof viewer.scene.requestRender === 'function') {
    viewer.scene.requestRender();
  }
}

function renderDroneAltitudeAnchor(Cesium, viewer, drone, pose) {
  var entity = viewer.entities.getById('focus-drone-anchor');
  if (!pose || !pose.sampledTerrain || pose.absoluteHeight - pose.groundHeight < 3) {
    if (entity) viewer.entities.remove(entity);
    return;
  }
  var anchor = {
    positions: [
      Cesium.Cartesian3.fromDegrees(Number(drone.longitude || 0), Number(drone.latitude || 0), pose.groundHeight + 1.5),
      pose.position,
    ],
    width: 2,
    material: Cesium.Color.fromCssColorString('#8cd6ff').withAlpha(0.52),
  };
  if (entity) {
    entity.polyline = anchor;
    return;
  }
  viewer.entities.add({
    id: 'focus-drone-anchor',
    polyline: anchor,
  });
  if (viewer.scene && typeof viewer.scene.requestRender === 'function') {
    viewer.scene.requestRender();
  }
}

function renderOperationalRegion(Cesium, viewer, scene, drone) {
  if (!scene || !scene.scene || !scene.scene.focus_region || !drone) return;
  var region = scene.scene.focus_region;
  var centerHeight = Math.max(Number(drone.altitude || 0) * 0.12, 2);
  var ellipse = {
    semiMajorAxis: Number(region.radius_m || 5000),
    semiMinorAxis: Number(region.radius_m || 5000),
    height: 0,
    material: Cesium.Color.fromCssColorString('#58a6ff').withAlpha(0.08),
    outline: true,
    outlineColor: Cesium.Color.fromCssColorString('#58a6ff').withAlpha(0.82),
    classificationType: Cesium.ClassificationType.BOTH,
  };
  var entity = viewer.entities.getById('focus-region');
  if (entity) {
    entity.position = Cesium.Cartesian3.fromDegrees(Number(drone.longitude), Number(drone.latitude), centerHeight);
    entity.ellipse = ellipse;
    return;
  }
  viewer.entities.add({
    id: 'focus-region',
    position: Cesium.Cartesian3.fromDegrees(Number(drone.longitude), Number(drone.latitude), centerHeight),
    ellipse: ellipse,
  });
}

function renderDroneTrack(Cesium, viewer, track) {
  if (!track || !track.length) return;
  var positions = track.map(function(point) {
    return Cesium.Cartesian3.fromDegrees(Number(point.longitude), Number(point.latitude), Number(point.altitude || 0));
  });
  var polyline = {
    positions: positions,
    width: 4,
    material: new Cesium.PolylineGlowMaterialProperty({
      color: Cesium.Color.CYAN,
      glowPower: 0.18,
    }),
  };
  var entity = viewer.entities.getById('focus-drone-track');
  if (entity) {
    entity.polyline = polyline;
    return;
  }
  viewer.entities.add({
    id: 'focus-drone-track',
    polyline: polyline,
  });
}

async function renderDroneEntity(Cesium, viewer, drone, scene) {
  var pose = await resolveDronePose(Cesium, viewer, drone, scene);
  var position = pose.position;
  var labelText = String(drone.label || drone.drone_id || 'DRONE');
  var hpr = new Cesium.HeadingPitchRoll(
    Cesium.Math.toRadians(Number(drone.heading || 0)),
    Cesium.Math.toRadians(Number(drone.pitch || 0)),
    Cesium.Math.toRadians(Number(drone.roll || 0))
  );
  var viewDistance = Math.max(Number(scene && scene.scene && scene.scene.focus_region && scene.scene.focus_region.radius_m || 5000) * 0.55, 1400);
  var entityState = {
    position: position,
    orientation: Cesium.Transforms.headingPitchRollQuaternion(position, hpr),
    viewFrom: new Cesium.Cartesian3(-viewDistance, 0, viewDistance * 0.42),
    billboard: {
      image: droneBillboardImage(),
      width: 44,
      height: 44,
      heightReference: pose.heightReference,
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
      scaleByDistance: new Cesium.NearFarScalar(250, 1.2, 20000, 0.92),
      eyeOffset: new Cesium.Cartesian3(0, 0, -80),
    },
    point: {
      pixelSize: 9,
      color: Cesium.Color.fromCssColorString('#58a6ff').withAlpha(0.95),
      outlineColor: Cesium.Color.WHITE.withAlpha(0.92),
      outlineWidth: 2,
      heightReference: pose.heightReference,
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
      scaleByDistance: new Cesium.NearFarScalar(250, 1.1, 20000, 0.85),
    },
    label: {
      text: labelText,
      font: '700 15px "Segoe UI", sans-serif',
      style: Cesium.LabelStyle.FILL_AND_OUTLINE,
      fillColor: Cesium.Color.WHITE,
      outlineColor: Cesium.Color.BLACK.withAlpha(0.95),
      outlineWidth: 3,
      showBackground: true,
      backgroundColor: Cesium.Color.fromCssColorString('#08111c').withAlpha(0.78),
      backgroundPadding: new Cesium.Cartesian2(10, 6),
      pixelOffset: new Cesium.Cartesian2(0, -42),
      pixelOffsetScaleByDistance: new Cesium.NearFarScalar(250, 1.0, 20000, 0.7),
      scaleByDistance: new Cesium.NearFarScalar(250, 1.08, 20000, 0.92),
      horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
      verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
      heightReference: pose.heightReference,
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
      eyeOffset: new Cesium.Cartesian3(0, 0, -120),
    },
  };
  renderDroneHeadingIndicator(Cesium, viewer, drone, pose);
  renderDroneAltitudeAnchor(Cesium, viewer, drone, pose);
  var entity = viewer.entities.getById('focus-drone');
  if (entity) {
    entity.position = entityState.position;
    entity.orientation = entityState.orientation;
    entity.viewFrom = entityState.viewFrom;
    entity.box = undefined;
    entity.billboard = entityState.billboard;
    entity.point = entityState.point;
    entity.label = entityState.label;
    return entity;
  }
  return viewer.entities.add(Object.assign({ id: 'focus-drone' }, entityState));
}

function renderNearbyAircraft(Cesium, viewer, aircraft) {
  removeDrone3DEntities(viewer, ['nearby-aircraft:']);
  (aircraft || []).forEach(function(drone) {
    viewer.entities.add({
      id: 'nearby-aircraft:' + String(drone.drone_id || 'unknown'),
      position: Cesium.Cartesian3.fromDegrees(Number(drone.longitude), Number(drone.latitude), Number(drone.altitude || 0)),
      point: {
        pixelSize: 10,
        color: Cesium.Color.ORANGE,
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 1,
      },
      label: {
        text: String(drone.drone_id || 'ACFT'),
        font: '12px sans-serif',
        fillColor: Cesium.Color.ORANGE,
        showBackground: true,
        backgroundColor: Cesium.Color.BLACK.withAlpha(0.55),
        pixelOffset: new Cesium.Cartesian2(0, -20),
      },
    });
  });
}

function renderObstacles(Cesium, viewer, obstacles) {
  removeDrone3DEntities(viewer, ['obstacle:']);
  (obstacles || []).forEach(function(obstacle) {
    var position = Cesium.Cartesian3.fromDegrees(
      Number(obstacle.longitude),
      Number(obstacle.latitude),
      Number(obstacle.base_altitude_m || 0) + Number(obstacle.height_m || 0) / 2.0
    );
    if (String(obstacle.kind || '') === 'building') {
      viewer.entities.add({
        id: 'obstacle:' + String(obstacle.obstacle_id || obstacle.name || 'building'),
        position: position,
        box: {
          dimensions: new Cesium.Cartesian3(
            Number(obstacle.footprint_radius_m || 18) * 1.8,
            Number(obstacle.footprint_radius_m || 18) * 1.8,
            Number(obstacle.height_m || 20)
          ),
          material: Cesium.Color.SANDYBROWN.withAlpha(0.5),
        },
      });
      return;
    }
    viewer.entities.add({
      id: 'obstacle:' + String(obstacle.obstacle_id || obstacle.name || 'obstacle'),
      position: position,
      cylinder: {
        length: Number(obstacle.height_m || 20),
        topRadius: Math.max(Number(obstacle.footprint_radius_m || 8) * 0.25, 3),
        bottomRadius: Number(obstacle.footprint_radius_m || 8),
        material: Cesium.Color.GOLDENROD.withAlpha(0.5),
      },
    });
  });
}

function airspaceGroundVisualOffset(Cesium, viewer, scene) {
  var altitudeMode = scene && scene.scene && scene.scene.rendering && scene.scene.rendering.airspace_altitude_mode
    ? scene.scene.rendering.airspace_altitude_mode
    : 'absolute';
  if (altitudeMode !== 'relative_to_ground_visual') return 0;
  if (!Cesium || !viewer || !viewer.scene || !viewer.scene.globe || typeof viewer.scene.globe.getHeight !== 'function') return 0;
  var latitude = Number(scene && scene.drone && scene.drone.latitude);
  var longitude = Number(scene && scene.drone && scene.drone.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return 0;
  try {
    var groundHeight = Number(viewer.scene.globe.getHeight(Cesium.Cartographic.fromDegrees(longitude, latitude)));
    if (Number.isFinite(groundHeight)) {
      return Math.max(groundHeight, 0) + 60.0;
    }
  } catch (err) {}
  return 60.0;
}

function zoneAltitudeRange(zone, scene, Cesium, viewer) {
  var relativeLower = Math.max(Number(zone.lower_altitude_m || 0), 0);
  var fallbackUpper = Math.max(relativeLower + 120, 120);
  var relativeUpper = Number(zone.upper_altitude_m || fallbackUpper);
  var maxUpper = Number(scene && scene.scene && scene.scene.rendering && scene.scene.rendering.airspace_volume_max_altitude_m || 6000);
  var baseOffset = airspaceGroundVisualOffset(Cesium, viewer, scene);
  if (!Number.isFinite(relativeUpper)) relativeUpper = fallbackUpper;
  if (relativeUpper <= relativeLower) relativeUpper = relativeLower + 60;
  relativeUpper = Math.min(relativeUpper, maxUpper);
  if (relativeUpper <= relativeLower) relativeLower = Math.max(0, relativeUpper - 60);
  var lower = relativeLower + baseOffset;
  var upper = relativeUpper + baseOffset;
  if (upper <= lower + 40) upper = lower + 40;
  return { lower: lower, upper: upper, baseOffset: baseOffset };
}

function zoneRenderStyle(Cesium, zone) {
  var category = String(zone.category || '').toLowerCase();
  var color = zone.color || '#58a6ff';
  var fillAlpha = 0.22;
  var outlineAlpha = 0.96;
  if (category === 'restricted') {
    fillAlpha = 0.28;
    outlineAlpha = 0.98;
  } else if (category === 'temporary_restriction') {
    fillAlpha = 0.24;
    outlineAlpha = 0.96;
  } else if (category === 'tma') {
    fillAlpha = 0.18;
    outlineAlpha = 0.92;
  }
  return {
    material: zoneColor(Cesium, color, fillAlpha),
    outlineColor: zoneColor(Cesium, color, outlineAlpha),
  };
}

function ringPositionsAtHeight(Cesium, ring, height) {
  return (ring || []).map(function(coord) {
    return Cesium.Cartesian3.fromDegrees(Number(coord[0]), Number(coord[1]), height);
  });
}

function addZonePolylineOutline(Cesium, viewer, id, positions, color, width) {
  if (!positions || positions.length < 2) return;
  viewer.entities.add({
    id: id,
    polyline: {
      positions: positions,
      width: width,
      material: color,
      depthFailMaterial: color,
      clampToGround: false,
    },
  });
}

function addZoneVerticalEdges(Cesium, viewer, idPrefix, ring, range, color) {
  var usablePoints = Math.max((ring || []).length - 1, 0);
  if (!usablePoints) return;
  var step = Math.max(1, Math.floor(usablePoints / 6));
  for (var index = 0; index < usablePoints; index += step) {
    var coord = ring[index];
    if (!coord) continue;
    addZonePolylineOutline(
      Cesium,
      viewer,
      idPrefix + ':edge:' + String(index),
      [
        Cesium.Cartesian3.fromDegrees(Number(coord[0]), Number(coord[1]), range.lower),
        Cesium.Cartesian3.fromDegrees(Number(coord[0]), Number(coord[1]), range.upper),
      ],
      color,
      2.5
    );
  }
}

function addZoneShellOverlays(Cesium, viewer, idPrefix, rings, range, style) {
  (rings || []).forEach(function(ring, ringIndex) {
    if (!ring || ring.length < 2) return;
    var ringPrefix = idPrefix + ':ring:' + String(ringIndex);
    addZonePolylineOutline(
      Cesium,
      viewer,
      ringPrefix + ':top',
      ringPositionsAtHeight(Cesium, ring, range.upper),
      style.outlineColor,
      4
    );
    addZonePolylineOutline(
      Cesium,
      viewer,
      ringPrefix + ':bottom',
      ringPositionsAtHeight(Cesium, ring, range.lower),
      style.outlineColor,
      2.5
    );
    addZoneVerticalEdges(Cesium, viewer, ringPrefix, ring, range, style.outlineColor);
  });
}

function buildZonePolygon(Cesium, hierarchy, range, style) {
  return {
    hierarchy: hierarchy,
    height: range.lower,
    extrudedHeight: range.upper,
    material: style.material,
    outline: true,
    outlineColor: style.outlineColor,
    closeTop: true,
    closeBottom: false,
    perPositionHeight: false,
    arcType: Cesium.ArcType.GEODESIC,
  };
}

function renderZones3D(Cesium, viewer, zones, scene) {
  (zones || []).slice().sort(function(left, right) {
    var leftRange = zoneAltitudeRange(left, scene, Cesium, viewer);
    var rightRange = zoneAltitudeRange(right, scene, Cesium, viewer);
    if (leftRange.lower !== rightRange.lower) return leftRange.lower - rightRange.lower;
    if (leftRange.upper !== rightRange.upper) return leftRange.upper - rightRange.upper;
    return String(left.name || left.zone_id || '').localeCompare(String(right.name || right.zone_id || ''));
  }).forEach(function(zone, zoneIndex) {
    var geometry = zone.geometry || {};
    var range = zoneAltitudeRange(zone, scene, Cesium, viewer);
    var style = zoneRenderStyle(Cesium, zone);
    if (geometry.type === 'Polygon') {
      var hierarchy = polygonHierarchyFromRings(Cesium, geometry.coordinates || [], range.lower);
      if (!hierarchy) return;
      var zoneId = 'zone:' + String(zone.zone_id || zoneIndex);
      viewer.entities.add({
        id: zoneId,
        polygon: buildZonePolygon(Cesium, hierarchy, range, style),
      });
      addZoneShellOverlays(Cesium, viewer, zoneId, geometry.coordinates || [], range, style);
      return;
    }
    if (geometry.type === 'MultiPolygon') {
      (geometry.coordinates || []).forEach(function(polygonRings, polygonIndex) {
        var hierarchy = polygonHierarchyFromRings(Cesium, polygonRings || [], range.lower);
        if (!hierarchy) return;
        var polygonId = 'zone:' + String(zone.zone_id || zoneIndex) + ':' + String(polygonIndex);
        viewer.entities.add({
          id: polygonId,
          polygon: buildZonePolygon(Cesium, hierarchy, range, style),
        });
        addZoneShellOverlays(Cesium, viewer, polygonId, polygonRings || [], range, style);
      });
    }
  });
}

async function ensureDrone3DBuildings(Cesium, viewer, scene) {
  var requestedBuildings = scene.scene && scene.scene.buildings && scene.scene.buildings.provider ? scene.scene.buildings.provider : 'none';
  if (requestedBuildings === drone3dBuildingsMode && drone3dBuildingsTileset) {
    drone3dBuildingsTileset.show = requestedBuildings !== 'none';
    return requestedBuildings !== 'none';
  }
  if (drone3dBuildingsTileset) {
    viewer.scene.primitives.remove(drone3dBuildingsTileset);
    drone3dBuildingsTileset = null;
  }
  drone3dBuildingsMode = requestedBuildings;
  if (requestedBuildings !== 'cesium_osm_buildings' && requestedBuildings !== 'google_photorealistic_3d_tiles') {
    return false;
  }
  try {
    if (requestedBuildings === 'google_photorealistic_3d_tiles' && typeof Cesium.createGooglePhotorealistic3DTileset === 'function') {
      drone3dBuildingsTileset = await Cesium.createGooglePhotorealistic3DTileset();
    } else if (typeof Cesium.createOsmBuildingsAsync === 'function') {
      drone3dBuildingsTileset = await Cesium.createOsmBuildingsAsync({
        defaultColor: Cesium.Color.fromCssColorString('#d7c7a3').withAlpha(0.88),
        enableShowOutline: false,
      });
    } else if (typeof Cesium.createOsmBuildings === 'function') {
      drone3dBuildingsTileset = Cesium.createOsmBuildings();
    }
    if (drone3dBuildingsTileset) {
      viewer.scene.primitives.add(drone3dBuildingsTileset);
      return true;
    }
  } catch (err) {
    drone3dBuildingsTileset = null;
  }
  return false;
}

async function renderDrone3DScene(scene, options) {
  var prepared = await ensureDrone3DViewer(scene);
  var Cesium = prepared.Cesium;
  var viewer = prepared.viewer;
  drone3dLastScene = scene;
  var droneEntity = await renderDroneEntity(Cesium, viewer, scene.drone || {}, scene);
  renderOperationalRegion(Cesium, viewer, scene, scene.drone || {});
  renderObstacles(Cesium, viewer, scene.obstacles || []);
  renderNearbyAircraft(Cesium, viewer, scene.nearby_aircraft || []);
  renderDroneTrack(Cesium, viewer, (scene.drone && scene.drone.track) || []);
  var buildingsLoaded = await ensureDrone3DBuildings(Cesium, viewer, scene);
  droneEntity = await renderDroneEntity(Cesium, viewer, scene.drone || {}, scene);
  renderDroneTrack(Cesium, viewer, (scene.drone && scene.drone.track) || []);
  syncDrone3DAirspaces(Cesium, viewer, scene, true);
  viewer.trackedEntity = undefined;
  if (!options || options.flyTo !== false) {
    var camera = scene.scene && scene.scene.camera ? scene.scene.camera : {};
    viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(
        Number(camera.longitude || scene.drone.longitude || 0),
        Number(camera.latitude || scene.drone.latitude || 0),
        Number(camera.altitude_m || 2200)
      ),
      orientation: {
        heading: Cesium.Math.toRadians(Number(camera.heading_deg || 0)),
        pitch: Cesium.Math.toRadians(Number(camera.pitch_deg || -35)),
        roll: 0,
      },
      duration: 1.4,
    });
  }
  return {
    Cesium: Cesium,
    viewer: viewer,
    terrainMode: prepared.terrainMode,
    buildingsLoaded: buildingsLoaded,
  };
}

async function loadDrone3DScene(droneId, options) {
  if (drone3dFetchInFlight) return;
  drone3dFetchInFlight = true;
  try {
    var res = await fetch('/api/drones/' + encodeURIComponent(droneId) + '/scene-3d');
    var scene = await res.json().catch(function() { return {}; });
    if (!res.ok) {
      throw new Error(scene.error || 'Failed to build the 3D scene.');
    }
    drone3dLastScene = scene;
    setDrone3DMeta(scene);
    var radiusKm = Number(scene.scene && scene.scene.radius_km || 5).toFixed(0);
    var buildingProvider = scene.scene && scene.scene.buildings && scene.scene.buildings.provider ? scene.scene.buildings.provider : 'none';
    document.getElementById('drone3dSubtitle').textContent =
      radiusKm + ' km terrain-following map around ' + (scene.drone && scene.drone.drone_id ? scene.drone.drone_id : droneId) + '. Terrain, buildings, and nearby airspace volumes refresh while the drone is live.';
    var rendered = await renderDrone3DScene(scene, options || {});
    if (rendered.terrainMode === 'ion' && buildingProvider === 'google_photorealistic_3d_tiles' && rendered.buildingsLoaded) {
      drone3dStatus('Live 3D map loaded with Cesium terrain, Google Photorealistic 3D Tiles, and nearby airspace volumes.', 'ok');
    } else if (rendered.terrainMode === 'ion' && rendered.buildingsLoaded) {
      drone3dStatus('Live 3D map loaded with terrain, OSM buildings, and nearby airspace volumes around the drone.', 'ok');
    } else if (rendered.terrainMode === 'ion') {
      drone3dStatus('Live 3D map loaded with terrain and nearby airspace volumes. Photorealistic or building tiles are unavailable for this session.', 'warn');
    } else {
      drone3dStatus('3D map loaded with imagery and nearby airspace volumes. Set DRONE_CESIUM_ION_TOKEN to unlock Cesium terrain and photorealistic 3D tiles.', 'warn');
    }
  } finally {
    drone3dFetchInFlight = false;
  }
}

function startDrone3DRefresh(droneId, intervalSeconds) {
  if (drone3dRefreshTimer) {
    clearInterval(drone3dRefreshTimer);
  }
  drone3dRefreshTimer = setInterval(function() {
    if (!drone3dActiveDroneId) return;
    loadDrone3DScene(droneId, { flyTo: false }).catch(function(err) {
      drone3dStatus(err && err.message ? err.message : '3D scene refresh failed.', 'warn');
    });
  }, Math.max(Number(intervalSeconds || 5), 3) * 1000);
}

async function openDrone3D(droneId) {
  if (!authenticatedUser || !droneId) return;
  drone3dActiveDroneId = droneId;
  document.getElementById('drone3dOverlay').style.display = 'block';
  drone3dStatus('Loading terrain, buildings, and live airspace context around the selected drone...', 'info');
  try {
    await loadDrone3DScene(droneId, { flyTo: true });
    startDrone3DRefresh(droneId, 5);
  } catch (err) {
    drone3dStatus(err && err.message ? err.message : '3D scene failed to load.', 'error');
  }
}

function renderMyDrones(drones) {
  latestMyDrones = drones || [];
  ensureMyDroneLayer();
  myDroneLayer.clearLayers();
  myDroneMarkers = {};

  if (!authenticatedUser) {
    setMyDronesContent('<div class="muted">Sign in to load your live drone telemetry.</div>');
    return;
  }
  if (!drones || !drones.length) {
    setMyDronesContent('<div class="muted">No drones are currently flying for your account.</div><div class="drone-note">Mock drones for upcoming flight plans are not shown here. This panel is reserved for active flights already in progress.</div>');
    return;
  }

  drones.forEach(function(drone) {
    var lat = Number(drone.latitude);
    var lon = Number(drone.longitude);
    var marker = L.marker([lat, lon], { icon: droneMarkerIcon(drone) });
    marker.bindPopup(
      '<strong>' + (drone.drone_id || '') + '</strong><br/>' +
      (drone.location_name || '') + '<br/>' +
      'Alt ' + Number(drone.altitude || 0).toFixed(1) + ' m | BAT ' + Number(drone.battery_level || 0).toFixed(0) + '%'
    );
    marker.addTo(myDroneLayer);
    myDroneMarkers[drone.drone_id] = marker;
  });

  var html = drones.map(function(drone) {
    var statusClass = String(drone.status || 'offline').toLowerCase();
    return (
      '<div class="drone-card">' +
        '<div class="drone-top">' +
          '<div class="drone-id">' + (drone.drone_id || '') + '</div>' +
          '<div class="drone-status ' + statusClass + '">' + (drone.status || 'unknown') + '</div>' +
        '</div>' +
        '<div class="drone-grid">' +
          '<div><span>Flight plan</span>' + (drone.flight_plan_public_id || '') + '</div>' +
          '<div><span>Updated</span>' + (drone.timestamp || '') + '</div>' +
          '<div><span>Altitude</span>' + Number(drone.altitude || 0).toFixed(1) + ' m</div>' +
          '<div><span>Battery</span>' + Number(drone.battery_level || 0).toFixed(0) + '%</div>' +
          '<div><span>Speed</span>' + Number(drone.speed || 0).toFixed(1) + ' m/s</div>' +
          '<div><span>Heading</span>' + Number(drone.heading || 0).toFixed(0) + '&deg;</div>' +
          '<div><span>Pitch / Roll</span>' + Number(drone.pitch || 0).toFixed(1) + '&deg; / ' + Number(drone.roll || 0).toFixed(1) + '&deg;</div>' +
          '<div><span>Position</span>' + Number(drone.latitude || 0).toFixed(5) + ', ' + Number(drone.longitude || 0).toFixed(5) + '</div>' +
        '</div>' +
        '<div class="drone-actions">' +
          '<button type="button" onclick="focusDrone(\\'' + (drone.drone_id || '') + '\\')">Show on map</button>' +
          '<button type="button" onclick="openDrone3D(\\'' + (drone.drone_id || '') + '\\')">3D view</button>' +
        '</div>' +
      '</div>'
    );
  }).join('');
  setMyDronesContent(html);
}

async function loadMyFlightPlans(showErrors) {
  if (!authenticatedUser) {
    renderMyFlightPlans([]);
    return;
  }
  try {
    const res = await fetch('/api/flight-plans?scope=mine&include_past=1');
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to load your flight plans.');
    }
    const data = await res.json();
    renderMyFlightPlans(data.flight_plans || []);
  } catch (err) {
    if (showErrors) {
      alert(err && err.message ? err.message : 'Failed to load your flight plans.');
    } else {
      console.error(err);
    }
  }
}

async function loadMyDrones(showErrors) {
  if (!authenticatedUser) {
    renderMyDrones([]);
    return;
  }
  try {
    const res = await fetch('/api/drones/live');
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to load live drone telemetry.');
    }
    const data = await res.json();
    renderMyDrones(data.drones || []);
  } catch (err) {
    if (showErrors) {
      alert(err && err.message ? err.message : 'Failed to load live drone telemetry.');
    } else {
      console.error(err);
    }
  }
}

function startMyDroneRefresh() {
  clearInterval(myDroneRefreshTimer);
  loadMyDrones(false);
  myDroneRefreshTimer = setInterval(function() {
    loadMyDrones(false);
  }, 4000);
}

function stopMyDroneRefresh() {
  clearInterval(myDroneRefreshTimer);
  myDroneRefreshTimer = null;
  latestMyDrones = [];
  if (myDroneLayer) {
    myDroneLayer.clearLayers();
  }
}

async function cancelFlightPlan(publicId) {
  if (!publicId) return;
  if (!confirm('Cancel flight plan ' + publicId + '?')) {
    return;
  }
  try {
    const res = await fetch('/api/flight-plans/' + encodeURIComponent(publicId) + '/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || 'Failed to cancel the flight plan.');
    }
    loadMyFlightPlans(false);
    if (fpSavedPlan && fpSavedPlan.public_id === publicId) {
      fpSavedPlan = data.flight_plan || fpSavedPlan;
      showSavedFlightPlan();
    }
  } catch (err) {
    alert(err && err.message ? err.message : 'Failed to cancel the flight plan.');
  }
}

async function restoreServerSession() {
  try {
    const res = await fetch('/api/auth/me');
    if (!res.ok) return false;
    const data = await res.json();
    if (data && data.user && data.user.email) {
      unlockAuthenticatedUi(data.user);
      return true;
    }
  } catch (err) {
    console.error('Failed to restore backend session', err);
  }
  return false;
}

async function bootstrapDemoFlightPlan() {
  if (!authenticatedUser) {
    return { created: false, reason: 'signed-out' };
  }
  try {
    const res = await fetch('/api/demo/bootstrap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || 'Failed to bootstrap demo flight plan.');
    }
    if (data && data.errors && data.errors.length) {
      console.warn('Demo flight bootstrap issues', data.errors);
    }
    return data || { created: false, reason: 'unknown' };
  } catch (err) {
    console.error('Demo flight bootstrap failed', err);
    return { created: false, reason: 'request-failed' };
  }
}

async function refreshAuthenticatedWorkspace(user) {
  const currentEmail = user && user.email ? user.email : '';
  const run = (async function() {
    await bootstrapDemoFlightPlan();
    if (!authenticatedUser || authenticatedUser.email !== currentEmail) {
      return;
    }
    await loadMyFlightPlans(false);
    if (!authenticatedUser || authenticatedUser.email !== currentEmail) {
      return;
    }
    startMyDroneRefresh();
    prefillFlightPlanForm();
  })();
  authWorkspaceRefreshPromise = run;
  try {
    await run;
  } finally {
    if (authWorkspaceRefreshPromise === run) {
      authWorkspaceRefreshPromise = null;
    }
  }
}

function setAuthError(message) {
  document.getElementById('authError').textContent = message || '';
}

function updateAuthenticatedUser() {
  const authUser = document.getElementById('authUser');
  if (!authenticatedUser) {
    authUser.hidden = true;
    document.getElementById('authUserName').textContent = '';
    document.getElementById('authUserEmail').textContent = '';
    return;
  }

  authUser.hidden = false;
  document.getElementById('authUserName').textContent =
    authenticatedUser.display_name || authenticatedUser.email || 'Google user';
  document.getElementById('authUserEmail').textContent = authenticatedUser.email || '';
}

function unlockAuthenticatedUi(user) {
  authenticatedUser = user;
  updateAuthenticatedUser();
  setAuthError('');
  document.getElementById('authGate').style.display = 'none';
  if (!layersLoaded) {
    layersLoaded = true;
    loadAllLayers();
  }
  refreshAuthenticatedWorkspace(user).catch(function(err) {
    console.error('Failed to refresh authenticated workspace', err);
  });
}

function showAuthGate(message) {
  authenticatedUser = null;
  updateAuthenticatedUser();
  setAuthError(message || '');
  document.getElementById('authGate').style.display = 'flex';
  closeDrone3D();
  renderMyFlightPlans([]);
  stopMyDroneRefresh();
  renderMyDrones([]);
}

function decodeJwtPayload(token) {
  const parts = token.split('.');
  if (parts.length !== 3) {
    throw new Error('Invalid Google credential received.');
  }

  const padded = parts[1]
    .replace(/-/g, '+')
    .replace(/_/g, '/')
    .padEnd(Math.ceil(parts[1].length / 4) * 4, '=');
  return JSON.parse(atob(padded));
}

async function handleGoogleCredential(response) {
  try {
    setAuthError('Saving login...');
    const claims = decodeJwtPayload(response.credential || '');
    const user = {
      email: claims.email || '',
      display_name: claims.name || '',
      google_user_id: claims.sub || '',
      id_token: response.credential,
      app: 'visualise_zones_web',
    };

    if (!user.email) {
      throw new Error('Google did not return an email address for this account.');
    }

    const res = await fetch('/api/auth/google-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(user),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Backend login log failed.');
    }

    const data = await res.json().catch(() => ({}));
    unlockAuthenticatedUi(data.user || user);
  } catch (err) {
    console.error('Google login failed', err);
    showAuthGate(err && err.message ? err.message : 'Google login failed.');
  }
}

function initGoogleLogin(attempt) {
  if (window.google && google.accounts && google.accounts.id) {
    google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: handleGoogleCredential,
      auto_select: false,
      cancel_on_tap_outside: false,
    });
    google.accounts.id.renderButton(
      document.getElementById('googleLoginButton'),
      {
        theme: 'filled_black',
        size: 'large',
        text: 'continue_with',
        shape: 'pill',
        width: 320,
      },
    );
    return;
  }

  if (attempt < 20) {
    window.setTimeout(function() {
      initGoogleLogin(attempt + 1);
    }, 250);
    return;
  }

  showAuthGate('Google Sign-In could not be loaded. Check the OAuth client ID and allowed origins.');
}

async function signOutCurrentUser() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (err) {
    console.error('Failed to clear backend session', err);
  }
  if (authenticatedUser && window.google && google.accounts && google.accounts.id) {
    google.accounts.id.disableAutoSelect();
    if (authenticatedUser.email) {
      google.accounts.id.revoke(authenticatedUser.email, function() {});
    }
  }
  showAuthGate('');
}

function mercatorToLngLat(x, y) {
  var lng = (x / 20037508.34) * 180.0;
  var lat = (y / 20037508.34) * 180.0;
  lat = 180.0 / Math.PI * (2.0 * Math.atan(Math.exp(lat * Math.PI / 180.0)) - Math.PI / 2.0);
  return [lng, lat];
}

function normalizeCoords(coords) {
  if (!Array.isArray(coords) || !coords.length) return coords;
  if (typeof coords[0] === 'number') {
    var x = coords[0], y = coords[1];
    if (Math.abs(x) > 180 || Math.abs(y) > 90) {
      return mercatorToLngLat(x, y);
    }
    return coords;
  }
  return coords.map(normalizeCoords);
}

function normalizeGeoJSON(geojson) {
  if (!geojson || !geojson.features) return geojson;
  return {
    type: geojson.type,
    features: geojson.features.map(function(feat) {
      return {
        type: feat.type,
        properties: feat.properties || {},
        geometry: feat.geometry ? {
          type: feat.geometry.type,
          coordinates: normalizeCoords(feat.geometry.coordinates)
        } : null
      };
    })
  };
}

// ========================================================================
// MAP INIT
// ========================================================================
const map = L.map('map', { zoomControl: true }).setView([45.9, 25.0], 7);

const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap', maxZoom: 19
});
const sat = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { attribution: 'Esri', maxZoom: 19 }
);
const dark = L.tileLayer(
  'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  { attribution: '&copy; CARTO', maxZoom: 19 }
);
dark.addTo(map);
L.control.layers({ 'Dark': dark, 'Street': osm, 'Satellite': sat }, null, { position: 'topright' }).addTo(map);

let crossMarker = null;
const DYNAMIC_AIRSPACE_LAYER_KEYS = ['uas_zones', 'notam', 'ctr', 'tma'];
const AIRSPACE_CATEGORY_FILTERS = ['ctr', 'tma', 'notam', 'restricted'];
let airspaceViewportRefreshTimer = null;
let airspaceViewportRequestId = 0;

// ========================================================================
// LAYER LOADING
// ========================================================================
async function loadAllLayers() {
  const keys = Object.keys(LAYERS_CFG).filter(function(key) {
    return DYNAMIC_AIRSPACE_LAYER_KEYS.indexOf(key) < 0;
  });
  const promises = keys.map(async k => {
    try {
      const resp = await fetch('/api/' + k);
      if (!resp.ok) return null;
      const data = await resp.json();
      return { key: k, data: data };
    } catch(e) { return null; }
  });
  const results = await Promise.all(promises);

  results.forEach(r => {
    if (!r) return;
    try {
      var normalized = normalizeGeoJSON(r.data);
      rawData[r.key] = normalized;
      buildMapLayer(r.key, normalized);
    } catch (e) {
      console.error('Failed to build layer', r.key, e);
    }
  });

  await loadViewportAirspaceLayers();
  buildLayerToggles();
  applyMode();
  applyAltFilter();
  updateStats();
}

function clearLayerArtifacts(key) {
  if (mapLayers[key]) {
    map.removeLayer(mapLayers[key]);
    delete mapLayers[key];
  }
  allFeatureIndex = allFeatureIndex.filter(function(entry) { return entry.key !== key; });
}

function normalizeAirspaceZoneFeature(zone) {
  var props = Object.assign({}, ((zone.metadata || {}).properties || {}));
  props.zone_id = props.zone_id || zone.zone_id || '';
  props.name = props.name || zone.name || '';
  props.category = props.category || zone.category || '';
  props.source = props.source || zone.source || '';
  if (props.lower_limit_m == null) props.lower_limit_m = zone.lower_altitude_m;
  if (props.upper_limit_m == null) props.upper_limit_m = zone.upper_altitude_m;
  if (props.valid_from == null) props.valid_from = zone.valid_from || null;
  if (props.valid_to == null) props.valid_to = zone.valid_to || null;
  return {
    type: 'Feature',
    properties: props,
    geometry: zone.geometry || null
  };
}

function buildDynamicAirspaceCollections(zones) {
  var collections = {};
  DYNAMIC_AIRSPACE_LAYER_KEYS.forEach(function(key) {
    collections[key] = { type: 'FeatureCollection', features: [] };
  });
  (zones || []).forEach(function(zone) {
    var layerKey = layerKeyFromAirspaceZone(zone);
    if (!collections[layerKey]) return;
    collections[layerKey].features.push(normalizeAirspaceZoneFeature(zone));
  });
  return collections;
}

async function loadViewportAirspaceLayers() {
  var bounds = map.getBounds();
  var bbox = [
    bounds.getWest().toFixed(6),
    bounds.getSouth().toFixed(6),
    bounds.getEast().toFixed(6),
    bounds.getNorth().toFixed(6)
  ].join(',');
  var requestId = ++airspaceViewportRequestId;
  const res = await fetch(
    '/airspace/zones?bbox=' + encodeURIComponent(bbox) +
    '&categories=' + encodeURIComponent(AIRSPACE_CATEGORY_FILTERS.join(','))
  );
  const data = await res.json().catch(function() { return {}; });
  if (!res.ok) {
    throw new Error(data.detail || data.error || 'Failed to load airspace zones.');
  }
  if (requestId !== airspaceViewportRequestId) return;

  var collections = buildDynamicAirspaceCollections(data.zones || []);
  DYNAMIC_AIRSPACE_LAYER_KEYS.forEach(function(key) {
    clearLayerArtifacts(key);
    rawData[key] = normalizeGeoJSON(collections[key]);
    buildMapLayer(key, rawData[key]);
  });
}

function scheduleViewportAirspaceRefresh() {
  clearTimeout(airspaceViewportRefreshTimer);
  airspaceViewportRefreshTimer = setTimeout(async function() {
    try {
      await loadViewportAirspaceLayers();
      buildLayerToggles();
      applyMode();
      applyAltFilter();
      updateStats();
    } catch (err) {
      console.error('Failed to refresh viewport airspace', err);
    }
  }, 200);
}

map.on('moveend', scheduleViewportAirspaceRefresh);

function buildMapLayer(key, geojson) {
  const cfg = LAYERS_CFG[key];
  const group = L.layerGroup();

  if (cfg.type === 'point') {
    L.geoJSON(geojson, {
      pointToLayer: function(feat, latlng) {
        return L.circleMarker(latlng, {
          radius: key === 'airports' ? 6 : 4,
          fillColor: cfg.color, color: cfg.color,
          weight: 1, fillOpacity: 0.8, opacity: 0.9,
        });
      },
      onEachFeature: function(feat, layer) {
        indexFeature(key, feat, layer);
        layer.bindPopup(function() { return buildPopup(key, feat.properties); });
      }
    }).addTo(group);
  } else if (cfg.type === 'line') {
    L.geoJSON(geojson, {
      style: { color: cfg.color, weight: 2, opacity: 0.7, dashArray: '6 4' },
      onEachFeature: function(feat, layer) {
        indexFeature(key, feat, layer);
        layer.bindPopup(function() { return buildPopup(key, feat.properties); });
      }
    }).addTo(group);
  } else {
    L.geoJSON(geojson, {
      style: function(feat) {
        return {
          color: cfg.color, fillColor: cfg.color,
          weight: 1.2, fillOpacity: 0.25, opacity: 0.8,
        };
      },
      onEachFeature: function(feat, layer) {
        indexFeature(key, feat, layer);
        layer.bindPopup(function() { return buildPopup(key, feat.properties); });
        layer.on('mouseover', function() { layer.setStyle({ fillOpacity: 0.5, weight: 2.5 }); });
        layer.on('mouseout',  function() { applyAltFilter(); });
      }
    }).addTo(group);
  }

  mapLayers[key] = group;
}

function indexFeature(key, feat, layer) {
  var p = feat.properties;
  var searchText = [
    p.zone_id, p.zone_code, p.name, p.icao, p.notam_id,
    p.arsp_name, p.ident, p.contact, p.airport,
    p.route_designator, p.iata_code,
  ].filter(Boolean).join(' ').toUpperCase();

  allFeatureIndex.push({ key: key, props: p, layer: layer, searchText: searchText, geometry: feat.geometry });
}

// ========================================================================
// POPUPS
// ========================================================================
function buildPopup(key, p) {
  var cfg = LAYERS_CFG[key];
  var html = '<div class="popup-title">' + popupTitle(key, p) + ' <span class="pill" style="background:' + cfg.color + '">' + cfg.label + '</span></div>';

  var rows = popupRows(key, p);
  rows.forEach(function(row) {
    html += '<div class="popup-row"><span class="popup-lbl">' + row[0] + '</span><span class="popup-val">' + row[1] + '</span></div>';
  });
  return html;
}

function popupTitle(key, p) {
  switch(key) {
    case 'uas_zones': return p.zone_id || 'UAS Zone';
    case 'notam':     return p.notam_id || p.zone_id || 'NOTAM UAS';
    case 'notam_all': return p.notam_id || p.serie || 'NOTAM';
    case 'ctr':       return p.name || p.arsp_name || 'CTR';
    case 'tma':       return p.name || p.arsp_name || 'TMA';
    case 'airports':  return (p.name || '') + ' (' + (p.icao || p.ident || '') + ')';
    case 'lower_routes': return p.route_designator || 'Route';
    default: return key;
  }
}

function popupRows(key, p) {
  var r = [];
  if (p.lower_lim_raw != null)  r.push(['Lower', p.lower_lim_raw + ' (' + fmtAlt(p.lower_limit_m) + ')']);
  if (p.upper_lim_raw != null)  r.push(['Upper', p.upper_lim_raw + ' (' + fmtAlt(p.upper_limit_m) + ')']);
  if (p.contact)                r.push(['Contact', p.contact]);
  if (p.status)                 r.push(['Status', p.status]);
  if (p.valid_from)             r.push(['From', p.valid_from]);
  if (p.valid_to)               r.push(['To', p.valid_to]);
  if (p.airport)                r.push(['Airport', p.airport]);
  if (p.icao)                   r.push(['ICAO', p.icao]);
  if (p.iata_code)              r.push(['IATA', p.iata_code]);
  if (p.route_designator)       r.push(['Route', p.route_designator]);
  if (p.from_fix)               r.push(['From fix', p.from_fix]);
  if (p.to_fix)                 r.push(['To fix', p.to_fix]);
  if (p.message) {
    var msg = p.message.replace(/\\r/g, ' ').replace(/\\s+/g, ' ');
    var short = msg.length > 200 ? msg.slice(0, 200) + '...' : msg;
    r.push(['Message', '<span style="font-size:.7rem">' + short + '</span>']);
  }
  return r;
}

function fmtAlt(m) {
  if (m == null) return 'N/A';
  if (m === 0) return 'GND';
  if (m < 1000) return Math.round(m) + ' m';
  return Math.round(m) + ' m / FL' + Math.round(m / 30.48);
}

// ========================================================================
// LAYER TOGGLES
// ========================================================================
function buildLayerToggles() {
  var container = document.getElementById('layerToggles');
  var previousVisibility = {};
  Object.keys(LAYERS_CFG).forEach(function(key) {
    var existing = document.getElementById('cb_' + key);
    if (existing) previousVisibility[key] = existing.checked;
  });
  container.innerHTML = '';
  Object.keys(LAYERS_CFG).forEach(function(key) {
    var cfg = LAYERS_CFG[key];
    var count = rawData[key] && rawData[key].features ? rawData[key].features.length : 0;

    // Build with DOM to avoid quote-escaping issues in inline event handlers
    var div = document.createElement('div');
    div.className = 'layer-item';

    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'layer-cb';
    cb.id = 'cb_' + key;
    cb.checked = Object.prototype.hasOwnProperty.call(previousVisibility, key)
      ? previousVisibility[key]
      : isLayerVisible(key);
    cb.addEventListener('change', (function(k) {
      return function() { toggleLayer(k); };
    })(key));

    var dot = document.createElement('span');
    dot.className = 'layer-dot';
    dot.style.background = cfg.color;

    var nameSpan = document.createElement('span');
    nameSpan.className = 'layer-name';
    nameSpan.textContent = cfg.label;

    var countSpan = document.createElement('span');
    countSpan.className = 'layer-count';
    countSpan.textContent = count;

    div.appendChild(cb);
    div.appendChild(dot);
    div.appendChild(nameSpan);
    div.appendChild(countSpan);
    container.appendChild(div);
  });
}

function isLayerVisible(key) {
  var existing = document.getElementById('cb_' + key);
  if (existing) return existing.checked;
  var cfg = LAYERS_CFG[key];
  return currentMode === 'drone' ? cfg.droneDefault : cfg.gaDefault;
}

function toggleLayer(key) {
  var cb = document.getElementById('cb_' + key);
  if (cb.checked) {
    if (mapLayers[key]) map.addLayer(mapLayers[key]);
  } else {
    if (mapLayers[key]) map.removeLayer(mapLayers[key]);
  }
  updateStats();
}

// ========================================================================
// MODE SWITCHING
// ========================================================================
function setMode(mode) {
  currentMode = mode;
  document.getElementById('btnDrone').classList.toggle('active', mode === 'drone');
  document.getElementById('btnGA').classList.toggle('active', mode === 'ga');

  if (mode === 'drone') {
    document.getElementById('altSlider').max = 500;
    document.getElementById('altSlider').step = 5;
    if (parseInt(document.getElementById('altSlider').value) > 500)
      document.getElementById('altSlider').value = 120;
  } else {
    document.getElementById('altSlider').max = 15000;
    document.getElementById('altSlider').step = 100;
  }

  applyMode();
  applyAltFilter();
}

function applyMode() {
  Object.keys(LAYERS_CFG).forEach(function(key) {
    var cfg = LAYERS_CFG[key];
    var shouldShow = currentMode === 'drone' ? cfg.droneDefault : cfg.gaDefault;
    var cb = document.getElementById('cb_' + key);
    if (cb) cb.checked = shouldShow;
    if (shouldShow) {
      if (mapLayers[key]) map.addLayer(mapLayers[key]);
    } else {
      if (mapLayers[key]) map.removeLayer(mapLayers[key]);
    }
  });
}

// ========================================================================
// ALTITUDE FILTER
// ========================================================================
function applyAltFilter() {
  var alt = parseInt(document.getElementById('altSlider').value, 10);
  document.getElementById('altValue').textContent = alt < 1000 ? alt + ' m' : 'FL' + Math.round(alt / 30.48);

  allFeatureIndex.forEach(function(entry) {
    var cfg = LAYERS_CFG[entry.key];
    if (cfg.type === 'point' || cfg.type === 'line') return;

    var lo = entry.props.lower_limit_m;
    var up = entry.props.upper_limit_m;
    var relevant = (lo == null || up == null) || (lo <= alt && alt <= up);

    if (relevant) {
      entry.layer.setStyle({ fillColor: cfg.color, color: cfg.color, fillOpacity: 0.25, opacity: 0.8, weight: 1.2 });
    } else {
      entry.layer.setStyle({ fillColor: '#333', color: '#444', fillOpacity: 0.05, opacity: 0.2, weight: 0.6 });
    }
  });

  updateStats();
}

document.getElementById('altSlider').addEventListener('input', applyAltFilter);

// ========================================================================
// STATS
// ========================================================================
function updateStats() {
  var alt = parseInt(document.getElementById('altSlider').value, 10);
  var totalVis = 0;
  Object.keys(LAYERS_CFG).forEach(function(k) {
    var cb = document.getElementById('cb_' + k);
    if (cb && cb.checked) totalVis += (rawData[k] && rawData[k].features) ? rawData[k].features.length : 0;
  });
  document.getElementById('stats').textContent =
    totalVis + ' features visible at ' + alt + ' m  |  ' + allFeatureIndex.length + ' total indexed';
}

// ========================================================================
// SEARCH
// ========================================================================
document.getElementById('searchBox').addEventListener('keydown', function(e) {
  if (e.key !== 'Enter') return;
  var q = e.target.value.trim().toUpperCase();
  if (!q) return;

  var hit = allFeatureIndex.find(function(f) { return f.searchText.indexOf(q) >= 0; });
  if (!hit) {
    document.getElementById('stats').textContent = '"' + q + '" not found';
    return;
  }
  if (hit.layer.getBounds) {
    map.fitBounds(hit.layer.getBounds(), { maxZoom: 13 });
  } else if (hit.layer.getLatLng) {
    map.setView(hit.layer.getLatLng(), 13);
  }
  hit.layer.openPopup();
  e.target.value = '';
});

// ========================================================================
// CROSS-CHECK (click map)
// ========================================================================
map.on('click', function(e) {
  var lat = e.latlng.lat, lng = e.latlng.lng;
  var alt = parseInt(document.getElementById('altSlider').value, 10);

  if (crossMarker) map.removeLayer(crossMarker);
  crossMarker = L.marker([lat, lng], {
    icon: L.divIcon({
      className: '',
      html: '<div style="width:14px;height:14px;background:#e94560;border:2px solid #fff;border-radius:50%;box-shadow:0 0 6px rgba(0,0,0,.5);"></div>',
      iconSize: [14, 14], iconAnchor: [7, 7],
    })
  }).addTo(map);

  fetch('/api/crosscheck?lon=' + lng.toFixed(6) + '&lat=' + lat.toFixed(6) + '&alt=' + alt)
    .then(function(r) { return r.json(); })
    .then(function(results) { showCrossPanel(lat, lng, alt, results); })
    .catch(function(err) { console.error('Cross-check error', err); });
});

function showCrossPanel(lat, lng, alt, results) {
  var panel = document.getElementById('crossPanel');
  var title = document.getElementById('crossTitle');
  var body  = document.getElementById('crossResults');

  title.textContent = 'Cross-check: ' + lat.toFixed(4) + ', ' + lng.toFixed(4) + ' @ ' + alt + ' m';

  if (!results || Object.keys(results).length === 0) {
    body.innerHTML = '<div style="color:var(--muted);font-size:.8rem;padding:8px 0;">No zones at this location & altitude</div>';
  } else {
    var html = '';
    Object.keys(results).forEach(function(layerKey) {
      var features = results[layerKey];
      var cfg = LAYERS_CFG[layerKey] || { label: layerKey, color: '#888' };
      features.forEach(function(f) {
        var name = f.zone_id || f.notam_id || f.name || f.arsp_name || f.serie || layerKey;
        var altTxt = (f.lower_limit_m != null && f.upper_limit_m != null)
          ? Math.round(f.lower_limit_m) + '-' + Math.round(f.upper_limit_m) + ' m'
          : '';
        html += '<div class="cross-item">' +
          '<span class="cross-layer" style="background:' + cfg.color + '">' + cfg.label + '</span>' +
          '<span class="cross-name">' + name + '</span>' +
          '<span class="cross-alt">' + altTxt + '</span>' +
        '</div>';
      });
    });
    body.innerHTML = html;
  }
  panel.style.display = 'block';
}

function closeCross() {
  document.getElementById('crossPanel').style.display = 'none';
  if (crossMarker) { map.removeLayer(crossMarker); crossMarker = null; }
}

// ========================================================================
// FLIGHT PLAN WIZARD
// ========================================================================
let fpAreaPickMode = null;
let fpCircle = null;
let fpCentre = null;
let fpPolygonPoints = [];
let fpPolygonLayer = null;
let fpPolygonMarkers = [];
let fpAreaResult = null;
let fpSavedPlan = null;

function formatLocalDate(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function formatLocalTime(date) {
  return [
    String(date.getHours()).padStart(2, '0'),
    String(date.getMinutes()).padStart(2, '0'),
  ].join(':');
}

function populateTwrSelect() {
  var select = document.getElementById('fpTwr');
  if (!select) return;
  var keys = Object.keys(TOWER_DATA || {}).sort();
  select.innerHTML = keys
    .filter(function(key) { return (TOWER_DATA[key] || {}).type !== 'military'; })
    .map(function(key) {
      var item = TOWER_DATA[key] || {};
      var city = item.city || item.name || key;
      return '<option value="' + key + '">' + city + ' - ' + key + '</option>';
    })
    .join('');
}

function prefillFlightPlanForm() {
  if (!authenticatedUser) return;
  var now = new Date();
  var later = new Date(now.getTime() + 60 * 60 * 1000);
  if (!document.getElementById('fp_operator').value) {
    document.getElementById('fp_operator').value = authenticatedUser.display_name || authenticatedUser.email || '';
  }
  if (!document.getElementById('fp_contact_person').value) {
    document.getElementById('fp_contact_person').value = authenticatedUser.display_name || '';
  }
  if (!document.getElementById('fp_email').value) {
    document.getElementById('fp_email').value = authenticatedUser.email || '';
  }
  if (!document.getElementById('fp_pilot').value) {
    document.getElementById('fp_pilot').value = authenticatedUser.display_name || '';
  }
  if (!document.getElementById('fp_date1').value) {
    document.getElementById('fp_date1').value = formatLocalDate(now);
  }
  if (!document.getElementById('fp_date2').value) {
    document.getElementById('fp_date2').value = formatLocalDate(now);
  }
  if (!document.getElementById('fp_time1').value) {
    document.getElementById('fp_time1').value = formatLocalTime(now);
  }
  if (!document.getElementById('fp_time2').value) {
    document.getElementById('fp_time2').value = formatLocalTime(later);
  }
}

function launchFlightPlan() {
  if (!authenticatedUser) {
    alert('Sign in with Google before creating a flight plan.');
    return;
  }
  fpSavedPlan = null;
  document.getElementById('fpSavedSummary').innerHTML = '';
  document.getElementById('contactCards').innerHTML = '';
  populateTwrSelect();
  prefillFlightPlanForm();
  document.getElementById('fpOverlay').style.display = 'block';
  setAreaKind(document.getElementById('fpAreaKind').value || 'circle');
  showStep(1);
}

function closeFlightPlan() {
  document.getElementById('fpOverlay').style.display = 'none';
  fpAreaPickMode = null;
  document.getElementById('fpOverlay').style.pointerEvents = 'all';
}

function showStep(n) {
  [1,2,3,4].forEach(function(i) {
    document.getElementById('wizStep' + i).classList.toggle('active', i === n);
    var dot = document.getElementById('stepDot' + i);
    if (dot) {
      dot.className = 'step-dot' + (i < n ? ' done' : i === n ? ' active' : '');
    }
  });
  renderFlightPlanActions(n);
}

function renderFlightPlanActions(step) {
  var html = '';
  if (step === 1) {
    html =
      '<button class="btn-secondary" type="button" onclick="closeFlightPlan()">Close</button>' +
      '<button class="btn-secondary" type="button" onclick="startAreaSelection()">Pick on Map</button>' +
      '<button class="btn-primary" id="fpCheckBtn" type="button" onclick="checkFpArea()">Check Area</button>';
  } else if (step === 2) {
    html =
      '<button class="btn-secondary" type="button" onclick="showStep(1)">Back</button>' +
      '<button class="btn-primary" type="button" onclick="showStep(3)">Continue</button>';
  } else if (step === 3) {
    html =
      '<button class="btn-secondary" type="button" onclick="showStep(2)">Back</button>' +
      '<button class="btn-primary" id="fpSaveBtn" type="button" onclick="saveFlightPlan()">Save Flight Plan</button>';
  } else {
    html =
      '<button class="btn-secondary" type="button" onclick="closeFlightPlan()">Close</button>' +
      '<button class="btn-success" type="button" onclick="loadMyFlightPlans(true)">Refresh My Plans</button>';
  }
  document.getElementById('fpActions').innerHTML = html;
}

function setAreaKind(kind) {
  document.getElementById('fpAreaKind').value = kind;
  document.getElementById('fpCircleFields').style.display = kind === 'circle' ? 'block' : 'none';
  document.getElementById('fpPolygonFields').style.display = kind === 'polygon' ? 'block' : 'none';
  document.getElementById('fpDrawHint').style.display = 'none';
  fpAreaPickMode = null;
  if (kind === 'circle') {
    renderPolygonSummary();
    syncCircleFromInputs();
  } else {
    clearFpCircle();
    renderPolygonSummary();
  }
}

function pointInRingJs(lon, lat, ring) {
  var inside = false;
  var j = ring.length - 1;
  for (var i = 0; i < ring.length; i += 1) {
    var xi = ring[i][0];
    var yi = ring[i][1];
    var xj = ring[j][0];
    var yj = ring[j][1];
    if (((yi > lat) !== (yj > lat)) && (lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi)) {
      inside = !inside;
    }
    j = i;
  }
  return inside;
}

function featureContainsPointJs(feature, lon, lat, altM) {
  if (!feature || !feature.geometry) return false;
  var props = feature.properties || {};
  var lo = props.lower_limit_m;
  var up = props.upper_limit_m;
  if (lo != null && up != null && (altM < lo || altM > up)) {
    return false;
  }
  var geom = feature.geometry;
  var coords = geom.coordinates || [];
  if (geom.type === 'Polygon') {
    return coords.some(function(ring) { return pointInRingJs(lon, lat, ring); });
  }
  if (geom.type === 'MultiPolygon') {
    return coords.some(function(poly) {
      return poly.some(function(ring) { return pointInRingJs(lon, lat, ring); });
    });
  }
  return false;
}

function formatBlockingHit(feature, layerKey) {
  var props = feature.properties || {};
  return {
    layerKey: layerKey,
    label: (LAYERS_CFG[layerKey] && LAYERS_CFG[layerKey].label) || layerKey,
    name: props.zone_id || props.zone_code || props.notam_id || props.name || props.arsp_name || layerKey,
  };
}

function layerKeyFromAirspaceZone(zone) {
  var source = ((zone && zone.source) || '').toLowerCase();
  var category = ((zone && zone.category) || '').toLowerCase();
  if (category === 'ctr' || source.indexOf('_ctr') >= 0) return 'ctr';
  if (category === 'tma' || source.indexOf('_tma') >= 0) return 'tma';
  if (category === 'temporary_restriction' || source.indexOf('notam') >= 0) return 'notam';
  return 'uas_zones';
}

async function fetchPointCheck(lon, lat, altM) {
  const res = await fetch(
    '/airspace/check-point?lon=' + encodeURIComponent(lon) +
    '&lat=' + encodeURIComponent(lat) +
    '&alt_m=' + encodeURIComponent(altM)
  );
  const data = await res.json().catch(function() { return {}; });
  if (!res.ok) {
    throw new Error(data.detail || data.error || 'Airspace point check failed.');
  }
  return data;
}

async function fetchRouteCheck(pathPoints) {
  const res = await fetch('/airspace/check-route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: pathPoints }),
  });
  const data = await res.json().catch(function() { return {}; });
  if (!res.ok) {
    throw new Error(data.detail || data.error || 'Airspace route check failed.');
  }
  return data;
}

function pointCheckToBlockingHits(data) {
  return ((data && data.zones) || [])
    .map(function(zone) {
      var layerKey = layerKeyFromAirspaceZone(zone);
      return {
        layerKey: layerKey,
        label: (LAYERS_CFG[layerKey] && LAYERS_CFG[layerKey].label) || layerKey,
        name: zone.zone_id || zone.name || layerKey,
      };
    })
    .filter(function(hit) {
      return CENTER_BLOCKING_LAYER_KEYS.indexOf(hit.layerKey) >= 0;
    });
}

function summarizeBlockingHits(hits) {
  return hits.slice(0, 4).map(function(hit) {
    return hit.label + ': ' + hit.name;
  }).join(', ') + (hits.length > 4 ? ', ...' : '');
}

function rejectCircleCenter(hits, interactive) {
  if (fpCircle) {
    map.removeLayer(fpCircle);
    fpCircle = null;
  }
  fpCentre = null;
  var message = 'Circle centre is not allowed inside CTR/UAS/NOTAM/TMA areas: ' + summarizeBlockingHits(hits);
  document.getElementById('fpCircleInfo').textContent = message;
  if (interactive) {
    alert(message);
  }
  return false;
}

async function setCircleCenter(lat, lon, interactive, syncInputs) {
  var altM = parseFloat(document.getElementById('fpAlt').value) || 120;
  try {
    var hits = pointCheckToBlockingHits(await fetchPointCheck(lon, lat, altM));
    if (hits.length) {
      return rejectCircleCenter(hits, interactive);
    }
  } catch (err) {
    if (interactive) {
      alert(err && err.message ? err.message : 'Airspace point check failed.');
    }
    return false;
  }
  fpCentre = { lat: lat, lon: lon };
  if (syncInputs) {
    document.getElementById('fpLat').value = lat.toFixed(6);
    document.getElementById('fpLon').value = lon.toFixed(6);
  }
  updateFpCircle();
  return true;
}

function clearFpCircle() {
  if (fpCircle) {
    map.removeLayer(fpCircle);
    fpCircle = null;
  }
  fpCentre = null;
  document.getElementById('fpCircleInfo').textContent = '';
}

function clearFpPolygon() {
  fpPolygonPoints = [];
  if (fpPolygonLayer) {
    map.removeLayer(fpPolygonLayer);
    fpPolygonLayer = null;
  }
  fpPolygonMarkers.forEach(function(marker) { map.removeLayer(marker); });
  fpPolygonMarkers = [];
  renderPolygonSummary();
}

function addPolygonVertex(lon, lat) {
  if (fpPolygonPoints.length >= 5) {
    throw new Error('ANEXA 1 allows a maximum of 5 polygon vertices.');
  }
  fpPolygonPoints.push([lon, lat]);
  updateFpPolygon();
}

function addPolygonPointFromInputs() {
  try {
    var lat = parseFloat(document.getElementById('fpPolyLat').value);
    var lon = parseFloat(document.getElementById('fpPolyLon').value);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      throw new Error('Enter both polygon latitude and longitude.');
    }
    addPolygonVertex(lon, lat);
    document.getElementById('fpPolyLat').value = '';
    document.getElementById('fpPolyLon').value = '';
  } catch (err) {
    alert(err && err.message ? err.message : 'Failed to add polygon point.');
  }
}

function clearFlightArea() {
  if (document.getElementById('fpAreaKind').value === 'polygon') {
    clearFpPolygon();
  } else {
    clearFpCircle();
    document.getElementById('fpLat').value = '';
    document.getElementById('fpLon').value = '';
  }
}

function startAreaSelection() {
  var kind = document.getElementById('fpAreaKind').value || 'circle';
  fpAreaPickMode = kind;
  document.getElementById('fpDrawHint').style.display = 'block';
  document.getElementById('fpOverlay').style.pointerEvents = 'none';
  document.getElementById('fpWizard').style.pointerEvents = 'all';
  document.getElementById('fpCircleInfo').textContent =
    kind === 'polygon'
      ? 'Click the map to add up to 5 polygon vertices.'
      : 'Click the map to place the circular area centre.';
}

let fpCircleSyncTimer = null;

function syncCircleFromInputs() {
  clearTimeout(fpCircleSyncTimer);
  fpCircleSyncTimer = setTimeout(async function() {
  var lat = parseFloat(document.getElementById('fpLat').value);
  var lon = parseFloat(document.getElementById('fpLon').value);
  if (Number.isFinite(lat) && Number.isFinite(lon)) {
      await setCircleCenter(lat, lon, false, false);
  }
  }, 250);
}

async function ensureCircleSelection() {
  if ((document.getElementById('fpAreaKind').value || 'circle') !== 'circle') {
    return;
  }
  var lat = parseFloat(document.getElementById('fpLat').value);
  var lon = parseFloat(document.getElementById('fpLon').value);
  if (Number.isFinite(lat) && Number.isFinite(lon)) {
    if (!fpCentre || Math.abs(fpCentre.lat - lat) > 0.000001 || Math.abs(fpCentre.lon - lon) > 0.000001) {
      var ok = await setCircleCenter(lat, lon, false, false);
      if (!ok) {
        throw new Error(document.getElementById('fpCircleInfo').textContent || 'Circle centre is not allowed.');
      }
    }
    return;
  }
  if (!fpCentre) {
    throw new Error('Set the circular area centre first.');
  }
}

function buildRoutePathForArea(payload, altM) {
  if (payload.area_kind === 'polygon') {
    var path = payload.polygon_points.slice().map(function(point) {
      return { lon: point[0], lat: point[1], alt_m: altM };
    });
    if (path.length && (path[0].lon !== path[path.length - 1].lon || path[0].lat !== path[path.length - 1].lat)) {
      path.push({ lon: path[0].lon, lat: path[0].lat, alt_m: altM });
    }
    return path;
  }

  var radiusM = parseFloat(payload.radius_m) || 0;
  var steps = 16;
  var latRad = payload.center_lat * Math.PI / 180;
  var angularDistance = radiusM / 6371000;
  var path = [];
  for (var i = 0; i <= steps; i += 1) {
    var bearing = (2 * Math.PI * i) / steps;
    var lat2 = Math.asin(
      Math.sin(latRad) * Math.cos(angularDistance) +
      Math.cos(latRad) * Math.sin(angularDistance) * Math.cos(bearing)
    );
    var lon2 = (payload.center_lon * Math.PI / 180) + Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(latRad),
      Math.cos(angularDistance) - Math.sin(latRad) * Math.sin(lat2)
    );
    path.push({
      lon: lon2 * 180 / Math.PI,
      lat: lat2 * 180 / Math.PI,
      alt_m: altM,
    });
  }
  return path;
}

async function preflightAreaBackendChecks(payload) {
  var altM = parseFloat(payload.max_altitude_m) || 120;
  if (payload.area_kind === 'circle') {
    await fetchPointCheck(payload.center_lon, payload.center_lat, altM);
  }
  var routePath = buildRoutePathForArea(payload, altM);
  if (routePath.length >= 2) {
    await fetchRouteCheck(routePath);
  }
}

function updateFpCircle() {
  if (!fpCentre) return;
  var radius = parseFloat(document.getElementById('fpRadius').value) || 200;
  if (fpCircle) map.removeLayer(fpCircle);
  fpCircle = L.circle([fpCentre.lat, fpCentre.lon], {
    radius: radius,
    color: '#e94560',
    fillColor: '#e94560',
    weight: 2,
    fillOpacity: 0.18,
    dashArray: '6 4',
    interactive: false,
  }).addTo(map);
  map.panTo([fpCentre.lat, fpCentre.lon]);
  document.getElementById('fpCircleInfo').textContent =
    'Circle centre: ' + fpCentre.lat.toFixed(5) + ', ' + fpCentre.lon.toFixed(5) +
    ' / radius ' + Math.round(radius) + ' m';
}

function renderPolygonSummary() {
  var html = '';
  if (!fpPolygonPoints.length) {
    html = '<div class="muted">No polygon points selected yet.</div>';
  } else {
    html = fpPolygonPoints.map(function(point, index) {
      return '<div class="saved-row"><span>P' + (index + 1) + '</span>' +
        point[1].toFixed(6) + ', ' + point[0].toFixed(6) + '</div>';
    }).join('');
  }
  document.getElementById('fpPolygonSummary').innerHTML = html;
}

function updateFpPolygon() {
  if (fpPolygonLayer) {
    map.removeLayer(fpPolygonLayer);
    fpPolygonLayer = null;
  }
  fpPolygonMarkers.forEach(function(marker) { map.removeLayer(marker); });
  fpPolygonMarkers = [];

  fpPolygonPoints.forEach(function(point, index) {
    var marker = L.circleMarker([point[1], point[0]], {
      radius: 5,
      color: '#58a6ff',
      fillColor: '#58a6ff',
      fillOpacity: 0.9,
      weight: 1,
    }).bindTooltip('P' + (index + 1), { permanent: true, direction: 'top', offset: [0, -8] });
    marker.addTo(map);
    fpPolygonMarkers.push(marker);
  });

  if (fpPolygonPoints.length >= 2) {
    fpPolygonLayer = L.polygon(
      fpPolygonPoints.map(function(point) { return [point[1], point[0]]; }),
      {
        color: '#58a6ff',
        fillColor: '#58a6ff',
        fillOpacity: 0.16,
        weight: 2,
      }
    ).addTo(map);
    map.fitBounds(fpPolygonLayer.getBounds(), { padding: [20, 20] });
  }

  renderPolygonSummary();
  document.getElementById('fpCircleInfo').textContent =
    fpPolygonPoints.length
      ? 'Polygon points selected: ' + fpPolygonPoints.length + ' / 5'
      : '';
}

function undoPolygonPoint() {
  if (!fpPolygonPoints.length) return;
  fpPolygonPoints.pop();
  updateFpPolygon();
}

async function onMapClickFP(e) {
  if (!fpAreaPickMode) return;
  if (fpAreaPickMode === 'circle') {
    if (!await setCircleCenter(e.latlng.lat, e.latlng.lng, true, true)) {
      return;
    }
    fpAreaPickMode = null;
    document.getElementById('fpDrawHint').style.display = 'none';
    document.getElementById('fpOverlay').style.pointerEvents = 'all';
    return;
  }

  if (fpAreaPickMode === 'polygon') {
    if (fpPolygonPoints.length >= 5) {
      fpAreaPickMode = null;
      document.getElementById('fpDrawHint').style.display = 'none';
      document.getElementById('fpOverlay').style.pointerEvents = 'all';
      return;
    }
    addPolygonVertex(e.latlng.lng, e.latlng.lat);
    if (fpPolygonPoints.length >= 5) {
      fpAreaPickMode = null;
      document.getElementById('fpDrawHint').style.display = 'none';
      document.getElementById('fpOverlay').style.pointerEvents = 'all';
    }
  }
}

function getCurrentAreaPayload() {
  var areaKind = document.getElementById('fpAreaKind').value || 'circle';
  if (areaKind === 'polygon') {
    if (fpPolygonPoints.length < 3) {
      throw new Error('Add at least 3 polygon vertices on the map.');
    }
    return {
      area_kind: 'polygon',
      polygon_points: fpPolygonPoints.slice(),
    };
  }

  if (!fpCentre) {
    throw new Error('Set the circular area centre first.');
  }
  return {
    area_kind: 'circle',
    center_lat: fpCentre.lat,
    center_lon: fpCentre.lon,
    radius_m: parseFloat(document.getElementById('fpRadius').value) || 200,
  };
}

async function checkFpArea() {
  try {
    await ensureCircleSelection();
    var payload = getCurrentAreaPayload();
    payload.max_altitude_m = parseFloat(document.getElementById('fpAlt').value) || 120;
    await preflightAreaBackendChecks(payload);
    document.getElementById('fpCheckBtn').textContent = 'Checking...';
    const res = await fetch('/api/flight-plans/assess', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || 'Area check failed.');
    }
    fpAreaResult = data;
    showRiskResults(data);
    showStep(2);
  } catch (err) {
    alert(err && err.message ? err.message : 'Area check failed.');
  } finally {
    var btn = document.getElementById('fpCheckBtn');
    if (btn) btn.textContent = 'Check Area';
  }
}

function showRiskResults(data) {
  var risk = data.risk_level || 'LOW';
  document.getElementById('riskBadge').textContent = risk;
  document.getElementById('riskBadge').className = 'risk-badge risk-' + risk;
  document.getElementById('riskSummary').textContent = data.summary || '';

  var warnings = (data.warnings || []).map(function(warning) {
    return '<div class="warning-box">' + warning + '</div>';
  }).join('');
  document.getElementById('riskWarnings').innerHTML = warnings;

  var html = '';
  function addHits(hits, label, color) {
    if (!hits || !hits.length) return;
    hits.forEach(function(hit) {
      var name = hit.zone_id || hit.notam_id || hit.name || hit.arsp_name || label;
      var alt = (hit.lower_limit_m != null && hit.upper_limit_m != null)
        ? ' (' + Math.round(hit.lower_limit_m) + '-' + Math.round(hit.upper_limit_m) + ' m)'
        : '';
      html += '<div class="hit-item"><span class="hit-layer" style="background:' + color + '">' +
        label + '</span>' + name + '<span style="color:var(--muted)">' + alt + '</span></div>';
    });
  }
  addHits(data.ctr_hits, 'CTR', '#58a6ff');
  addHits(data.uas_hits, 'UAS Zone', '#e94560');
  addHits(data.notam_hits, 'NOTAM', '#ff9800');
  addHits(data.tma_hits, 'TMA', '#3fb950');
  document.getElementById('riskHits').innerHTML = html || '<div style="color:var(--muted);font-size:.8rem">No conflicting zones found.</div>';

  if (data.tower_contacts && data.tower_contacts.length > 0 && data.tower_contacts[0].icao) {
    document.getElementById('fpTwr').value = data.tower_contacts[0].icao;
  }
}

async function collectFlightPlanPayload() {
  await ensureCircleSelection();
  var payload = getCurrentAreaPayload();
  payload.operator_name = document.getElementById('fp_operator').value;
  payload.operator_contact = document.getElementById('fp_address').value;
  payload.contact_person = document.getElementById('fp_contact_person').value;
  payload.phone_landline = document.getElementById('fp_phone_landline').value;
  payload.phone_mobile = document.getElementById('fp_mobil').value;
  payload.fax = document.getElementById('fp_fax').value;
  payload.operator_email = document.getElementById('fp_email').value;
  payload.uas_registration = document.getElementById('fp_reg').value;
  payload.mtom_kg = document.getElementById('fp_weight').value;
  payload.uas_class_code = document.getElementById('fp_class').value;
  payload.category = document.getElementById('fp_cat').value;
  payload.operation_mode = document.getElementById('fp_mode').value;
  payload.pilot_name = document.getElementById('fp_pilot').value;
  payload.pilot_phone = document.getElementById('fp_pphone').value;
  payload.purpose = document.getElementById('fp_purpose').value;
  payload.max_altitude_m = document.getElementById('fpAlt').value;
  payload.start_date = document.getElementById('fp_date1').value;
  payload.end_date = document.getElementById('fp_date2').value;
  payload.start_time = document.getElementById('fp_time1').value;
  payload.end_time = document.getElementById('fp_time2').value;
  payload.location_name = document.getElementById('fp_loc').value;
  payload.selected_twr = document.getElementById('fpTwr').value;
  payload.timezone = 'Europe/Bucharest';
  payload.created_from_app = 'visualise_zones_web';
  await preflightAreaBackendChecks(payload);
  return payload;
}

async function saveFlightPlan() {
  try {
    var payload = await collectFlightPlanPayload();
    var saveBtn = document.getElementById('fpSaveBtn');
    if (saveBtn) saveBtn.textContent = 'Saving...';
    const res = await fetch('/api/flight-plans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || 'Failed to save the flight plan.');
    }
    fpSavedPlan = data.flight_plan || null;
    if (fpSavedPlan && fpSavedPlan.airspace_assessment) {
      fpAreaResult = fpSavedPlan.airspace_assessment;
    }
    showSavedFlightPlan();
    showStep(4);
    loadMyFlightPlans(false);
  } catch (err) {
    alert(err && err.message ? err.message : 'Failed to save the flight plan.');
  } finally {
    var saveBtn = document.getElementById('fpSaveBtn');
    if (saveBtn) saveBtn.textContent = 'Save Flight Plan';
  }
}

function showSavedFlightPlan() {
  if (!fpSavedPlan) {
    document.getElementById('fpSavedSummary').innerHTML = '';
    return;
  }

  var html =
    '<div class="saved-plan-card">' +
      '<div class="saved-title">' + (fpSavedPlan.public_id || 'Flight Plan Saved') + '</div>' +
      '<div class="saved-row"><span>Owner</span>' + (fpSavedPlan.owner_display_name || fpSavedPlan.owner_email || '') + '</div>' +
      '<div class="saved-row"><span>Schedule</span>' + (fpSavedPlan.scheduled_start_local || '') + ' -> ' + (fpSavedPlan.scheduled_end_local || '') + '</div>' +
      '<div class="saved-row"><span>Location</span>' + (fpSavedPlan.location_name || '') + '</div>' +
      '<div class="saved-row"><span>TWR</span>' + (fpSavedPlan.selected_twr || '') + '</div>' +
      '<div class="saved-row"><span>Risk</span>' + (fpSavedPlan.risk_level || 'LOW') + '</div>' +
      '<div class="saved-row"><span>PDF</span><a href="' + (fpSavedPlan.download_url || '#') + '" target="_blank">Download ANEXA 1 PDF</a></div>' +
    '</div>';
  document.getElementById('fpSavedSummary').innerHTML = html;
  showContactInfo();
}

function showContactInfo() {
  var contacts = fpAreaResult && fpAreaResult.tower_contacts ? fpAreaResult.tower_contacts : [];
  var selectedTwr = document.getElementById('fpTwr').value;
  var html = '';
  var shown = new Set();

  contacts.forEach(function(contact) {
    if (!contact.icao) return;
    shown.add(contact.icao);
    html += buildContactCard(contact);
  });

  if (selectedTwr && !shown.has(selectedTwr) && TOWER_DATA[selectedTwr]) {
    html += buildContactCard(Object.assign({ icao: selectedTwr }, TOWER_DATA[selectedTwr]));
  }

  if (!html) {
    html = '<div style="color:var(--muted);font-size:.8rem">No CTR detected. Submit via ' +
      '<a href="https://flightplan.romatsa.ro" target="_blank" style="color:var(--blue)">flightplan.romatsa.ro</a></div>';
  }
  document.getElementById('contactCards').innerHTML = html;
}

function buildContactCard(contact) {
  var phones = (contact.phone || []).join(', ');
  var emailLink = contact.email
    ? '<a href="' + buildMailto(contact) + '" style="color:var(--blue)">' + contact.email + '</a>'
    : '-';
  var note = contact.note
    ? '<div style="color:var(--orange);font-size:.7rem;margin-top:4px">' + contact.note + '</div>'
    : '';
  return '<div class="contact-card">' +
    '<div class="cc-name">' + (contact.icao || '') + ' - ' + (contact.name || '') + '</div>' +
    '<div class="cc-row"><span class="cc-lbl">Phone</span><span>' + phones + '</span></div>' +
    '<div class="cc-row"><span class="cc-lbl">Email</span><span>' + emailLink + '</span></div>' +
    note +
  '</div>';
}

function buildMailto(contact) {
  var subject = encodeURIComponent('Notificare operare UAS in CTR ' + (contact.icao || ''));
  var nl = '\\n';
  var body = encodeURIComponent(
    'Buna ziua,' + nl + nl +
    'Va transmit atasat Anexa 1 pentru operarea UAS in CTR ' + (contact.icao || '') + '.' + nl + nl +
    'Locatia: ' + (document.getElementById('fp_loc').value || 'N/A') + nl +
    'Data: ' + (document.getElementById('fp_date1').value || 'N/A') + nl +
    'Altitudine maxima: ' + (document.getElementById('fpAlt').value || 'N/A') + ' m' + nl + nl +
    'Cu stima,' + nl + (document.getElementById('fp_operator').value || '')
  );
  return 'mailto:' + (contact.email || '') + '?subject=' + subject + '&body=' + body;
}

function openRomatsaPortal() {
  window.open('https://flightplan.romatsa.ro', '_blank');
}

map.on('click', function(e) {
  onMapClickFP(e);
});

window.addEventListener('load', async function() {
  populateTwrSelect();
  var signOutBtn = document.getElementById('signOutBtn');
  if (signOutBtn) {
    signOutBtn.addEventListener('click', signOutCurrentUser);
  }

  var fpRadiusInput = document.getElementById('fpRadius');
  if (fpRadiusInput) {
    fpRadiusInput.addEventListener('input', updateFpCircle);
  }

  var restored = await restoreServerSession();
  if (!restored) {
    showAuthGate('');
    initGoogleLogin(0);
  }
});

// ========================================================================
// BOOT
// ========================================================================
</script>

<!-- ======================================================================
     FLIGHT PLAN WIZARD MODAL
     ====================================================================== -->
<div id="fpOverlay">
  <div id="fpWizard">
    <div class="wiz-head">
      <h2>&#9992; UAS Flight Notification (ANEXA 1)</h2>
      <button class="close-wiz" onclick="closeFlightPlan()">&times;</button>
    </div>
    <div class="step-indicator">
      <div class="step-dot active" id="stepDot1"></div>
      <div class="step-dot" id="stepDot2"></div>
      <div class="step-dot" id="stepDot3"></div>
      <div class="step-dot" id="stepDot4"></div>
    </div>
    <div class="wiz-body">

      <!-- Step 1: Define area -->
      <div class="wiz-step active" id="wizStep1">
        <h3>Step 1 &ndash; Define Flight Area</h3>
        <div class="fp-row">
          <label>Area shape from ANEXA 1</label>
          <select id="fpAreaKind" onchange="setAreaKind(this.value)">
            <option value="circle" selected>Circle</option>
            <option value="polygon">Polygon (ANEXA 1, max 5 points)</option>
          </select>
          <div class="area-note">
            Use <strong>Circle</strong> for center + radius notifications, or switch to
            <strong>Polygon</strong> to enter the ANEXA 1 vertices directly.
          </div>
        </div>
        <div class="draw-hint" id="fpDrawHint" style="display:none">
          <span class="hint-icon">&#128205;</span>
          Use the map to place the requested flight area
        </div>
        <div id="fpCircleFields">
          <div class="fp-2col">
            <div class="fp-row">
              <label>Center latitude</label>
              <input id="fpLat" type="number" step="0.000001" placeholder="44.4268" oninput="syncCircleFromInputs()"/>
            </div>
            <div class="fp-row">
              <label>Center longitude</label>
              <input id="fpLon" type="number" step="0.000001" placeholder="26.1025" oninput="syncCircleFromInputs()"/>
            </div>
          </div>
          <div class="fp-row">
            <label>Radius (metres)</label>
            <input id="fpRadius" type="number" min="50" max="5000" value="200"/>
          </div>
          <div class="area-note">
            The circle centre cannot be placed inside CTR, UAS, NOTAM, or TMA polygons.
          </div>
          <div class="inline-btn-row">
            <button class="inline-btn" type="button" onclick="startAreaSelection()">Pick center on map</button>
            <button class="inline-btn" type="button" onclick="clearFlightArea()">Clear circle</button>
          </div>
        </div>
        <div id="fpPolygonFields" style="display:none">
          <div class="fp-2col">
            <div class="fp-row">
              <label>Vertex latitude</label>
              <input id="fpPolyLat" type="number" step="0.000001" placeholder="44.426800"/>
            </div>
            <div class="fp-row">
              <label>Vertex longitude</label>
              <input id="fpPolyLon" type="number" step="0.000001" placeholder="26.102500"/>
            </div>
          </div>
          <div class="fp-row">
            <label>Polygon vertices</label>
            <div id="fpPolygonSummary" class="my-plan-card">
              <div class="muted">No polygon points selected yet.</div>
            </div>
          </div>
          <div class="inline-btn-row">
            <button class="inline-btn" type="button" onclick="addPolygonPointFromInputs()">Add typed vertex</button>
            <button class="inline-btn" type="button" onclick="startAreaSelection()">Add points on map</button>
          </div>
          <div class="inline-btn-row">
            <button class="inline-btn" type="button" onclick="undoPolygonPoint()">Undo last point</button>
            <button class="inline-btn" type="button" onclick="clearFlightArea()">Clear polygon</button>
          </div>
          <div class="area-note">
            Add between 3 and 5 vertices, in the same order you would complete ANEXA 1.
          </div>
        </div>
        <div class="fp-row">
          <label>Maximum altitude above ground (m)</label>
          <input id="fpAlt" type="number" min="0" max="120" value="120"/>
        </div>
        <div id="fpCircleInfo" style="font-size:.75rem;color:var(--muted);margin-top:6px"></div>
      </div>

      <!-- Step 2: Risk results -->
      <div class="wiz-step" id="wizStep2">
        <h3>Step 2 &ndash; Airspace Risk</h3>
        <div id="riskBadge" class="risk-badge risk-LOW">LOW</div>
        <div id="riskSummary" style="font-size:.8rem;margin-bottom:8px;color:var(--muted)"></div>
        <div id="riskWarnings"></div>
        <div id="riskHits" class="hit-list"></div>
      </div>

      <!-- Step 3: Flight plan form -->
      <div class="wiz-step" id="wizStep3">
        <h3>Step 3 &ndash; ANEXA 1 Details</h3>
        <div class="fp-row"><label>CTR (TWR)</label>
          <select id="fpTwr">
            <option value="LRAR">Arad - LRAR</option>
            <option value="LRBC">Bacau - LRBC</option>
            <option value="LRBM">Baia Mare - LRBM</option>
            <option value="LRBV">Brasov - LRBV</option>
            <option value="LRBS">Bucuresti Baneasa - LRBS</option>
            <option value="LROP">Bucuresti Otopeni - LROP</option>
            <option value="LRCL">Cluj-Napoca - LRCL</option>
            <option value="LRCK">Constanta - LRCK</option>
            <option value="LRCV">Craiova - LRCV</option>
            <option value="LRIA">Iasi - LRIA</option>
            <option value="LROD">Oradea - LROD</option>
            <option value="LRSM">Satu Mare - LRSM</option>
            <option value="LRSB">Sibiu - LRSB</option>
            <option value="LRSV">Suceava - LRSV</option>
            <option value="LRTM">Targu Mures - LRTM</option>
            <option value="LRTR">Timisoara - LRTR</option>
            <option value="LRTC">Tulcea - LRTC</option>
          </select>
        </div>
        <div class="fp-row"><label>Detinator / Operator UAS</label><input id="fp_operator" type="text"/></div>
        <div class="fp-row"><label>Date de contact</label><textarea id="fp_address" rows="2"></textarea></div>
        <div class="fp-row"><label>Persoana de contact</label><input id="fp_contact_person" type="text"/></div>
        <div class="fp-2col">
          <div class="fp-row"><label>Telefon fix</label><input id="fp_phone_landline" type="tel"/></div>
          <div class="fp-row"><label>Telefon mobil</label><input id="fp_mobil" type="tel"/></div>
        </div>
        <div class="fp-2col">
          <div class="fp-row"><label>Fax</label><input id="fp_fax" type="text"/></div>
          <div class="fp-row"><label>e-mail</label><input id="fp_email" type="email"/></div>
        </div>
        <div class="fp-2col">
          <div class="fp-row"><label>UAS Registration</label><input id="fp_reg" type="text"/></div>
          <div class="fp-row"><label>MTOM (kg)</label><input id="fp_weight" type="text"/></div>
        </div>
        <div class="fp-2col">
          <div class="fp-row"><label>Class</label>
            <select id="fp_class">
              <option value="PRV250">Private &lt;250g</option>
              <option value="C0">C0 &lt;250g</option>
              <option value="C1">C1 250g-900g</option>
              <option value="C2" selected>C2 900g-4kg</option>
              <option value="C3">C3 &lt;25kg</option>
              <option value="C4">C4 &lt;25kg</option>
              <option value="PRV25">Private &lt;25kg</option>
            </select>
          </div>
          <div class="fp-row"><label>Category</label>
            <select id="fp_cat">
              <option value="A1">A1</option>
              <option value="A2" selected>A2</option>
              <option value="A3">A3</option>
            </select>
          </div>
        </div>
        <div style="font-size:.72rem;color:var(--muted);margin-top:-2px;margin-bottom:10px">
          Note: the provided PDF template exports both C1 and C2 as the same underlying field value. The backend stores your real class code separately.
        </div>
        <div class="fp-row"><label>Operation Mode</label>
          <select id="fp_mode">
            <option value="VLOS" selected>VLOS</option>
            <option value="VBLOS">BVLOS</option>
          </select>
        </div>
        <div class="fp-row"><label>Pilot Name</label><input id="fp_pilot" type="text"/></div>
        <div class="fp-row"><label>Pilot Phone</label><input id="fp_pphone" type="tel"/></div>
        <div class="fp-row"><label>Flight Purpose</label><textarea id="fp_purpose" rows="2"></textarea></div>
        <div class="fp-row"><label>Location Name</label><input id="fp_loc" type="text"/></div>
        <div class="fp-2col">
          <div class="fp-row"><label>Start Date (LT)</label><input id="fp_date1" type="date"/></div>
          <div class="fp-row"><label>End Date (LT)</label><input id="fp_date2" type="date"/></div>
        </div>
        <div class="fp-2col">
          <div class="fp-row"><label>Start Time (LT)</label><input id="fp_time1" type="time" value="09:00"/></div>
          <div class="fp-row"><label>End Time (LT)</label><input id="fp_time2" type="time" value="10:00"/></div>
        </div>
      </div>

      <!-- Step 4: Saved plan -->
      <div class="wiz-step" id="wizStep4">
        <h3>Step 4 &ndash; Saved Flight Plan</h3>
        <p style="font-size:.78rem;color:var(--muted);margin-bottom:10px">
          The backend stored your flight plan, generated the ANEXA 1 PDF, and linked it to your logged-in account.
        </p>
        <div id="fpSavedSummary"></div>
        <div id="contactCards"></div>
        <div style="font-size:.75rem;color:var(--muted);margin-top:8px">
          Or submit online at
          <a href="https://flightplan.romatsa.ro" target="_blank" style="color:var(--blue)">flightplan.romatsa.ro</a>
          (registration required)
        </div>
      </div>

    </div><!-- .wiz-body -->
    <div class="fp-actions" id="fpActions"></div>
  </div><!-- #fpWizard -->
</div><!-- #fpOverlay -->

</body>
</html>
"""

# ──────────────────────────────────────────────────────────────────────────
# Cross-check logic (mirrors fetch_romatsa_data.py)
# ──────────────────────────────────────────────────────────────────────────

def point_in_polygon(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def feature_contains(feat: dict, lon: float, lat: float, alt_m: float) -> bool:
    """Check if a GeoJSON feature contains the given point at the given altitude."""
    geom = feat.get("geometry")
    if not geom:
        return False

    props = feat.get("properties", {})
    lo = props.get("lower_limit_m")
    up = props.get("upper_limit_m")

    # altitude check (skip if limits are null)
    if lo is not None and up is not None:
        if alt_m < lo or alt_m > up:
            return False

    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])

    if gtype == "Polygon":
        return any(point_in_polygon(lon, lat, ring) for ring in coords)
    elif gtype == "MultiPolygon":
        return any(
            point_in_polygon(lon, lat, ring)
            for poly in coords
            for ring in poly
        )
    elif gtype == "Point":
        # check within ~5 NM (roughly 0.08 degrees)
        if isinstance(coords[0], list):
            px, py = coords[0]
        else:
            px, py = coords[0], coords[1]
        return abs(px - lon) < 0.08 and abs(py - lat) < 0.08
    else:
        return False


def do_crosscheck(lon: float, lat: float, alt_m: float) -> dict:
    """Run point cross-check against the PostGIS-backed airspace backend."""
    if _crosscheck_point is None:
        raise RuntimeError("Airspace assessment backend is not available")
    return _crosscheck_point(lon, lat, alt_m)


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _json_bytes(payload, *, ensure_ascii: bool = False) -> bytes:
    return json.dumps(payload, ensure_ascii=ensure_ascii, default=_json_default).encode("utf-8")


def _parse_bbox_query(raw: str) -> tuple[float, float, float, float]:
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must contain minLon,minLat,maxLon,maxLat")
    return tuple(float(part) for part in parts)  # type: ignore[return-value]


def _decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid JWT")

    payload = parts[1]
    padded = payload + ("=" * (-len(payload) % 4))
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    data = json.loads(decoded.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid JWT payload")
    return data


AUTH_MODULE = build_auth_module(
    logged_accounts_file=LOGGED_ACCOUNTS_FILE,
    upsert_user=_upsert_app_user,
    create_token=create_session_token,
    cookie_header=session_cookie_header,
    clear_cookie_header=clear_session_cookie_header,
    session_user_from_headers=session_user_from_headers,
    token_payload_decoder=_decode_jwt_payload,
    app_user_upsert_errors=(FlightPlanRepositoryError,),
)
FLIGHT_PLANS_MODULE = build_flight_plans_module(
    pdf_dir=FLIGHT_PLAN_PDF_DIR,
    create_plan_repo=_store_flight_plan,
    list_plans_repo=_list_flight_plans_db,
    get_plan_repo=_get_flight_plan,
    cancel_plan_repo=_cancel_flight_plan_db,
    build_flight_plan=_build_flight_plan,
    build_anexa_payload=_fm.build_anexa_payload,
    generate_pdf=_generate_anexa1_pdf,
    assess_flight_area_fn=_assess_flight_area,
    build_circle_area=_build_circle_area,
    build_polygon_area=_build_polygon_area,
    twr_options=_twr_options,
)


def _list_logged_accounts() -> list[dict]:
    return AUTH_MODULE.list_logged_accounts()


def _safe_session_user(headers) -> dict | None:
    return AUTH_MODULE.current_user(headers)


def _require_session_user(headers) -> dict:
    user = _safe_session_user(headers)
    if not user or not user.get("email"):
        raise PermissionError("Login required")
    return user


def _ensure_db_user(user: dict, app_name: str):
    try:
        _upsert_app_user(user, app_name)
    except FlightPlanRepositoryError:
        # Keep the login flow usable even if the DB is currently unavailable.
        pass


def _create_flight_plan_from_payload(payload: dict, owner: dict) -> dict:
    return FLIGHT_PLANS_MODULE.create(payload, owner)


def _cancel_owned_flight_plan(public_id: str, owner: dict) -> dict:
    return FLIGHT_PLANS_MODULE.cancel(public_id, owner)


def _list_flight_plans_response(
    *,
    owner_email: str | None = None,
    include_past: bool = False,
    include_cancelled: bool = True,
) -> list[dict]:
    return FLIGHT_PLANS_MODULE.list(
        owner_email=owner_email,
        include_past=include_past,
        include_cancelled=include_cancelled,
    )


def _build_admin_overview_response() -> dict:
    accounts = _list_logged_accounts()
    flight_plans = _list_flight_plans_response(owner_email=None, include_past=True, include_cancelled=True)
    return {
        "accounts": accounts,
        "flight_plans": flight_plans,
        "airspace": AIRSPACE_ADMIN_OVERVIEW_SERVICE.overview(),
        "live_drones": _list_live_drones_for_admin(),
    }


def _list_live_drones_for_user(owner_email: str) -> list[dict]:
    return DRONE_TRACKING_REPO.list_live_drones(
        owner_email=owner_email,
        include_upcoming=False,
        only_ongoing=True,
    )


def _list_live_drones_for_admin() -> list[dict]:
    return DRONE_TRACKING_REPO.list_live_drones(
        owner_email=None,
        include_upcoming=False,
        only_ongoing=True,
    )


def _build_drone_3d_scene(drone_id: str, *, owner_email: str | None, admin_view: bool = False) -> dict:
    return DRONE_3D_SCENE_SERVICE.build_scene(
        drone_id,
        owner_email=owner_email,
        radius_km=5.0,
        admin_view=admin_view,
    )


_DEMO_FLIGHT_BLUEPRINTS = (
    {
        "location_name": "PETROSANI Demo",
        "purpose": "Automatic demo flight for 2D/3D feature discovery",
        "selected_twr": "LRAR",
        "area_kind": "circle",
        "center_lon": 24.0271,
        "center_lat": 45.444717,
        "radius_m": 180,
    },
    {
        "location_name": "BUCHAREST Airspace Demo",
        "purpose": "Automatic demo flight for crowded Bucharest airspace visualization",
        "selected_twr": "LRBS",
        "area_kind": "polygon",
        "polygon_points": [
            [26.0495, 44.4820],
            [26.1290, 44.4835],
            [26.1485, 44.5220],
            [26.1025, 44.5535],
            [26.0415, 44.5260],
        ],
    },
)


def _build_demo_payload(
    owner: dict,
    *,
    owner_name: str,
    start_local: datetime,
    end_local: datetime,
    blueprint: dict,
) -> dict:
    payload = {
        "operator_name": owner_name,
        "operator_contact": "Auto demo bootstrap",
        "contact_person": owner_name,
        "phone_landline": "-",
        "phone_mobile": "0712345678",
        "fax": "-",
        "operator_email": owner["email"],
        "uas_registration": f"YR-DEMO-{str(uuid.uuid4()).split('-')[0].upper()}",
        "uas_class_code": "C2",
        "category": "A2",
        "operation_mode": "VLOS",
        "mtom_kg": "1.4",
        "pilot_name": owner_name,
        "pilot_phone": "0712345678",
        "purpose": blueprint["purpose"],
        "location_name": blueprint["location_name"],
        "area_kind": blueprint["area_kind"],
        "max_altitude_m": 120,
        "selected_twr": blueprint["selected_twr"],
        "start_date": start_local.strftime("%Y-%m-%d"),
        "end_date": end_local.strftime("%Y-%m-%d"),
        "start_time": start_local.strftime("%H:%M"),
        "end_time": end_local.strftime("%H:%M"),
        "timezone": "Europe/Bucharest",
        "created_from_app": "auto_demo_bootstrap",
    }
    if blueprint["area_kind"] == "circle":
        payload["center_lon"] = blueprint["center_lon"]
        payload["center_lat"] = blueprint["center_lat"]
        payload["radius_m"] = blueprint["radius_m"]
    else:
        payload["polygon_points"] = [list(point) for point in blueprint["polygon_points"]]
    return payload


def _bootstrap_demo_flight_plan(owner: dict) -> dict:
    if not AUTO_DEMO_FLIGHT_PLAN_ENABLED:
        return {"created": False, "reason": "disabled"}

    active_plans = _list_flight_plans_response(
        owner_email=owner["email"],
        include_past=False,
        include_cancelled=False,
    )
    active_locations = {
        str(plan.get("location_name") or "").strip().lower()
        for plan in active_plans
        if (plan.get("runtime_state") or "").lower() in {"ongoing", "upcoming"}
    }

    now_local = datetime.now(ZoneInfo("Europe/Bucharest"))
    start_local = now_local.replace(second=0, microsecond=0) - timedelta(minutes=5)
    end_local = start_local + timedelta(minutes=60)
    owner_name = (owner.get("display_name") or owner.get("email") or "Demo Pilot").strip()
    created_plans: list[dict] = []
    errors: list[dict[str, str]] = []

    for blueprint in _DEMO_FLIGHT_BLUEPRINTS:
        if blueprint["location_name"].strip().lower() in active_locations:
            continue
        try:
            created_plans.append(
                _create_flight_plan_from_payload(
                    _build_demo_payload(
                        owner,
                        owner_name=owner_name,
                        start_local=start_local,
                        end_local=end_local,
                        blueprint=blueprint,
                    ),
                    owner,
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "location_name": blueprint["location_name"],
                    "error": str(exc),
                }
            )

    return {
        "created": bool(created_plans),
        "reason": "created" if created_plans else ("failed" if errors else "existing-demo-plans"),
        "flight_plans": created_plans,
        "errors": errors,
    }


def _run_mock_drone_loop(stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            DRONE_MOCK_TELEMETRY_SERVICE.generate_tick()
        except Exception as exc:
            print(f"  Mock drone telemetry tick failed: {exc}")
        stop_event.wait(MOCK_DRONE_INTERVAL_SECONDS)


def _start_mock_drone_loop():
    global _mock_drone_thread
    if not MOCK_DRONE_ENABLED or _mock_drone_thread is not None:
        return
    _mock_drone_thread = threading.Thread(
        target=_run_mock_drone_loop,
        args=(_mock_drone_stop,),
        name="mock-drone-telemetry",
        daemon=True,
    )
    _mock_drone_thread.start()


# ──────────────────────────────────────────────────────────────────────────
# HTTP handler
# ──────────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        path = self.path.split("?")[0]
        if path in ("/", "/favicon.ico"):
            return
        print(f"  {self.command} {path} -> {args[1] if len(args) > 1 else ''}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/favicon.ico":
            self._send(204, "image/x-icon", b"")

        elif path == "/" or path == "/index.html":
            page = HTML.replace(b"__GOOGLE_CLIENT_ID__", GOOGLE_WEB_CLIENT_ID.encode("utf-8"))
            page = page.replace(
                b"__TOWER_CONTACTS_JSON__",
                json.dumps(TOWER_CONTACTS, ensure_ascii=False).encode("utf-8"),
            )
            page = page.replace(
                b"__CESIUM_ION_TOKEN__",
                CESIUM_ION_TOKEN.encode("utf-8"),
            )
            self._send(
                200,
                "text/html; charset=utf-8",
                page,
            )

        elif path in ("/admin", "/admin/logged-accounts", "/admin/flight-plans"):
            self._send(200, "text/html; charset=utf-8", ADMIN_DASHBOARD_HTML.encode("utf-8"))

        elif path == "/api/auth/sessions":
            self._send(
                200,
                "application/json; charset=utf-8",
                _json_bytes({"accounts": _list_logged_accounts()}),
            )

        elif path == "/api/admin/overview":
            self._send(
                200,
                "application/json; charset=utf-8",
                _json_bytes(_build_admin_overview_response()),
            )

        elif path == "/api/admin/drones/live":
            self._send(
                200,
                "application/json; charset=utf-8",
                _json_bytes({"drones": _list_live_drones_for_admin()}),
            )

        elif path == "/api/auth/me":
            user = _safe_session_user(self.headers)
            self._send(
                200,
                "application/json; charset=utf-8",
                _json_bytes({"user": user}),
            )

        elif path == "/api/drones/live":
            try:
                user = _require_session_user(self.headers)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes({"drones": _list_live_drones_for_user(user["email"])}),
                )
            except PermissionError as exc:
                self._send(401, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path.startswith("/api/drones/") and path.endswith("/scene-3d"):
            drone_id = path[len("/api/drones/"):-len("/scene-3d")].strip("/")
            try:
                user = _require_session_user(self.headers)
                scene = _build_drone_3d_scene(drone_id, owner_email=user["email"], admin_view=False)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(scene, ensure_ascii=False),
                )
            except PermissionError as exc:
                self._send(401, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except LookupError as exc:
                self._send(404, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except Exception as exc:
                self._send(500, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path.startswith("/api/admin/drones/") and path.endswith("/scene-3d"):
            drone_id = path[len("/api/admin/drones/"):-len("/scene-3d")].strip("/")
            try:
                scene = _build_drone_3d_scene(drone_id, owner_email=None, admin_view=True)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(scene, ensure_ascii=False),
                )
            except LookupError as exc:
                self._send(404, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except Exception as exc:
                self._send(500, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path == "/healthz":
            self._send(200, "application/json; charset=utf-8", b'{"ok": true}')

        elif path == "/airspace/zones":
            try:
                bbox = _parse_bbox_query(qs.get("bbox", [""])[0])
                categories = _normalize_airspace_categories(qs.get("categories", [None])[0])
                result = AIRSPACE_QUERY_SERVICE.get_zones_in_bbox(bbox, categories=categories)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(result, ensure_ascii=False),
                )
            except Exception as exc:
                self._send(400, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path == "/airspace/zones/near":
            try:
                lat = float(qs.get("lat", [0])[0])
                lon = float(qs.get("lon", [0])[0])
                radius_km = float(qs.get("radius_km", [10])[0])
                categories = _normalize_airspace_categories(qs.get("categories", [None])[0])
                result = AIRSPACE_QUERY_SERVICE.get_zones_near(
                    lat=lat,
                    lon=lon,
                    radius_km=radius_km,
                    categories=categories,
                )
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(result, ensure_ascii=False),
                )
            except Exception as exc:
                self._send(400, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path == "/airspace/check-point":
            try:
                if _check_point is None:
                    raise RuntimeError("Airspace backend is not available")
                lon = float(qs.get("lon", [0])[0])
                lat = float(qs.get("lat", [0])[0])
                alt = float(qs.get("alt_m", [120])[0])
                result = _check_point(lon, lat, alt)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(result, ensure_ascii=False),
                )
            except Exception as exc:
                self._send(503, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path == "/api/flight-plans/options":
            self._send(
                200,
                "application/json; charset=utf-8",
                _json_bytes({"twr_options": FLIGHT_PLANS_MODULE.twr_options()}, ensure_ascii=False),
            )

        elif path == "/api/flight-plans":
            try:
                scope = (qs.get("scope", ["mine"])[0] or "mine").lower()
                include_past = (qs.get("include_past", ["0"])[0] or "0") in ("1", "true", "yes")
                include_cancelled = (qs.get("include_cancelled", ["1"])[0] or "1") in ("1", "true", "yes")
                owner_email = None
                if scope != "all":
                    owner_email = _require_session_user(self.headers)["email"]
                plans = _list_flight_plans_response(
                    owner_email=owner_email,
                    include_past=include_past,
                    include_cancelled=include_cancelled,
                )
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes({"flight_plans": plans}, ensure_ascii=False),
                )
            except PermissionError as exc:
                self._send(401, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except Exception as exc:
                self._send(500, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path.startswith("/api/flight-plans/") and path.endswith("/pdf"):
            public_id = path[len("/api/flight-plans/"):-len("/pdf")].strip("/")
            try:
                plan = FLIGHT_PLANS_MODULE.get(public_id)
                if not plan:
                    self._send(404, "application/json; charset=utf-8", b'{"error":"Flight plan not found"}')
                    return
                pdf_path = SCRIPT_DIR.parent / plan["pdf_rel_path"]
                if not pdf_path.exists():
                    raise FileNotFoundError(f"Generated PDF missing for {public_id}")
                pdf_bytes = pdf_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(pdf_bytes)))
                self.send_header("Content-Disposition", f'attachment; filename="{public_id}.pdf"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(pdf_bytes)
            except Exception as exc:
                self._send(500, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path == "/api/crosscheck":
            try:
                lon = float(qs.get("lon", [0])[0])
                lat = float(qs.get("lat", [0])[0])
                alt = float(qs.get("alt", [120])[0])
                result = do_crosscheck(lon, lat, alt)
                self._send(200, "application/json; charset=utf-8", _json_bytes(result))
            except Exception as exc:
                self._send(500, "application/json", _json_bytes({"error": str(exc)}))

        elif path == "/api/area_check":
            try:
                lon = float(qs.get("lon", [0])[0])
                lat = float(qs.get("lat", [0])[0])
                radius = float(qs.get("radius", [200])[0])
                alt = float(qs.get("alt", [120])[0])
                if _area_check is None:
                    raise RuntimeError("flight_plan_manager not loaded")
                result = _area_check(lon, lat, radius, alt)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(result, ensure_ascii=False),
                )
            except Exception as exc:
                self._send(500, "application/json", _json_bytes({"error": str(exc)}))

        elif path.startswith("/api/"):
            layer_key = path[len("/api/"):].rstrip("/")
            fpath = LAYER_FILES.get(layer_key)
            if fpath and fpath.exists():
                self._send(200, "application/json; charset=utf-8", fpath.read_bytes())
            else:
                self._send(
                    404,
                    "application/json",
                    _json_bytes({"error": f"Layer '{layer_key}' not found"}),
                )

        else:
            self._send(404, "text/plain", b"Not found")

    def _send(self, code: int, ctype: str, body: bytes, extra_headers: dict[str, str] | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"

        if path == "/api/auth/google-session":
            try:
                payload = json.loads(raw)
                source_ip = self.client_address[0] if self.client_address else ""
                result = AUTH_MODULE.register_google_session(payload, source_ip)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes({"ok": True, "user": result["user"]}),
                    extra_headers={"Set-Cookie": result["set_cookie"]},
                )
            except Exception as exc:
                self._send(400, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path == "/api/auth/logout":
            self._send(
                200,
                "application/json; charset=utf-8",
                b'{"ok": true}',
                extra_headers={"Set-Cookie": AUTH_MODULE.clear_cookie_header()},
            )

        elif path == "/api/demo/bootstrap":
            try:
                owner = _require_session_user(self.headers)
                result = _bootstrap_demo_flight_plan(owner)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(result, ensure_ascii=False),
                )
            except PermissionError as exc:
                self._send(401, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except _flight_plan_error as exc:
                self._send(400, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except FlightPlanRepositoryError as exc:
                self._send(500, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except Exception as exc:
                self._send(500, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path == "/api/flight-plans/assess":
            try:
                data = json.loads(raw)
                result = FLIGHT_PLANS_MODULE.assess(data)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(result, ensure_ascii=False),
                )
            except Exception as exc:
                self._send(400, "application/json", _json_bytes({"error": str(exc)}))

        elif path == "/airspace/check-route":
            try:
                if _check_route is None:
                    raise RuntimeError("Airspace backend is not available")
                data = json.loads(raw)
                path_points = data.get("path") or []
                result = _check_route(path_points)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes(result, ensure_ascii=False),
                )
            except Exception as exc:
                self._send(400, "application/json", _json_bytes({"error": str(exc)}))

        elif path.startswith("/api/flight-plans/") and path.endswith("/cancel"):
            public_id = path[len("/api/flight-plans/"):-len("/cancel")].strip("/")
            try:
                owner = _require_session_user(self.headers)
                cancelled = _cancel_owned_flight_plan(public_id, owner)
                self._send(
                    200,
                    "application/json; charset=utf-8",
                    _json_bytes({"ok": True, "flight_plan": cancelled}, ensure_ascii=False),
                )
            except PermissionError as exc:
                self._send(401, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except ValueError as exc:
                self._send(400, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except Exception as exc:
                self._send(500, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))

        elif path == "/api/flight-plans":
            try:
                owner = _require_session_user(self.headers)
                payload = json.loads(raw)
                plan = _create_flight_plan_from_payload(payload, owner)
                self._send(
                    201,
                    "application/json; charset=utf-8",
                    _json_bytes({"flight_plan": plan}, ensure_ascii=False),
                )
            except PermissionError as exc:
                self._send(401, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except _flight_plan_error as exc:
                self._send(400, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except FlightPlanRepositoryError as exc:
                self._send(500, "application/json; charset=utf-8", _json_bytes({"error": str(exc)}))
            except Exception as exc:
                self._send(500, "application/json", _json_bytes({"error": str(exc)}))

        elif path == "/api/generate_pdf":
            try:
                data = json.loads(raw)
                if _generate_anexa1_pdf is None:
                    raise RuntimeError("flight_plan_manager not loaded")
                legacy_email = " ".join(str(data.get("operator_email") or data.get("email") or "legacy@example.com").strip().split())
                legacy_name = " ".join(str(data.get("operator_name") or data.get("operator") or "Legacy User").strip().split())
                owner = {
                    "email": legacy_email,
                    "display_name": legacy_name,
                    "google_user_id": "",
                }
                legacy_payload = {
                    "operator_name": data.get("operator") or "",
                    "operator_contact": data.get("date_contact") or "",
                    "contact_person": data.get("pers_contact") or data.get("operator") or "",
                    "phone_landline": data.get("telefon_fix") or "",
                    "phone_mobile": data.get("mobil") or "",
                    "fax": data.get("fax") or "",
                    "operator_email": data.get("email") or "",
                    "uas_registration": data.get("inmatriculare") or "",
                    "uas_class_code": data.get("clasa") or "C2",
                    "category": data.get("categorie") or "A2",
                    "operation_mode": data.get("mod_operare") or "VLOS",
                    "mtom_kg": data.get("greutate") or "1",
                    "pilot_name": data.get("pilot_name") or "",
                    "pilot_phone": data.get("pilot_phone") or "",
                    "purpose": data.get("scop_zbor") or "",
                    "max_altitude_m": data.get("alt_max_m") or "120",
                    "start_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "end_date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "start_time": data.get("ora_start") or "08:00",
                    "end_time": data.get("ora_end") or "09:00",
                    "location_name": data.get("localitatea") or "",
                    "selected_twr": data.get("twr") or "LRBV",
                    "timezone": "Europe/Bucharest",
                    "created_from_app": "legacy_generate_pdf",
                    "area_kind": "polygon" if data.get("polygon") else "circle",
                    "center_lon": data.get("center_lon"),
                    "center_lat": data.get("center_lat"),
                    "radius_m": data.get("radius_m"),
                    "polygon_points": data.get("polygon"),
                }
                plan = _build_flight_plan(legacy_payload, owner)
                pdf_path = Path("/tmp/anexa1_filled.pdf")
                _generate_anexa1_pdf(plan, pdf_path)
                pdf_bytes = pdf_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(pdf_bytes)))
                self.send_header("Content-Disposition", 'attachment; filename="ANEXA1_filled.pdf"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(pdf_bytes)
            except Exception as exc:
                self._send(500, "application/json", _json_bytes({"error": str(exc)}))
        else:
            self._send(404, "text/plain", b"Not found")


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ROMATSA Multi-Layer Map Server")
    parser.add_argument("--host", default=os.environ.get("DRONE_BIND_HOST", "0.0.0.0"), help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5174")), help="Port (default: 5174)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    missing = [k for k, p in LAYER_FILES.items() if not p.exists()]
    if missing:
        print(f"  Warning: missing layers: {', '.join(missing)}")
        print("  Run:  python3 scripts/fetch_romatsa_data.py\n")
    if not _fm.ANEXA1_TEMPLATE_PATH.exists():
        print(f"  Warning: missing ANEXA 1 template: {_fm.ANEXA1_TEMPLATE_PATH}")

    url = f"http://localhost:{args.port}"
    found = [k for k, p in LAYER_FILES.items() if p.exists()]
    print(f"\n  ROMATSA Mirror  ->  {url}")
    print(f"  Layers found: {len(found)}/{len(LAYER_FILES)}: {', '.join(found)}")
    print(f"  Admin dashboard: {url}/admin")
    print(f"  Logged accounts: {url}/admin/logged-accounts")
    print(f"  Flight plans: {url}/admin/flight-plans")
    if MOCK_DRONE_ENABLED:
        print(f"  Mock drone telemetry: enabled ({MOCK_DRONE_INTERVAL_SECONDS:.0f}s interval)")
    print("  Press Ctrl-C to stop.\n")

    server = HTTPServer((args.host, args.port), Handler)
    _start_mock_drone_loop()

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
    finally:
        _mock_drone_stop.set()


if __name__ == "__main__":
    main()
