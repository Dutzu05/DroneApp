#!/usr/bin/env python3
"""
flight_plan_manager.py  -  ROMATSA UAS Flight Plan Utilities
─────────────────────────────────────────────────────────────
Provides:

  • TOWER_CONTACTS   – per-CTR contact data extracted from ANEXA 3
  • dd_to_dms()      – decimal degrees → degrees/minutes/seconds
  • circle_intersects_geojson() – check if a circle overlaps features
  • find_ctr_for_area() – return CTR(s) a circle falls within / touches
  • fill_anexa1()    – pre-fill the ANEXA 1 PDF form and write output file
  • area_check()     – full area + altitude risk assessment (JSON result)

Usage (CLI):
  python3 scripts/flight_plan_manager.py --check-area 25.8956 44.4268 300 120
  python3 scripts/flight_plan_manager.py --fill-pdf output.pdf --config plan.json
"""

from __future__ import annotations

import json
import math
import os
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ANEXA1_TEMPLATE_PATH = Path(
    os.environ.get("DRONE_ANEXA1_TEMPLATE_PATH", "/home/vlad/Downloads/ANEXA1.pdf")
)
ROMANIA_TZ = "Europe/Bucharest"
UAS_CLASS_OPTIONS: dict[str, str] = {
    "PRV250": "Construcție privată <250g",
    "C0": "C0 <250g",
    "C1": "C1 250g < 900g",
    "C2": "C2 900g < 4kg",
    "C3": "C3 <25kg",
    "C4": "C4 <25kg",
    "PRV25": "De construcție privată <25kg",
}
# The provided ANEXA 1 PDF exports both C1 and C2 using the same underlying value.
PDF_CLASS_EXPORT_MAP: dict[str, str] = {
    "PRV250": "PRV250",
    "C0": "C0",
    "C1": "C2",
    "C2": "C2",
    "C3": "C3",
    "C4": "C4",
    "PRV25": "PRV25",
}
CATEGORY_OPTIONS = {"A1", "A2", "A3"}
OPERATION_MODE_OPTIONS = {"VLOS", "VBLOS"}


class FlightPlanValidationError(ValueError):
    pass

# ──────────────────────────────────────────────────────────────────────────
# Tower contacts (ANEXA 3 – extracted via OCR, manually corrected)
# Key: ICAO airport code; value: dict with name / phone / email
# ──────────────────────────────────────────────────────────────────────────

