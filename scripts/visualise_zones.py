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
import io
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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
    _fill_anexa1   = _fm.fill_anexa1
else:
    TOWER_CONTACTS = {}
    _area_check    = None
    _fill_anexa1   = None

SCRIPT_DIR = Path(__file__).resolve().parent
ASSET_DIR  = SCRIPT_DIR.parent / "mobile_app" / "assets"

LAYER_FILES = {
    "uas_zones":    ASSET_DIR / "restriction_zones.geojson",
    "notam":        ASSET_DIR / "notam_zones.geojson",
    "notam_all":    ASSET_DIR / "notam_all.geojson",
    "ctr":          ASSET_DIR / "airspace_ctr.geojson",
    "tma":          ASSET_DIR / "airspace_tma.geojson",
    "airports":     ASSET_DIR / "airports.geojson",
    "lower_routes": ASSET_DIR / "lower_routes.geojson",
}

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

  /* Sidebar */
  #sidebar {
    width: 280px; min-width: 280px; background: var(--bg2); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; overflow-y: auto; z-index: 1001;
  }
  .sb-header {
    padding: 14px 16px; border-bottom: 1px solid var(--border); display: flex;
    align-items: center; gap: 8px;
  }
  .sb-header h1 { font-size: 0.95rem; font-weight: 700; color: var(--accent); }
  .sb-header .flag { font-size: 1.2rem; }

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
  .btn-primary:hover, .btn-secondary:hover, .btn-success:hover { opacity:.85; }
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
  .draw-hint {
    background: rgba(233,69,96,.12); border: 1px dashed var(--accent);
    border-radius:8px; padding:10px 12px; font-size:.8rem;
    color:var(--text); margin-bottom:10px; text-align:center;
  }
  .draw-hint .hint-icon { font-size:1.4rem; display:block; margin-bottom:4px; }
  #fpCircleInfo { font-size:.76rem; color:var(--muted); margin-top:6px; }
</style>
</head>
<body>

<div id="sidebar">
  <div class="sb-header">
    <span class="flag">&#127479;&#127476;</span>
    <h1>ROMATSA Mirror</h1>
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

let currentMode = 'drone';
let mapLayers = {};
let rawData   = {};
let allFeatureIndex = [];

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
    rawData[r.key] = r.data;
    buildMapLayer(r.key, r.data);
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
let fpMode = false;   // true = waiting for user to click circle centre
let fpCircle = null;  // Leaflet circle on map
let fpCentre = null;  // { lat, lon }
let fpAreaResult = null; // last area_check response

function launchFlightPlan() {
  document.getElementById('fpOverlay').style.display = 'block';
  showStep(1);
}

function closeFlightPlan() {
  document.getElementById('fpOverlay').style.display = 'none';
  fpMode = false;
  clearFpCircle();
}

function clearFpCircle() {
  if (fpCircle) { map.removeLayer(fpCircle); fpCircle = null; }
  fpCentre = null;
  document.getElementById('fpCircleInfo').textContent = '';
}

function showStep(n) {
  [1,2,3,4].forEach(function(i) {
    document.getElementById('wizStep' + i).classList.toggle('active', i === n);
    var dot = document.getElementById('stepDot' + i);
    if (dot) {
      dot.className = 'step-dot' + (i < n ? ' done' : i === n ? ' active' : '');
    }
  });
}

// -- Step 1: Place circle --------------------------------------------------
function enterDrawMode() {
  fpMode = true;
  document.getElementById('fpDrawHint').style.display = 'block';
  document.getElementById('fpCircleInfo').textContent = 'Click anywhere on the map to place the centre...';
  // Make overlay transparent to clicks on the map side
  document.getElementById('fpOverlay').style.pointerEvents = 'none';
  document.getElementById('fpWizard').style.pointerEvents = 'all';
}

