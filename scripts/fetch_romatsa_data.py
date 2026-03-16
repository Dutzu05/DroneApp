    #!/usr/bin/env python3
"""
fetch_romatsa_data.py
─────────────────────
Unified data pipeline for ALL ROMATSA aeronautical data layers.

Downloads, normalises, and writes enriched GeoJSON files that the Flutter
app can consume directly.  Covers **both** the permanent navigational
infrastructure and the live temporal hazards.

Data sources (GeoServer WFS)
────────────────────────────
  Layer                              Description
  ──────────────────────────────────────────────────────────────────────
  zone_restrictionate_uav            Permanent UAS restriction zones
  restrictii_notam_pt_uav            Temporary UAS restrictions (NOTAM)
  valid_notams_LRBB                  ALL active NOTAMs (incl. non-UAS)
  CTR_LRBB                          Control Zones (manned aviation)
  tma_boundary                       Terminal Manoeuvring Areas
  airport_current_WGS84              Airport locations
  route_segments_lower_next          Lower airway segments (VFR/IFR)
  ──────────────────────────────────────────────────────────────────────

Output (mobile_app/assets/)
───────────────────────────
  restriction_zones.geojson          UAS permanent zones (enriched)
  notam_zones.geojson                Active NOTAM polygons (enriched)
  airspace.geojson                   CTR + TMA boundaries (enriched)
  airports.geojson                   Airport points
  lower_routes.geojson               Lower airway route segments

Usage
─────
  python3 scripts/fetch_romatsa_data.py                # fetch all layers
  python3 scripts/fetch_romatsa_data.py --watch 300    # poll every 5 min
  python3 scripts/fetch_romatsa_data.py --layer notam  # fetch only NOTAMs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ──────────────────────────── paths ────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_ROOT / "mobile_app" / "assets"

# ──────────────────────────── constants ────────────────────────────────────

GEOSERVER_BASE = "https://flightplan.romatsa.ro/init/geoserver/ows"
STATIC_BASE = "https://flightplan.romatsa.ro/init/static"

FT_TO_M = 0.3048

# WFS query template
WFS_PARAMS = (
    "service=WFS&version=1.0.0&request=GetFeature"
    "&maxFeatures=50000&outputFormat=application%2Fjson"
)

# All layers we fetch from GeoServer
LAYERS = {
    "uas_zones": {
        "url": f"{STATIC_BASE}/zone_restrictionate_uav.json",
        "wfs": False,
        "output": "restriction_zones.geojson",
        "description": "Permanent UAS restriction zones",
    },
    "notam": {
        "typeName": "carto:restrictii_notam_pt_uav",
        "wfs": True,
        "output": "notam_zones.geojson",
        "description": "Temporary UAS restrictions issued by NOTAM",
    },
    "notam_all": {
        "typeName": "opr:valid_notams_LRBB",
        "wfs": True,
        "output": "notam_all.geojson",
        "description": "All active NOTAMs in Romania FIR (LRBB)",
    },
    "ctr": {
        "typeName": "carto:CTR_LRBB",
        "wfs": True,
        "output": "airspace_ctr.geojson",
        "description": "Control Zones (CTR) around Romanian airports",
    },
    "tma": {
        "typeName": "opr:tma_boundary",
        "wfs": True,
        "output": "airspace_tma.geojson",
        "description": "Terminal Manoeuvring Areas (TMA)",
    },
    "airports": {
        "typeName": "carto:airport_current_WGS84",
        "wfs": True,
        "output": "airports.geojson",
        "description": "Airport locations (global, filter to Romania in app)",
    },
    "lower_routes": {
        "typeName": "carto:route_segments_lower_next",
        "wfs": True,
        "srsName": "EPSG:4326",
        "output": "lower_routes.geojson",
        "description": "Lower airway route segments (manned aviation corridors)",
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)


# ──────────────────────────── SSL context ──────────────────────────────────


def _ssl_ctx() -> ssl.SSLContext:
    """ROMATSA server uses a weak DH key; tolerate it for public data."""
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:!DH")
    return ctx


# ──────────────────────────── altitude parsing ─────────────────────────────

_RE_METRES = re.compile(
    r"^\s*(?P<val>\d+(?:\.\d+)?)\s*m?\s*(?:AGL)?\s*$", re.IGNORECASE
)
_RE_FEET = re.compile(
    r"^\s*(?P<val>\d+(?:\.\d+)?)\s*(?:FT|FEET)\s*(?:AGL|AMSL|STD|QNH)?\s*$",
    re.IGNORECASE,
)
_RE_FL = re.compile(r"^\s*FL\s*(?P<val>\d+)\s*$", re.IGNORECASE)
_RE_BARE_NUM = re.compile(r"^\s*(?P<val>\d{3,5})\s*$")  # e.g. "02500" in CTR


def parse_altitude_to_metres(raw: str | None, unit_hint: str | None = None) -> float | None:
    """
    Convert a ROMATSA altitude string to metres.

    Handles formats: GND, 120M AGL, 2500FT AMSL, FL105, 02500, BY NOTAM.
    *unit_hint* can be "FL" (flight levels) or "FT" to disambiguate bare numbers.
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None

    upper = raw.upper()
    if upper in ("GND", "GROUND", "0", "0M AGL", "0 M AGL"):
        return 0.0

    # metres
    m = _RE_METRES.match(raw)
    if m:
        return float(m.group("val"))

    # feet with suffix
    m = _RE_FEET.match(raw)
    if m:
        return round(float(m.group("val")) * FT_TO_M, 1)

    # flight level with prefix
    m = _RE_FL.match(raw)
    if m:
        return round(int(m.group("val")) * 100 * FT_TO_M, 1)

    # Bare number like "02500" or "06500" (common in CTR data)
    m = _RE_BARE_NUM.match(raw)
    if m:
        val = int(m.group("val"))
        if unit_hint == "FL":
            return round(val * 100 * FT_TO_M, 1)
        else:
            # default: feet (CTR upper limits are in feet)
            return round(val * FT_TO_M, 1)

    # "UNLTD" → unlimited
    if upper in ("UNLTD", "UNLIMITED", "UNL"):
        return 99999.0

    # "NESTB" → not established
    if upper in ("NESTB",):
        return None

    # BY NOTAM / unknown
    return None