TOWER_CONTACTS: dict[str, dict[str, Any]] = {
    # ── Civil TWR units ────────────────────────────────────────────────
    "LRAR": {
        "name":   "Arad TWR",
        "city":   "Arad",
        "phone":  ["0257 255 706", "0734 222 926"],
        "email":  "twr.arad@romatsa.ro",
        "type":   "civil",
    },
    "LRBC": {
        "name":   "Bacău TWR",
        "city":   "Bacău",
        "phone":  ["0234 575 432"],
        "email":  "twr.bacau@romatsa.ro",
        "type":   "civil",
    },
    "LRBM": {
        "name":   "Baia Mare TWR",
        "city":   "Baia Mare",
        "phone":  ["0721 250 173"],
        "email":  "twr.baiamare@romatsa.ro",
        "type":   "civil",
    },
    "LRBV": {
        "name":   "Brașov TWR",
        "city":   "Brașov",
        "phone":  ["0257 328 573", "0368 447 751"],
        "email":  "twr.brasov@romatsa.ro",
        "type":   "civil",
    },
    "LROP": {
        "name":   "București Otopeni TWR",
        "city":   "București",
        "phone":  ["0724 222 137"],
        "email":  "drone.otp@romatsa.ro",   # drone-specific address!
        "type":   "civil",
        "note":   "Drone-specific email – use for all UAS notifications",
    },
    "LRBS": {
        "name":   "București Băneasa TWR",
        "city":   "București",
        "phone":  ["021 232 39 61", "0724 222 138"],
        "email":  "twr.lrbs@romatsa.ro",
        "type":   "civil",
    },
    "LRCL": {
        "name":   "Cluj-Napoca TWR",
        "city":   "Cluj-Napoca",
        "phone":  ["0264 410 421", "0737 551 151"],
        "email":  "apptwrcluj@romatsa.ro",
        "type":   "civil",
    },
    "LRCK": {
        "name":   "Constanța TWR",
        "city":   "Constanța",
        "phone":  ["0241 258 872", "0737 552 250"],
        "email":  "drone.lrck@romatsa.ro",  # drone-specific address!
        "type":   "civil",
    },
    "LRCV": {
        "name":   "Craiova TWR",
        "city":   "Craiova",
        "phone":  ["0251 415 180", "0737 552 254"],
        "email":  "aro.lrcv@romatsa.ro",
        "type":   "civil",
    },
    "LRIA": {
        "name":   "Iași TWR",
        "city":   "Iași",
        "phone":  ["0232 271 520"],
        "email":  "twr.iasi@romatsa.ro",
        "email_alt": "twriasi@yahoo.com",
        "type":   "civil",
    },
    "LROD": {
        "name":   "Oradea TWR",
        "city":   "Oradea",
        "phone":  ["0259 427 366", "0737 552 256"],
        "email":  "aro.lrod@romatsa.ro",
        "type":   "civil",
    },
    "LRSM": {
        "name":   "Satu Mare TWR",
        "city":   "Satu Mare",
        "phone":  ["0261 770 053", "0737 551 150"],
        "email":  "aro.lrsm@romatsa.ro",
        "type":   "civil",
    },
    "LRSB": {
        "name":   "Sibiu TWR",
        "city":   "Sibiu",
        "phone":  ["0269 253 088", "0731 499 410"],
        "email":  "twr.lrsb@romatsa.ro",
        "type":   "civil",
    },
    "LRSV": {
        "name":   "Suceava TWR",
        "city":   "Suceava",
        "phone":  ["0230 535 602", "0737 551 152"],
        "email":  "twr.suceava@romatsa.ro",
        "type":   "civil",
    },
    "LRTM": {
        "name":   "Târgu Mureș TWR",
        "city":   "Târgu Mureș",
        "phone":  ["0265 328 260", "0747 064 234"],
        "email":  "drone.mures@romatsa.ro",  # drone-specific address!
        "type":   "civil",
    },
    "LRTR": {
        "name":   "Timișoara TWR",
        "city":   "Timișoara",
        "phone":  ["0256 295 911", "0724 263 848"],
        "email":  "twr.lrtr@romatsa.ro",
        "type":   "civil",
    },
    "LRTC": {
        "name":   "Tulcea TWR",
        "city":   "Tulcea",
        "phone":  ["0240 511 581", "0737 552 253"],
        "email":  "twr.tulcea@romatsa.ro",
        "type":   "civil",
    },
    # ── Military TWR units ─────────────────────────────────────────────
    "LRBO": {
        "name":   "Boboc Military TWR",
        "city":   "Boboc",
        "phone":  ["0238 718 984"],
        "email":  "milais_safa@roaf.ro",
        "type":   "military",
        "note":   "For shared civil-military airports (Bacău, Constanța, Otopeni, Timișoara) contact the CIVIL TWR",
    },
    "LRCT": {
        "name":   "Câmpia Turzii Military TWR",
        "city":   "Câmpia Turzii",
        "phone":  ["0264 366 903"],
        "email":  "currentops_71afb@roaf.ro",
        "type":   "military",
    },
    "LRFT": {
        "name":   "Fetești Military TWR",
        "city":   "Fetești",
        "phone":  ["0243 362 579"],
        "email":  "milais_bz86aer@roaf.ro",
        "type":   "military",
    },
}

# Map CTR names (from GeoJSON properties) → ICAO codes
CTR_NAME_TO_ICAO: dict[str, str] = {
    "ARAD CTR":              "LRAR",
    "BACAU CTR":             "LRBC",
    "BAIA MARE CTR":         "LRBM",
    "BRASOV CTR":            "LRBV",
    "BUCURESTI CTR":         "LROP",
    "OTOPENI CTR":           "LROP",
    "BANEASA CTR":           "LRBS",
    "BANEASA":               "LRBS",
    "CLUJ CTR":              "LRCL",
    "CONSTANTA CTR":         "LRCK",
    "CRAIOVA CTR":           "LRCV",
    "IASI CTR":              "LRIA",
    "ORADEA CTR":            "LROD",
    "SATU MARE CTR":         "LRSM",
    "SIBIU CTR":             "LRSB",
    "SUCEAVA CTR":           "LRSV",
    "TARGU MURES CTR":       "LRTM",
    "TIMISOARA CTR":         "LRTR",
    "TULCEA CTR":            "LRTC",
    "BOBOC CTR":             "LRBO",
    "CAMPIA TURZII CTR":     "LRCT",
    "FETESTI CTR":           "LRFT",
}


def twr_label(icao: str) -> str:
    contact = TOWER_CONTACTS.get(icao, {})
    return f"{contact.get('city', icao)} - {icao}"


def available_twr_options() -> list[dict[str, str]]:
    return [
        {
            "icao": icao,
            "label": twr_label(icao),
            "name": contact["name"],
            "email": contact["email"],
        }
        for icao, contact in sorted(TOWER_CONTACTS.items())
        if contact.get("type") == "civil"
    ]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _require_text(payload: dict[str, Any], key: str, label: str) -> str:
    value = _clean_text(payload.get(key))
    if not value:
        raise FlightPlanValidationError(f"{label} is required")
    return value