function onMapClickFP(e) {
  if (!fpMode) return;
  fpMode = false;
  fpCentre = { lat: e.latlng.lat, lon: e.latlng.lng };
  document.getElementById('fpOverlay').style.pointerEvents = 'all';
  document.getElementById('fpDrawHint').style.display = 'none';
  document.getElementById('fpLat').value = fpCentre.lat.toFixed(6);
  document.getElementById('fpLon').value = fpCentre.lon.toFixed(6);
  updateFpCircle();
  document.getElementById('fpCircleInfo').textContent =
    'Centre: ' + fpCentre.lat.toFixed(5) + ', ' + fpCentre.lon.toFixed(5);
}

function updateFpCircle() {
  if (!fpCentre) return;
  var r = parseFloat(document.getElementById('fpRadius').value) || 200;
  if (fpCircle) map.removeLayer(fpCircle);
  fpCircle = L.circle([fpCentre.lat, fpCentre.lon], {
    radius: r,
    color: '#e94560', fillColor: '#e94560',
    weight: 2, fillOpacity: 0.18, dashArray: '6 4',
    interactive: false,
  }).addTo(map);
  map.panTo([fpCentre.lat, fpCentre.lon]);
}

map.on('click', function(e) {
  onMapClickFP(e);
});

document.getElementById('fpRadius').addEventListener('input', updateFpCircle);

function checkFpArea() {
  if (!fpCentre) {
    alert('Please place the circle centre on the map first.');
    return;
  }
  var radius = parseFloat(document.getElementById('fpRadius').value) || 200;
  var alt    = parseFloat(document.getElementById('fpAlt').value)    || 120;
  document.getElementById('fpCheckBtn').textContent = 'Checking...';
  fetch('/api/area_check?lon=' + fpCentre.lon.toFixed(6) +
        '&lat='    + fpCentre.lat.toFixed(6) +
        '&radius=' + radius + '&alt=' + alt)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      fpAreaResult = data;
      showRiskResults(data);
      document.getElementById('fpCheckBtn').textContent = 'Check Area';
      showStep(2);
    })
    .catch(function(err) {
      alert('Area check failed: ' + err);
      document.getElementById('fpCheckBtn').textContent = 'Check Area';
    });
}

// -- Step 2: Risk results --------------------------------------------------
function showRiskResults(data) {
  var risk = data.risk_level || 'LOW';
  document.getElementById('riskBadge').textContent = risk;
  document.getElementById('riskBadge').className = 'risk-badge risk-' + risk;
  document.getElementById('riskSummary').textContent = data.summary || '';

  var html = '';
  function addHits(hits, label, color) {
    if (!hits || !hits.length) return;
    hits.forEach(function(h) {
      var name = h.zone_id || h.notam_id || h.name || h.arsp_name || label;
      var alt = (h.lower_limit_m != null && h.upper_limit_m != null)
        ? ' (' + Math.round(h.lower_limit_m) + '-' + Math.round(h.upper_limit_m) + ' m)'
        : '';
      html += '<div class="hit-item"><span class="hit-layer" style="background:' + color + '">' +
        label + '</span>' + name + '<span style="color:var(--muted)">' + alt + '</span></div>';
    });
  }
  addHits(data.ctr_hits,   'CTR',      '#58a6ff');
  addHits(data.uas_hits,   'UAS Zone', '#e94560');
  addHits(data.notam_hits, 'NOTAM',    '#ff9800');
  addHits(data.tma_hits,   'TMA',      '#3fb950');

  document.getElementById('riskHits').innerHTML = html || '<div style="color:var(--muted);font-size:.8rem">No conflicting zones found.</div>';

  // Auto-fill TWR field from first CTR hit
  if (data.tower_contacts && data.tower_contacts.length > 0) {
    var tc = data.tower_contacts[0];
    if (tc.icao) document.getElementById('fpTwr').value = tc.icao;
  }
}

