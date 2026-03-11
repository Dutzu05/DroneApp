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
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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
// BOOT
// ========================================================================
loadAllLayers();
</script>
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