def _optional_text(payload: dict[str, Any], key: str) -> str:
    return _clean_text(payload.get(key))


def _parse_float(value: Any, label: str) -> float:
    text = _clean_text(value).replace(",", ".")
    if not text:
        raise FlightPlanValidationError(f"{label} is required")
    try:
        return float(text)
    except ValueError as exc:
        raise FlightPlanValidationError(f"{label} must be numeric") from exc


def _parse_schedule(payload: dict[str, Any], now: datetime | None = None) -> tuple[datetime, datetime, str, str]:
    start_date = _require_text(payload, "start_date", "Start date")
    end_date = _clean_text(payload.get("end_date")) or start_date
    start_time = _require_text(payload, "start_time", "Start time")
    end_time = _require_text(payload, "end_time", "End time")
    timezone_name = _clean_text(payload.get("timezone")) or ROMANIA_TZ

    try:
        tz = ZoneInfo(timezone_name)
    except Exception as exc:
        raise FlightPlanValidationError(f"Unsupported timezone: {timezone_name}") from exc

    try:
        start_local = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        end_local = datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    except ValueError as exc:
        raise FlightPlanValidationError("Dates must use YYYY-MM-DD and times must use HH:MM") from exc

    if end_local <= start_local:
        raise FlightPlanValidationError("End date/time must be after start date/time")

    current_local = now.astimezone(tz) if now else datetime.now(tz)
    if end_local <= current_local:
        raise FlightPlanValidationError("Flight plan must be ongoing or in the future")

    return (
        start_local,
        end_local,
        start_local.strftime("%d.%m.%Y"),
        end_local.strftime("%d.%m.%Y"),
    )


def _validate_email(value: str, label: str) -> str:
    cleaned = _clean_text(value).lower()
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise FlightPlanValidationError(f"{label} must be a valid email")
    return cleaned


def _validate_phone(value: str, label: str, *, required: bool = True) -> str:
    cleaned = _clean_text(value)
    if required and not cleaned:
        raise FlightPlanValidationError(f"{label} is required")
    return cleaned


# ──────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ──────────────────────────────────────────────────────────────────────────