# ──────────────────────────── download ─────────────────────────────────────


def _fetch_url(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "DroneApp/2.0"})
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=60) as resp:
        return resp.read()


def download_wfs(type_name: str, srs: str | None = None) -> dict:
    """Fetch a GeoServer WFS layer as GeoJSON."""
    url = f"{GEOSERVER_BASE}?{WFS_PARAMS}&typeName={type_name}"
    if srs:
        url += f"&srsName={srs}"
    log.info("WFS  %s", type_name)
    raw = _fetch_url(url)
    log.info("  -> %d bytes", len(raw))
    return json.loads(raw)


def download_static(url: str) -> dict:
    """Fetch a static JSON file."""
    log.info("GET  %s", url)
    raw = _fetch_url(url)
    log.info("  -> %d bytes", len(raw))
    return json.loads(raw)


# ──────────────────────────── enrichment per layer ─────────────────────────


def _enrich_uas_zones(source: dict) -> dict:
    """Permanent UAS restriction zones (original pipeline)."""
    features = []
    for feat in source.get("features", []):
        props = feat.get("properties", {})
        lo_raw = props.get("lower_lim", "")
        up_raw = props.get("upper_lim", "")
        props["lower_lim_raw"] = lo_raw
        props["upper_lim_raw"] = up_raw
        props["lower_limit_m"] = parse_altitude_to_metres(lo_raw)
        props["upper_limit_m"] = parse_altitude_to_metres(up_raw)
        props["layer"] = "uas_zones"
        feat["properties"] = props
        features.append(feat)
    return _wrap_fc(features, "Permanent UAS restriction zones")