// -- Step 3: Form prefill & generate PDF ----------------------------------
function generatePdf() {
  var centre = fpCentre || { lat: 0, lon: 0 };
  var radius = parseFloat(document.getElementById('fpRadius').value) || 200;

  var payload = {
    operator:      document.getElementById('fp_operator').value,
    date_contact:  document.getElementById('fp_address').value,
    email:         document.getElementById('fp_email').value,
    mobil:         document.getElementById('fp_mobil').value,
    inmatriculare: document.getElementById('fp_reg').value,
    greutate:      document.getElementById('fp_weight').value,
    clasa:         document.getElementById('fp_class').value,
    categorie:     document.getElementById('fp_cat').value,
    mod_operare:   document.getElementById('fp_mode').value,
    twr:           document.getElementById('fpTwr').value,
    pilot_name:    document.getElementById('fp_pilot').value,
    pilot_phone:   document.getElementById('fp_pphone').value,
    scop_zbor:     document.getElementById('fp_purpose').value,
    alt_max_m:     document.getElementById('fpAlt').value,
    data_start:    document.getElementById('fp_date1').value,
    data_end:      document.getElementById('fp_date2').value,
    ora_start:     document.getElementById('fp_time1').value,
    ora_end:       document.getElementById('fp_time2').value,
    localitatea:   document.getElementById('fp_loc').value,
    center_lon:    centre.lon,
    center_lat:    centre.lat,
    radius_m:      radius,
  };

  document.getElementById('fpGenBtn').textContent = 'Generating...';

  fetch('/api/generate_pdf', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  .then(function(r) {
    if (!r.ok) return r.json().then(function(e) { throw new Error(e.error || 'Server error'); });
    return r.blob();
  })
  .then(function(blob) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = 'ANEXA1_filled.pdf';
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    document.getElementById('fpGenBtn').textContent = 'Download PDF';
    showStep(4);
    showContactInfo();
  })
  .catch(function(err) {
    alert('PDF generation failed: ' + err.message);
    document.getElementById('fpGenBtn').textContent = 'Download PDF';
  });
}

// -- Step 4: Contact info --------------------------------------------------
function showContactInfo() {
  var contacts = fpAreaResult && fpAreaResult.tower_contacts ? fpAreaResult.tower_contacts : [];
  var selectedTwr = document.getElementById('fpTwr').value;
  var html = '';

  // Show all hit contacts, plus the selected TWR if not in hits
  var shown = new Set();
  contacts.forEach(function(c) {
    if (!c.icao) return;
    shown.add(c.icao);
    html += buildContactCard(c);
  });

  // If user manually selected a different TWR, also show that
  if (selectedTwr && !shown.has(selectedTwr) && window._towerData && window._towerData[selectedTwr]) {
    html += buildContactCard(Object.assign({icao: selectedTwr}, window._towerData[selectedTwr]));
  }

  if (!html) {
    html = '<div style="color:var(--muted);font-size:.8rem">No CTR detected. ' +
      'Submit via <a href="https://flightplan.romatsa.ro" target="_blank" style="color:var(--blue)">flightplan.romatsa.ro</a></div>';
  }
  document.getElementById('contactCards').innerHTML = html;
}

function buildContactCard(c) {
  var phones = (c.phone || []).join(', ');
  var emailLink = c.email
    ? '<a href="' + buildMailto(c) + '" style="color:var(--blue)">' + c.email + '</a>'
    : '-';
  var note = c.note ? '<div style="color:var(--orange);font-size:.7rem;margin-top:4px">' + c.note + '</div>' : '';
  return '<div class="contact-card">' +
    '<div class="cc-name">' + (c.icao || '') + ' - ' + (c.name || '') + '</div>' +
    '<div class="cc-row"><span class="cc-lbl">Phone</span><span>' + phones + '</span></div>' +
    '<div class="cc-row"><span class="cc-lbl">Email</span><span>' + emailLink + '</span></div>' +
    note +
    '</div>';
}