def dd_to_dms(dd: float) -> tuple[int, int, float]:
    """Convert decimal degrees to (degrees, minutes, seconds)."""
    dd = abs(dd)
    d = int(dd)
    m = int((dd - d) * 60)
    s = round(((dd - d) * 60 - m) * 60, 1)
    return d, m, s


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Haversine distance in metres between two WGS-84 points."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def point_in_polygon(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_to_segment_dist_m(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """Approximate distance (metres) from point P to segment A-B (flat earth)."""
    dx, dy = bx - ax, by - ay
    t = 0.0
    if dx * dx + dy * dy > 0:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx = ax + t * dx
    cy = ay + t * dy
    return haversine_m(px, py, cx, cy)


def _segments_intersect(a: list[float], b: list[float], c: list[float], d: list[float]) -> bool:
    def orientation(p: list[float], q: list[float], r: list[float]) -> int:
        value = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
        if abs(value) < 1e-12:
            return 0
        return 1 if value > 0 else 2

    def on_segment(p: list[float], q: list[float], r: list[float]) -> bool:
        return (
            min(p[0], r[0]) - 1e-12 <= q[0] <= max(p[0], r[0]) + 1e-12
            and min(p[1], r[1]) - 1e-12 <= q[1] <= max(p[1], r[1]) + 1e-12
        )

    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_segment(a, c, b):
        return True
    if o2 == 0 and on_segment(a, d, b):
        return True
    if o3 == 0 and on_segment(c, a, d):
        return True
    if o4 == 0 and on_segment(c, b, d):
        return True
    return False


def polygon_intersects_ring(polygon: list[list[float]], ring: list[list[float]]) -> bool:
    if not polygon or not ring:
        return False

    for point in polygon:
        if point_in_polygon(point[0], point[1], ring):
            return True
    for point in ring:
        if point_in_polygon(point[0], point[1], polygon):
            return True

    for i in range(len(polygon)):
        pa = polygon[i]
        pb = polygon[(i + 1) % len(polygon)]
        for j in range(len(ring)):
            ra = ring[j]
            rb = ring[(j + 1) % len(ring)]
            if _segments_intersect(pa, pb, ra, rb):
                return True
    return False


def circle_intersects_ring(
    lon: float, lat: float, radius_m: float,
    ring: list[list[float]],
) -> bool:
    """Return True if circle (centre lon/lat, radius_m) intersects a polygon ring."""
    # 1. Centre inside polygon?
    if point_in_polygon(lon, lat, ring):
        return True
    # 2. Any vertex inside circle?
    for pt in ring:
        if haversine_m(lon, lat, pt[0], pt[1]) <= radius_m:
            return True
    # 3. Any edge closer than radius to centre?
    n = len(ring)
    for i in range(n):
        j = (i + 1) % n
        if _point_to_segment_dist_m(lon, lat, ring[i][0], ring[i][1], ring[j][0], ring[j][1]) <= radius_m:
            return True
    return False


def circle_intersects_feature(
    lon: float, lat: float, radius_m: float,
    feat: dict,
    alt_m: float | None = None,
) -> bool:
    """Return True if circle intersects the GeoJSON feature (optionally altitude-filtered)."""
    geom = feat.get("geometry")
    if not geom:
        return False

    # Altitude filter
    if alt_m is not None:
        p = feat.get("properties", {})
        lo = p.get("lower_limit_m")
        up = p.get("upper_limit_m")
        if lo is not None and up is not None:
            if alt_m < lo or alt_m > up:
                return False

    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])

    if gtype == "Polygon":
        return any(circle_intersects_ring(lon, lat, radius_m, ring) for ring in coords)
    elif gtype == "MultiPolygon":
        return any(
            circle_intersects_ring(lon, lat, radius_m, ring)
            for poly in coords
            for ring in poly
        )
    return False


def polygon_intersects_feature(
    polygon: list[list[float]],
    feat: dict,
    alt_m: float | None = None,
) -> bool:
    geom = feat.get("geometry")
    if not geom:
        return False

    if alt_m is not None:
        p = feat.get("properties", {})
        lo = p.get("lower_limit_m")
        up = p.get("upper_limit_m")
        if lo is not None and up is not None and (alt_m < lo or alt_m > up):
            return False

    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])

    if gtype == "Polygon":
        return any(polygon_intersects_ring(polygon, ring) for ring in coords)
    if gtype == "MultiPolygon":
        return any(
            polygon_intersects_ring(polygon, ring)
            for poly in coords
            for ring in poly
        )
    return False


def build_circle_area(lon: float, lat: float, radius_m: float) -> dict[str, Any]:
    return {
        "kind": "circle",
        "center_lon": lon,
        "center_lat": lat,
        "radius_m": radius_m,
    }


def build_polygon_area(points: list[list[float]]) -> dict[str, Any]:
    clean_points = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise FlightPlanValidationError("Polygon points must be [lon, lat] pairs")
        lon = float(point[0])
        lat = float(point[1])
        if not -180 <= lon <= 180:
            raise FlightPlanValidationError("Polygon longitude must be between -180 and 180")
        if not -90 <= lat <= 90:
            raise FlightPlanValidationError("Polygon latitude must be between -90 and 90")
        clean_points.append([lon, lat])
    if len(clean_points) < 3 or len(clean_points) > 5:
        raise FlightPlanValidationError("Polygon area must contain between 3 and 5 vertices")
    if clean_points[0] == clean_points[-1]:
        clean_points = clean_points[:-1]
    return {
        "kind": "polygon",
        "points": clean_points,
    }


# ──────────────────────────────────────────────────────────────────────────
# Layer access
# ──────────────────────────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).resolve().parent
_ASSET_DIR  = _SCRIPT_DIR.parent / "mobile_app" / "assets"

LAYER_FILES = {
    "uas_zones":    _ASSET_DIR / "restriction_zones.geojson",
    "notam":        _ASSET_DIR / "notam_zones.geojson",
    "ctr":          _ASSET_DIR / "airspace_ctr.geojson",
    "tma":          _ASSET_DIR / "airspace_tma.geojson",
}

_geojson_cache: dict[str, dict] = {}

def _load(layer_key: str) -> dict:
    if layer_key not in _geojson_cache:
        p = LAYER_FILES[layer_key]
        _geojson_cache[layer_key] = json.loads(p.read_text())
    return _geojson_cache[layer_key]


def assess_flight_area(area: dict[str, Any], alt_m: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "area": area,
        "alt_m": alt_m,
        "ctr_hits": [],
        "uas_hits": [],
        "notam_hits": [],
        "tma_hits": [],
        "tower_contacts": [],
        "risk_level": "LOW",
        "summary": "",
        "eligibility_status": "ready",
        "warnings": [],
    }

    def matches(feat: dict) -> bool:
        if area["kind"] == "circle":
            return circle_intersects_feature(
                area["center_lon"],
                area["center_lat"],
                area["radius_m"],
                feat,
                alt_m,
            )
        return polygon_intersects_feature(area["points"], feat, alt_m)

    ctr_data = _load("ctr")
    for feat in ctr_data.get("features", []):
        if matches(feat):
            p = feat.get("properties", {})
            result["ctr_hits"].append(p)
            name = (p.get("name") or p.get("arsp_name") or "").upper()
            icao = p.get("icao") or _resolve_icao(name)
            if icao and icao in TOWER_CONTACTS:
                contact = {**TOWER_CONTACTS[icao], "icao": icao}
                if contact not in result["tower_contacts"]:
                    result["tower_contacts"].append(contact)

    for layer_key, key in (("uas_zones", "uas_hits"), ("notam", "notam_hits"), ("tma", "tma_hits")):
        layer_data = _load(layer_key)
        for feat in layer_data.get("features", []):
            if matches(feat):
                result[key].append(feat.get("properties", {}))

    ctr_count = len(result["ctr_hits"])
    uas_count = len(result["uas_hits"])
    notam_count = len(result["notam_hits"])
    tma_count = len(result["tma_hits"])

    if ctr_count or uas_count:
        result["risk_level"] = "HIGH"
    elif notam_count or tma_count:
        result["risk_level"] = "MEDIUM"

    summary_parts = []
    if ctr_count:
        ctr_names = [h.get("name") or h.get("arsp_name") or "CTR" for h in result["ctr_hits"]]
        summary_parts.append(f"CTR overlap: {', '.join(ctr_names)}")
    if uas_count:
        summary_parts.append(f"{uas_count} UAS restriction zone(s)")
    if notam_count:
        summary_parts.append(f"{notam_count} NOTAM zone(s)")
    if tma_count:
        summary_parts.append(f"{tma_count} TMA zone(s) at {alt_m:.0f} m")
    if not summary_parts:
        summary_parts.append("No conflicting airspace found")

    if uas_count:
        result["eligibility_status"] = "manual_review"
        result["warnings"].append(
            "ANEXA 1 notes say open-category flights in CTR are considered authorized only outside restricted UAS geographical zones."
        )
    if result["risk_level"] == "HIGH":
        result["warnings"].append("High-risk airspace overlap detected. Manual review is required before relying on this plan.")
    elif result["risk_level"] == "MEDIUM":
        result["warnings"].append("Additional coordination may be needed because NOTAM/TMA overlaps were detected.")

    result["summary"] = ". ".join(summary_parts) + "."
    return result


# ──────────────────────────────────────────────────────────────────────────
# Area check
# ──────────────────────────────────────────────────────────────────────────

def area_check(
    lon: float,
    lat: float,
    radius_m: float,
    alt_m: float,
) -> dict:
    """
    Full risk assessment for a circular flight area.

    Returns dict with keys:
      location        – {lon, lat, radius_m, alt_m}
      ctr_hits        – list of CTR features that intersect the circle
      uas_hits        – list of UAS zone features that intersect
      notam_hits      – list of NOTAM zone features that intersect
      tma_hits        – list of TMA features that intersect (altitude considered)
      tower_contacts  – list of TOWER_CONTACTS entries for each CTR hit
      risk_level      – "LOW" | "MEDIUM" | "HIGH"
      summary         – human-readable summary string
    """
    result = assess_flight_area(build_circle_area(lon, lat, radius_m), alt_m)
    result["location"] = {"lon": lon, "lat": lat, "radius_m": radius_m, "alt_m": alt_m}
    return result


def _resolve_icao(ctr_name: str) -> str | None:
    """Try to find ICAO code for a CTR by name."""
    for k, v in CTR_NAME_TO_ICAO.items():
        if k in ctr_name or ctr_name in k:
            return v
    # Fallback: check if name contains known city fragments
    city_map = {
        "ARAD": "LRAR", "BACAU": "LRBC", "BAIA MARE": "LRBM",
        "BRASOV": "LRBV", "OTOPENI": "LROP", "BANEASA": "LRBS",
        "CLUJ": "LRCL", "CONSTANTA": "LRCK", "CRAIOVA": "LRCV",
        "IASI": "LRIA", "ORADEA": "LROD", "SATU MARE": "LRSM",
        "SIBIU": "LRSB", "SUCEAVA": "LRSV", "TARGU MURES": "LRTM",
        "TIMISOARA": "LRTR", "TULCEA": "LRTC",
    }
    for city, icao in city_map.items():
        if city in ctr_name:
            return icao
    return None


def _normalize_area(payload: dict[str, Any]) -> dict[str, Any]:
    area_kind = (_clean_text(payload.get("area_kind")) or "circle").lower()
    if area_kind == "circle":
        center_lon = _parse_float(payload.get("center_lon"), "Center longitude")
        center_lat = _parse_float(payload.get("center_lat"), "Center latitude")
        radius_m = _parse_float(payload.get("radius_m"), "Radius")
        if not -180 <= center_lon <= 180:
            raise FlightPlanValidationError("Center longitude must be between -180 and 180")
        if not -90 <= center_lat <= 90:
            raise FlightPlanValidationError("Center latitude must be between -90 and 90")
        if radius_m <= 0:
            raise FlightPlanValidationError("Radius must be greater than 0")
        return build_circle_area(center_lon, center_lat, radius_m)
    if area_kind == "polygon":
        return build_polygon_area(payload.get("polygon_points") or [])
    raise FlightPlanValidationError("Area kind must be circle or polygon")


def area_to_geojson(area: dict[str, Any]) -> dict[str, Any]:
    if area["kind"] == "circle":
        return {
            "type": "Feature",
            "properties": {"shape": "circle", "radius_m": area["radius_m"]},
            "geometry": {
                "type": "Point",
                "coordinates": [area["center_lon"], area["center_lat"]],
            },
        }
    polygon = list(area["points"])
    if polygon[0] != polygon[-1]:
        polygon = polygon + [polygon[0]]
    return {
        "type": "Feature",
        "properties": {"shape": "polygon"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [polygon],
        },
    }


def _build_public_id(now: datetime | None = None) -> str:
    moment = now or datetime.utcnow()
    return f"FP-{moment.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3).upper()}"


def build_anexa_payload(plan: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "operator": plan["operator_name"],
        "date_contact": plan["operator_contact"],
        "fax": plan.get("fax", ""),
        "email": plan["operator_email"],
        "pers_contact": plan["contact_person"],
        "telefon_fix": plan.get("phone_landline", ""),
        "mobil": plan["phone_mobile"],
        "inmatriculare": plan["uas_registration"],
        "greutate": f"{plan['mtom_kg']:.3f}".rstrip("0").rstrip("."),
        "clasa": PDF_CLASS_EXPORT_MAP[plan["uas_class_code"]],
        "categorie": plan["category"],
        "mod_operare": plan["operation_mode"],
        "twr": plan["selected_twr"],
        "pilot_name": plan["pilot_name"],
        "pilot_phone": plan["pilot_phone"],
        "scop_zbor": plan["purpose"],
        "alt_max_m": f"{plan['max_altitude_m']:.0f}",
        "data_start": plan["pdf_start_date"],
        "data_end": plan["pdf_end_date"],
        "ora_start": plan["pdf_start_time"],
        "ora_end": plan["pdf_end_time"],
        "localitatea": plan["location_name"],
    }
    if plan["area_kind"] == "circle":
        payload["center_lon"] = plan["center_lon"]
        payload["center_lat"] = plan["center_lat"]
        payload["radius_m"] = plan["radius_m"]
    else:
        payload["polygon"] = plan["polygon_points"]
    return payload


def validate_and_build_flight_plan(
    payload: dict[str, Any],
    actor: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    owner_email = _validate_email(actor.get("email") or "", "Logged in user email")
    owner_display_name = _clean_text(actor.get("display_name")) or owner_email
    owner_google_user_id = _clean_text(actor.get("google_user_id"))

    operator_email = _validate_email(
        payload.get("operator_email") or owner_email,
        "Operator email",
    )
    operator_name = _require_text(payload, "operator_name", "Operator name")
    operator_contact = _require_text(payload, "operator_contact", "Operator contact")
    contact_person = _require_text(payload, "contact_person", "Contact person")
    phone_mobile = _validate_phone(payload.get("phone_mobile"), "Mobile phone")
    phone_landline = _validate_phone(payload.get("phone_landline"), "Landline phone", required=False)
    fax = _optional_text(payload, "fax")
    uas_registration = _require_text(payload, "uas_registration", "UAS registration")
    uas_class_code = (_require_text(payload, "uas_class_code", "UAS class")).upper()
    if uas_class_code not in UAS_CLASS_OPTIONS:
        raise FlightPlanValidationError("Unsupported UAS class")
    uas_class_label = UAS_CLASS_OPTIONS[uas_class_code]
    category = (_require_text(payload, "category", "Flight category")).upper()
    if category not in CATEGORY_OPTIONS:
        raise FlightPlanValidationError("Unsupported flight category")
    operation_mode = (_require_text(payload, "operation_mode", "Operation mode")).upper()
    if operation_mode not in OPERATION_MODE_OPTIONS:
        raise FlightPlanValidationError("Unsupported operation mode")

    mtom_kg = _parse_float(payload.get("mtom_kg"), "MTOM (kg)")
    max_altitude_m = _parse_float(payload.get("max_altitude_m"), "Maximum altitude")
    if max_altitude_m < 0 or max_altitude_m > 120:
        raise FlightPlanValidationError("Maximum altitude must be between 0 and 120 metres AGL for ANEXA 1")

    selected_twr = (_require_text(payload, "selected_twr", "Selected TWR")).upper()
    if selected_twr not in TOWER_CONTACTS:
        raise FlightPlanValidationError("Selected TWR is not recognised")

    pilot_name = _require_text(payload, "pilot_name", "Pilot name")
    pilot_phone = _validate_phone(payload.get("pilot_phone"), "Pilot phone")
    purpose = _require_text(payload, "purpose", "Flight purpose")
    location_name = _require_text(payload, "location_name", "Location/locality")
    area = _normalize_area(payload)
    start_local, end_local, pdf_start_date, pdf_end_date = _parse_schedule(payload, now=now)

    assessment = assess_flight_area(area, max_altitude_m)

    return {
        "public_id": _build_public_id(now),
        "owner_email": owner_email,
        "owner_display_name": owner_display_name,
        "owner_google_user_id": owner_google_user_id,
        "operator_name": operator_name,
        "operator_contact": operator_contact,
        "contact_person": contact_person,
        "phone_landline": phone_landline,
        "phone_mobile": phone_mobile,
        "fax": fax,
        "operator_email": operator_email,
        "uas_registration": uas_registration,
        "uas_class_code": uas_class_code,
        "uas_class_label": uas_class_label,
        "category": category,
        "operation_mode": operation_mode,
        "mtom_kg": mtom_kg,
        "pilot_name": pilot_name,
        "pilot_phone": pilot_phone,
        "purpose": purpose,
        "local_timezone": _clean_text(payload.get("timezone")) or ROMANIA_TZ,
        "scheduled_start_at": start_local.astimezone(ZoneInfo("UTC")).isoformat(),
        "scheduled_end_at": end_local.astimezone(ZoneInfo("UTC")).isoformat(),
        "pdf_start_date": pdf_start_date,
        "pdf_end_date": pdf_end_date,
        "pdf_start_time": start_local.strftime("%H:%M"),
        "pdf_end_time": end_local.strftime("%H:%M"),
        "location_name": location_name,
        "area_kind": area["kind"],
        "center_lon": area.get("center_lon"),
        "center_lat": area.get("center_lat"),
        "radius_m": area.get("radius_m"),
        "polygon_points": area.get("points"),
        "area_geojson": area_to_geojson(area),
        "max_altitude_m": max_altitude_m,
        "selected_twr": selected_twr,
        "risk_level": assessment["risk_level"],
        "risk_summary": assessment["summary"],
        "airspace_assessment": assessment,
        "created_from_app": _clean_text(payload.get("created_from_app")) or "visualise_zones_web",
    }


# ──────────────────────────────────────────────────────────────────────────
# PDF form filler  (requires pypdf)
# ──────────────────────────────────────────────────────────────────────────

def _dms_fields(dd: float, prefix: str) -> dict[str, str]:
    """Return dict of form-field name → string value for a lat or lon."""
    d, m, s = dd_to_dms(dd)
    return {
        f"gr{prefix}":  str(d),
        f"min{prefix}": str(m),
        f"sec{prefix}": f"{s:.1f}",
    }


def fill_anexa1(
    template_path: str | Path,
    output_path: str | Path,
    data: dict,
) -> Path:
    """
    Fill the ANEXA 1 PDF form and save to output_path.

    data keys (all optional, empty string if missing):
      operator       – operator name
      date_contact   – operator address / contact details
      fax            – fax number
      email          – operator email
      pers_contact   – contact person name
      telefon_fix    – landline phone
      mobil          – mobile phone
      inmatriculare  – UAS registration / serial number
      greutate       – MTOM in kg  (string)
      clasa          – class code: PRV250 | C0 | C2 | C3 | C4 | PRV25
      categorie      – A1 | A2 | A3
      mod_operare    – VLOS | VBLOS
      twr            – ICAO code of the CTR tower (LROP, LRBV, …)
      pilot_name     – pilot full name
      pilot_phone    – pilot phone
      scop_zbor      – purpose of flight (free text)
      alt_max_m      – max altitude AGL in metres  (string)
      data_start     – start date  (DD.MM.YYYY)
      data_end       – end date    (DD.MM.YYYY or same as start)
      ora_start      – start time  (HH:MM UTC)
      ora_end        – end time    (HH:MM UTC)
      localitatea    – locality / place name
      center_lon     – circle center longitude (decimal degrees)
      center_lat     – circle center latitude  (decimal degrees)
      radius_m       – circle radius in metres  (string)
      polygon        – list of up to 5 [lon, lat] for polygon zone
                       (overrides circle fields if provided)
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import BooleanObject, NameObject
    except ImportError:
        raise RuntimeError("pypdf is required: pip install pypdf")

    reader = PdfReader(str(template_path))
    writer = PdfWriter()
    writer.append(reader)

    # Build the field-value mapping
    fv: dict[str, str] = {}

    def g(key: str, default: str = "") -> str:
        return str(data.get(key, default))

    fv["operator"]         = g("operator")
    fv["Date de contact"]  = g("date_contact")
    fv["Fax"]              = g("fax")
    fv["email"]            = g("email")
    fv["pers_contact"]     = g("pers_contact")
    fv["telefon_fix"]      = g("telefon_fix")
    fv["mobil"]            = g("mobil")
    fv["inmatriculare"]    = g("inmatriculare")
    fv["greutate"]         = g("greutate")
    fv["Clasa"]            = g("clasa", "C2")
    fv["categorie_zbor"]   = g("categorie", "A2")
    fv["mod_operare"]      = g("mod_operare", "VLOS")
    fv["TWR"]              = g("twr", "LRBV")
    fv["Nume_pilot"]       = g("pilot_name")
    fv["telefon_pilot"]    = g("pilot_phone")
    fv["scop_zbor"]        = g("scop_zbor")
    fv["inaltime_zbor"]    = g("alt_max_m")
    fv["data_zbor"]        = g("data_start")
    fv["data_zbor_end"]    = g("data_end")
    fv["ora_start"]        = g("ora_start")
    fv["ora_finală"]       = g("ora_end")
    fv["localitatea"]      = g("localitatea")

    # Polygon zone (5 vertices)
    poly = data.get("polygon")
    if poly and len(poly) >= 1:
        for i, pt in enumerate(poly[:5], start=1):
            lon, lat = float(pt[0]), float(pt[1])
            lon_d, lon_m, lon_s = dd_to_dms(lon)
            lat_d, lat_m, lat_s = dd_to_dms(lat)
            fv[f"gr{i}_long"]  = str(lon_d)
            fv[f"min{i}_long"] = str(lon_m)
            fv[f"sec{i}_long"] = f"{lon_s:.1f}"
            fv[f"gr{i}_lat"]   = str(lat_d)
            fv[f"min{i}_lat"]  = str(lat_m)
            fv[f"sec{i}_lat"]  = f"{lat_s:.1f}"

    # Circular zone
    clon = data.get("center_lon")
    clat = data.get("center_lat")
    if clon is not None and clat is not None:
        clon, clat = float(clon), float(clat)
        lon_d, lon_m, lon_s = dd_to_dms(clon)
        lat_d, lat_m, lat_s = dd_to_dms(clat)
        fv["gr_center_long"]  = str(lon_d)
        fv["min_center_long"] = str(lon_m)
        fv["sec_center_long"] = f"{lon_s:.1f}"
        fv["gr_center_lat"]   = str(lat_d)
        fv["min_center_lat"]  = str(lat_m)
        fv["sec_center_lat"]  = f"{lat_s:.1f}"

    if data.get("radius_m"):
        fv["raza"] = str(data["radius_m"])

    # Write fields to all pages
    for page in writer.pages:
        writer.update_page_form_field_values(page, fv)
    writer._root_object.update({NameObject("/NeedAppearances"): BooleanObject(True)})

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)

    return out