def _enrich_notam_uas(source: dict) -> dict:
    """NOTAM-based UAS restrictions (temporary)."""
    features = []
    for feat in source.get("features", []):
        props = feat.get("properties", {})
        # Parse dates
        props["valid_from"] = props.get("dfrom")
        props["valid_to"] = props.get("dto")
        props["notam_id"] = props.get("serie", "")
        props["notam_type"] = props.get("tip", "")
        props["message"] = props.get("mesaj", "")
        props["layer"] = "notam_uas"
        # NOTAMs don't have structured altitude – they are always relevant
        props["lower_limit_m"] = 0.0
        props["upper_limit_m"] = None  # unknown ceiling
        feat["properties"] = props
        features.append(feat)
    return _wrap_fc(features, "Temporary UAS restrictions (NOTAM)")


def _enrich_notam_all(source: dict) -> dict:
    """All active NOTAMs with altitude and Q-code."""
    features = []
    for feat in source.get("features", []):
        props = feat.get("properties", {})
        unit = (props.get("um") or "").strip().upper()

        lo = props.get("lower")
        up = props.get("upper")

        # Convert NOTAM altitudes (unit is in "um" field: FL or FT)
        if unit == "FL":
            props["lower_limit_m"] = parse_altitude_to_metres(
                f"FL{lo}" if lo is not None else None
            )
            props["upper_limit_m"] = parse_altitude_to_metres(
                f"FL{up}" if up is not None else None
            )
        else:
            props["lower_limit_m"] = parse_altitude_to_metres(
                f"{lo}FT" if lo is not None else None
            ) if lo else 0.0
            props["upper_limit_m"] = parse_altitude_to_metres(
                f"{up}FT" if up is not None else None
            )

        props["lower_lim_raw"] = f"{lo} {unit}" if lo is not None else ""
        props["upper_lim_raw"] = f"{up} {unit}" if up is not None else ""
        props["notam_id"] = props.get("serie", "")
        props["valid_from"] = props.get("dfrom")
        props["valid_to"] = props.get("dto")
        props["qcode"] = props.get("qcode", "")
        props["airport"] = (props.get("ad") or "").strip()
        props["radius_nm"] = props.get("radius")
        props["message"] = props.get("mesaj", "")
        props["layer"] = "notam_all"
        feat["properties"] = props
        features.append(feat)
    return _wrap_fc(features, "All active NOTAMs in LRBB FIR")


def _enrich_ctr(source: dict) -> dict:
    """Control Zones around airports."""
    features = []
    for feat in source.get("features", []):
        props = feat.get("properties", {})
        lo_raw = (props.get("lower_limit") or "").strip()
        up_raw = (props.get("upper_limit") or "").strip()

        props["lower_lim_raw"] = lo_raw
        props["upper_lim_raw"] = up_raw
        props["lower_limit_m"] = parse_altitude_to_metres(lo_raw)
        props["upper_limit_m"] = parse_altitude_to_metres(up_raw)
        props["name"] = (props.get("arsp_name") or "").strip()
        props["icao"] = (props.get("ident") or "").strip()
        props["layer"] = "ctr"
        feat["properties"] = props
        features.append(feat)
    return _wrap_fc(features, "Control Zones (CTR)")


def _enrich_tma(source: dict) -> dict:
    """Terminal Manoeuvring Areas."""
    features = []
    for feat in source.get("features", []):
        props = feat.get("properties", {})
        props["icao"] = (props.get("ident") or "").strip()
        props["layer"] = "tma"
        feat["properties"] = props
        features.append(feat)
    return _wrap_fc(features, "Terminal Manoeuvring Areas (TMA)")