function buildMailto(c) {
  var subject = encodeURIComponent('Notificare operare UAS in CTR ' + (c.icao || ''));
  var nl = '\\n';
  var body = encodeURIComponent(
    'Buna ziua,' + nl + nl + 'Va transmit atasat Anexa 1 pentru operarea UAS in CTR ' + (c.icao || '') + '.' + nl + nl
    + 'Locatia: ' + (document.getElementById('fp_loc').value || 'N/A') + nl
    + 'Data: ' + (document.getElementById('fp_date1').value || 'N/A') + nl
    + 'Altitudine maxima: ' + (document.getElementById('fpAlt').value || 'N/A') + ' m' + nl + nl
    + 'Cu stima,' + nl + (document.getElementById('fp_operator').value || '')
  );
  return 'mailto:' + (c.email || '') + '?subject=' + subject + '&body=' + body;
}

function openRomatsaPortal() {
  window.open('https://flightplan.romatsa.ro', '_blank');
}

// ========================================================================
// BOOT
// ========================================================================
loadAllLayers();
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
        <div class="draw-hint" id="fpDrawHint" style="display:none">
          <span class="hint-icon">&#128205;</span>
          Click on the map to place the circle centre
        </div>
        <div class="fp-2col">
          <div class="fp-row">
            <label>Latitude</label>
            <input id="fpLat" type="number" step="0.000001" placeholder="44.4268"/>
          </div>
          <div class="fp-row">
            <label>Longitude</label>
            <input id="fpLon" type="number" step="0.000001" placeholder="26.1025"/>
          </div>
        </div>
        <div class="fp-row">
          <label>Radius (metres)</label>
          <input id="fpRadius" type="number" min="50" max="5000" value="200"/>
        </div>
        <div class="fp-row">
          <label>Max Altitude AGL (m)</label>
          <input id="fpAlt" type="number" min="0" max="120" value="120"/>
        </div>
        <div id="fpCircleInfo" style="font-size:.75rem;color:var(--muted);margin-top:6px"></div>
      </div>

      <!-- Step 2: Risk results -->
      <div class="wiz-step" id="wizStep2">
        <h3>Step 2 &ndash; Airspace Risk</h3>
        <div id="riskBadge" class="risk-badge risk-LOW">LOW</div>
        <div id="riskSummary" style="font-size:.8rem;margin-bottom:8px;color:var(--muted)"></div>
        <div id="riskHits" class="hit-list"></div>
      </div>

      <!-- Step 3: Flight plan form -->
      <div class="wiz-step" id="wizStep3">
        <h3>Step 3 &ndash; Flight Plan Details</h3>
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
        <div class="fp-row"><label>Operator Name</label><input id="fp_operator" type="text"/></div>
        <div class="fp-row"><label>Address / Contact</label><textarea id="fp_address" rows="2"></textarea></div>
        <div class="fp-2col">
          <div class="fp-row"><label>Email</label><input id="fp_email" type="email"/></div>
          <div class="fp-row"><label>Mobile</label><input id="fp_mobil" type="tel"/></div>
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
              <option value="C2" selected>C2 900g-4kg</option>
              <option value="C3">C3 &lt;25kg</option>
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
          <div class="fp-row"><label>Start Date (DD.MM.YYYY)</label><input id="fp_date1" type="text" placeholder="01.06.2025"/></div>
          <div class="fp-row"><label>End Date</label><input id="fp_date2" type="text" placeholder="01.06.2025"/></div>
        </div>
        <div class="fp-2col">
          <div class="fp-row"><label>Start Time (UTC)</label><input id="fp_time1" type="text" placeholder="08:00"/></div>
          <div class="fp-row"><label>End Time (UTC)</label><input id="fp_time2" type="text" placeholder="10:00"/></div>
        </div>
      </div>

      <!-- Step 4: Contact & submit -->
      <div class="wiz-step" id="wizStep4">
        <h3>Step 4 &ndash; Send to ROMATSA</h3>
        <p style="font-size:.78rem;color:var(--muted);margin-bottom:10px">
          Your ANEXA 1 PDF has been downloaded. Send it to the responsible TWR unit:
        </p>
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
    """Run cross-check across all loaded GeoJSON files."""
    results = {}
    for key, path in LAYER_FILES.items():
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        hits = []
        for feat in data.get("features", []):
            if feature_contains(feat, lon, lat, alt_m):
                hits.append(feat["properties"])
        if hits:
            results[key] = hits
    return results


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
        qs   = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._send(200, "text/html; charset=utf-8", HTML)

        elif path == "/api/crosscheck":
            try:
                lon = float(qs.get("lon", [0])[0])
                lat = float(qs.get("lat", [0])[0])
                alt = float(qs.get("alt", [120])[0])
                result = do_crosscheck(lon, lat, alt)
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(result).encode())
            except Exception as exc:
                self._send(500, "application/json",
                           json.dumps({"error": str(exc)}).encode())

        elif path == "/api/area_check":
            try:
                lon    = float(qs.get("lon",    [0])[0])
                lat    = float(qs.get("lat",    [0])[0])
                radius = float(qs.get("radius", [200])[0])
                alt    = float(qs.get("alt",    [120])[0])
                if _area_check is None:
                    raise RuntimeError("flight_plan_manager not loaded")
                result = _area_check(lon, lat, radius, alt)
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(result, ensure_ascii=False).encode())
            except Exception as exc:
                self._send(500, "application/json",
                           json.dumps({"error": str(exc)}).encode())

        elif path.startswith("/api/"):
            layer_key = path[len("/api/"):].rstrip("/")
            fpath = LAYER_FILES.get(layer_key)
            if fpath and fpath.exists():
                self._send(200, "application/json; charset=utf-8",
                           fpath.read_bytes())
            else:
                self._send(404, "application/json",
                           json.dumps({"error": f"Layer '{layer_key}' not found"}).encode())

        else:
            self._send(404, "text/plain", b"Not found")

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
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
        path   = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw    = self.rfile.read(length) if length else b"{}"

        if path == "/api/generate_pdf":
            try:
                data = json.loads(raw)
                if _fill_anexa1 is None:
                    raise RuntimeError("flight_plan_manager not loaded")
                template = Path("/home/vlad/Downloads/ANEXA1.pdf")
                if not template.exists():
                    raise FileNotFoundError(f"ANEXA1.pdf not found at {template}")
                out_path = Path("/tmp/anexa1_filled.pdf")
                _fill_anexa1(template, out_path, data)
                pdf_bytes = out_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type",        "application/pdf")
                self.send_header("Content-Length",       str(len(pdf_bytes)))
                self.send_header("Content-Disposition", 'attachment; filename="ANEXA1_filled.pdf"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(pdf_bytes)
            except Exception as exc:
                self._send(500, "application/json",
                           json.dumps({"error": str(exc)}).encode())
        else:
            self._send(404, "text/plain", b"Not found")


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ROMATSA Multi-Layer Map Server")
    parser.add_argument("--port", type=int, default=5174, help="Port (default: 5174)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    missing = [k for k, p in LAYER_FILES.items() if not p.exists()]
    if missing:
        print(f"  Warning: missing layers: {', '.join(missing)}")
        print("  Run:  python3 scripts/fetch_romatsa_data.py\n")

    url = f"http://localhost:{args.port}"
    found = [k for k, p in LAYER_FILES.items() if p.exists()]
    print(f"\n  ROMATSA Mirror  ->  {url}")
    print(f"  Layers found: {len(found)}/{len(LAYER_FILES)}: {', '.join(found)}")
    print("  Press Ctrl-C to stop.\n")

    server = HTTPServer(("", args.port), Handler)

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