def generate_anexa1_pdf(
    plan: dict[str, Any],
    output_path: str | Path,
    *,
    template_path: str | Path = ANEXA1_TEMPLATE_PATH,
) -> Path:
    return fill_anexa1(template_path, output_path, build_anexa_payload(plan))


# ──────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Flight Plan Manager CLI")
    sub = parser.add_subparsers(dest="cmd")

    # check-area subcommand
    ca = sub.add_parser("check-area", help="Check a circular flight area for conflicts")
    ca.add_argument("lon",      type=float, help="Center longitude (decimal degrees)")
    ca.add_argument("lat",      type=float, help="Center latitude  (decimal degrees)")
    ca.add_argument("radius_m", type=float, help="Circle radius in metres")
    ca.add_argument("alt_m",    type=float, help="Max altitude in metres AGL")

    # fill-pdf subcommand
    fp = sub.add_parser("fill-pdf", help="Fill ANEXA 1 PDF form")
    fp.add_argument("--template", default=str(ANEXA1_TEMPLATE_PATH),
                    help="Template PDF path")
    fp.add_argument("--output", required=True, help="Output PDF path")
    fp.add_argument("--config", required=True, help="JSON file with flight plan data")

    # list-contacts
    sub.add_parser("contacts", help="List all tower contacts")

    args = parser.parse_args()

    if args.cmd == "check-area":
        result = area_check(args.lon, args.lat, args.radius_m, args.alt_m)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.cmd == "fill-pdf":
        with open(args.config) as f:
            data = json.load(f)
        out = fill_anexa1(args.template, args.output, data)
        print(f"✅ Generated: {out}")

    elif args.cmd == "contacts":
        print("ROMATSA Tower Contacts (ANEXA 3)")
        print("=" * 60)
        for icao, c in TOWER_CONTACTS.items():
            print(f"\n{icao} – {c['name']}")
            print(f"  Phone : {', '.join(c['phone'])}")
            print(f"  Email : {c['email']}")
            if "note" in c:
                print(f"  Note  : {c['note']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