def _enrich_airports(source: dict) -> dict:
    """Airport locations – filter to Romania (ICAO prefix LR)."""
    features = []
    for feat in source.get("features", []):
        props = feat.get("properties", {})
        ident = (props.get("arpt_ident") or "").strip()
        icao = (props.get("icao_code") or "").strip()

        # Filter to Romanian airports only (ICAO code = LR)
        if icao != "LR":
            continue

        elev_raw = (props.get("airp_elev") or "").strip()
        try:
            props["elevation_ft"] = int(elev_raw) if elev_raw else None
        except ValueError:
            props["elevation_ft"] = None

        props["icao_ident"] = ident
        props["name"] = (props.get("airp_name") or "").strip()
        props["iata"] = (props.get("iata") or "").strip()
        props["ifr"] = (props.get("ifr") or "").strip() == "Y"
        props["layer"] = "airports"
        feat["properties"] = props
        features.append(feat)
    return _wrap_fc(features, "Romanian airports")


# Romania bounding box for route filtering
RO_LON_MIN, RO_LON_MAX = 20.0, 30.5
RO_LAT_MIN, RO_LAT_MAX = 43.5, 48.5


def _enrich_lower_routes(source: dict) -> dict:
    """Lower airway route segments – filter to Romania airspace."""
    features = []
    for feat in source.get("features", []):
        coords = feat.get("geometry", {}).get("coordinates", [])
        # Filter: at least one coordinate in Romania bbox
        in_ro = any(
            RO_LON_MIN <= c[0] <= RO_LON_MAX and RO_LAT_MIN <= c[1] <= RO_LAT_MAX
            for c in coords if len(c) >= 2
        )
        if not in_ro:
            continue

        props = feat.get("properties", {})

        min_alt_raw = (props.get("min_alt1") or "").strip()
        max_alt_raw = (props.get("max_alt") or "").strip()
        props["lower_lim_raw"] = min_alt_raw
        props["upper_lim_raw"] = max_alt_raw
        props["lower_limit_m"] = parse_altitude_to_metres(min_alt_raw)
        props["upper_limit_m"] = parse_altitude_to_metres(max_alt_raw)
        props["route"] = (props.get("route_ident") or "").strip()
        props["from_fix"] = (props.get("from_fix") or "").strip()
        props["to_fix"] = (props.get("to_fix") or "").strip()
        props["direction"] = (props.get("direction") or "").strip()
        props["layer"] = "lower_routes"
        feat["properties"] = props
        features.append(feat)
    return _wrap_fc(features, "Lower airway segments (Romania)")


ENRICHERS: dict[str, Any] = {
    "uas_zones": _enrich_uas_zones,
    "notam": _enrich_notam_uas,
    "notam_all": _enrich_notam_all,
    "ctr": _enrich_ctr,
    "tma": _enrich_tma,
    "airports": _enrich_airports,
    "lower_routes": _enrich_lower_routes,
}


# ──────────────────────────── helpers ──────────────────────────────────────


