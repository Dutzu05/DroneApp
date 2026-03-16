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
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modules.auth.module import build_auth_module
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
    width: 280px; min-width: 280px; background: var(--bg2); border-right: 1px solid var(--border);
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

  .sb-section { padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .sb-section h2 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 8px; }

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
    display: none; position: absolute; top: 0; left: 280px; right: 0; bottom: 0;
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
  .draw-hint {
    background: rgba(233,69,96,.12); border: 1px dashed var(--accent);
    border-radius:8px; padding:10px 12px; font-size:.8rem;
    color:var(--text); margin-bottom:10px; text-align:center;
  }
  .draw-hint .hint-icon { font-size:1.4rem; display:block; margin-bottom:4px; }
  #fpCircleInfo { font-size:.76rem; color:var(--muted); margin-top:6px; }
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

  <div id="stats">Loading layers...</div>
</div>

<div id="map"></div>

<div id="crossPanel">
  <button class="close-btn" onclick="closeCross()">&times;</button>
  <h3 id="crossTitle">Cross-check</h3>
  <div id="crossResults"></div>
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
const TOWER_DATA = __TOWER_CONTACTS_JSON__;
window._towerData = TOWER_DATA;

let currentMode = 'drone';
let mapLayers = {};
let rawData   = {};
let allFeatureIndex = [];
let layersLoaded = false;
let authenticatedUser = null;
const CENTER_BLOCKING_LAYER_KEYS = ['ctr', 'uas_zones', 'notam', 'tma'];

function setMyPlansContent(html) {
  document.getElementById('myPlansList').innerHTML = html;
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
  loadMyFlightPlans(false);
  prefillFlightPlanForm();
}

function showAuthGate(message) {
  authenticatedUser = null;
  updateAuthenticatedUser();
  setAuthError(message || '');
  document.getElementById('authGate').style.display = 'flex';
  renderMyFlightPlans([]);
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

// ========================================================================
// LAYER LOADING
// ========================================================================
async function loadAllLayers() {
  const keys = Object.keys(LAYERS_CFG);
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

  buildLayerToggles();
  applyMode();
  applyAltFilter();
  updateStats();
}

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
    cb.checked = isLayerVisible(key);
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
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _json_bytes(payload, *, ensure_ascii: bool = False) -> bytes:
    return json.dumps(payload, ensure_ascii=ensure_ascii, default=_json_default).encode("utf-8")


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
            self._send(
                200,
                "text/html; charset=utf-8",
                page,
            )

        elif path == "/admin/logged-accounts":
            self._send(200, "text/html; charset=utf-8", ADMIN_HTML.encode("utf-8"))

        elif path == "/admin/flight-plans":
            self._send(200, "text/html; charset=utf-8", FLIGHT_PLAN_ADMIN_HTML.encode("utf-8"))

        elif path == "/api/auth/sessions":
            self._send(
                200,
                "application/json; charset=utf-8",
                _json_bytes({"accounts": _list_logged_accounts()}),
            )

        elif path == "/api/auth/me":
            user = _safe_session_user(self.headers)
            self._send(
                200,
                "application/json; charset=utf-8",
                _json_bytes({"user": user}),
            )

        elif path == "/healthz":
            self._send(200, "application/json; charset=utf-8", b'{"ok": true}')

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
    print(f"  Logged accounts: {url}/admin/logged-accounts")
    print(f"  Flight plans: {url}/admin/flight-plans")
    print("  Press Ctrl-C to stop.\n")

    server = HTTPServer((args.host, args.port), Handler)

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
