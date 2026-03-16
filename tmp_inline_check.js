
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
      ? '<button class="mini-btn mini-btn-danger" type="button" onclick="cancelFlightPlan(\'' + (plan.public_id || '') + '\')">Cancel</button>'
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

function findBlockingCircleCenterHits(lon, lat, altM) {
  var hits = [];
  CENTER_BLOCKING_LAYER_KEYS.forEach(function(layerKey) {
    var data = rawData[layerKey];
    if (!data || !data.features) return;
    data.features.forEach(function(feature) {
      if (featureContainsPointJs(feature, lon, lat, altM)) {
        hits.push(formatBlockingHit(feature, layerKey));
      }
    });
  });
  return hits;
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

function setCircleCenter(lat, lon, interactive, syncInputs) {
  var altM = parseFloat(document.getElementById('fpAlt').value) || 120;
  var hits = findBlockingCircleCenterHits(lon, lat, altM);
  if (hits.length) {
    return rejectCircleCenter(hits, interactive);
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

function syncCircleFromInputs() {
  var lat = parseFloat(document.getElementById('fpLat').value);
  var lon = parseFloat(document.getElementById('fpLon').value);
  if (Number.isFinite(lat) && Number.isFinite(lon)) {
    setCircleCenter(lat, lon, false, false);
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

function onMapClickFP(e) {
  if (!fpAreaPickMode) return;
  if (fpAreaPickMode === 'circle') {
    if (!setCircleCenter(e.latlng.lat, e.latlng.lng, true, true)) {
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

  syncCircleFromInputs();
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
    var payload = getCurrentAreaPayload();
    payload.max_altitude_m = parseFloat(document.getElementById('fpAlt').value) || 120;
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

function collectFlightPlanPayload() {
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
  return payload;
}

async function saveFlightPlan() {
  try {
    var payload = collectFlightPlanPayload();
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