def _wrap_fc(features: list, description: str) -> dict:
    return {
        "type": "FeatureCollection",
        "metadata": {
            "source": "ROMATSA GeoServer",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "total_features": len(features),
            "description": description,
        },
        "features": features,
    }


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_geojson(geojson: dict, path: Path) -> bool:
    """Write GeoJSON; skip if content unchanged. Returns True if updated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_bytes = json.dumps(geojson, ensure_ascii=False).encode("utf-8")
    new_hash = _content_hash(new_bytes)

    if path.exists():
        if _content_hash(path.read_bytes()) == new_hash:
            log.info("  %s unchanged", path.name)
            return False

    path.write_bytes(new_bytes)
    log.info("  Wrote %s (%d bytes, %d features)",
             path.name, len(new_bytes), len(geojson.get("features", [])))
    return True


# ──────────────────────────── fetch pipeline ───────────────────────────────


def fetch_layer(name: str) -> dict | None:
    """Download one layer and return raw GeoJSON."""
    cfg = LAYERS[name]
    try:
        if cfg.get("wfs"):
            return download_wfs(cfg["typeName"], cfg.get("srsName"))
        else:
            return download_static(cfg["url"])
    except Exception:
        log.exception("Failed to fetch layer '%s'", name)
        return None


def process_layer(name: str, raw: dict) -> dict:
    """Enrich a raw GeoJSON layer."""
    enricher = ENRICHERS[name]
    return enricher(raw)


def fetch_all(layer_filter: str | None = None) -> dict[str, bool]:
    """Fetch, enrich, and write all (or selected) layers. Returns update map."""
    results: dict[str, bool] = {}
    names = [layer_filter] if layer_filter and layer_filter in LAYERS else list(LAYERS.keys())

    for name in names:
        cfg = LAYERS[name]
        log.info("--- %s: %s ---", name, cfg["description"])
        raw = fetch_layer(name)
        if raw is None:
            results[name] = False
            continue
        enriched = process_layer(name, raw)
        out_path = ASSETS_DIR / cfg["output"]
        results[name] = write_geojson(enriched, out_path)

        n = len(enriched.get("features", []))
        log.info("  %s: %d features", name, n)

    return results


# ──────────────────────────── cross-check ──────────────────────────────────
# (re-exported so the visualiser / Flutter backend can import it)


def point_in_polygon(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    """
    Ray-casting point-in-polygon for GeoJSON Polygon coordinates.
    polygon = [ ring, ... ] where ring = [ [lon,lat], ... ]
    Only checks the outer ring (index 0).
    """
    ring = polygon[0]
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


def cross_check(
    lon: float,
    lat: float,
    altitude_m: float,
    layers: dict[str, dict] | None = None,
) -> dict:
    """
    Check a coordinate against all loaded layers simultaneously.

    Returns a dict with keys per layer and a list of matching zone properties.
    If *layers* is None, loads from the asset files on disk.
    """
    if layers is None:
        layers = {}
        for name, cfg in LAYERS.items():
            path = ASSETS_DIR / cfg["output"]
            if path.exists():
                with open(path) as f:
                    layers[name] = json.load(f)

    result: dict[str, list[dict]] = {}

    for name, fc in layers.items():
        matches = []
        for feat in fc.get("features", []):
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})

            # Altitude check
            lo = props.get("lower_limit_m")
            up = props.get("upper_limit_m")
            alt_relevant = True
            if lo is not None and up is not None:
                alt_relevant = lo <= altitude_m <= up
            elif lo is not None:
                alt_relevant = altitude_m >= lo

            if not alt_relevant:
                continue

            # Geometry check (Polygon only for now)
            gtype = geom.get("type", "")
            coords = geom.get("coordinates", [])

            if gtype == "Polygon" and coords:
                if point_in_polygon(lon, lat, coords):
                    matches.append(props)
            elif gtype == "Point" and coords:
                # For airports: within ~5 km (rough)
                px, py = coords[0], coords[1]
                if abs(px - lon) < 0.05 and abs(py - lat) < 0.05:
                    matches.append(props)
            elif gtype == "LineString":
                # Route segments: check proximity (~2 km buffer)
                for c in coords:
                    if abs(c[0] - lon) < 0.02 and abs(c[1] - lat) < 0.02:
                        matches.append(props)
                        break

        if matches:
            result[name] = matches

    return result


# ──────────────────────────── main ─────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch & convert all ROMATSA aeronautical data to GeoJSON."
    )
    parser.add_argument(
        "--watch", type=int, default=0, metavar="SECONDS",
        help="Poll interval in seconds.  0 = one-shot (default).",
    )
    parser.add_argument(
        "--layer", type=str, default=None,
        choices=list(LAYERS.keys()),
        help="Fetch only a specific layer.",
    )
    parser.add_argument(
        "--check", nargs=3, metavar=("LON", "LAT", "ALT_M"),
        help="Cross-check a point: --check 26.1 44.4 50",
    )
    args = parser.parse_args()

    if args.check:
        lon, lat, alt = float(args.check[0]), float(args.check[1]), float(args.check[2])
        result = cross_check(lon, lat, alt)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    if args.watch > 0:
        log.info("Watch mode: polling every %d s.  Ctrl-C to stop.", args.watch)
        while True:
            try:
                fetch_all(args.layer)
            except Exception:
                log.exception("Fetch cycle failed - will retry")
            time.sleep(args.watch)
    else:
        fetch_all(args.layer)


if __name__ == "__main__":
    main()
