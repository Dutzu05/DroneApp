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
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    result: dict[str, Any] = {
        "location": {"lon": lon, "lat": lat, "radius_m": radius_m, "alt_m": alt_m},
        "ctr_hits": [],
        "uas_hits": [],
        "notam_hits": [],
        "tma_hits": [],
        "tower_contacts": [],
        "risk_level": "LOW",
        "summary": "",
    }

    # CTR check (no altitude filter – CTR goes to GND)
    ctr_data = _load("ctr")
    for feat in ctr_data.get("features", []):
        if circle_intersects_feature(lon, lat, radius_m, feat):
            p = feat.get("properties", {})
            result["ctr_hits"].append(p)
            # Resolve tower contact
            name = (p.get("name") or p.get("arsp_name") or "").upper()
            icao = p.get("icao") or _resolve_icao(name)
            if icao and icao in TOWER_CONTACTS:
                contact = {**TOWER_CONTACTS[icao], "icao": icao}
                if contact not in result["tower_contacts"]:
                    result["tower_contacts"].append(contact)

    # UAS restriction zones (no altitude filter here – show all)
    uas_data = _load("uas_zones")
    for feat in uas_data.get("features", []):
        if circle_intersects_feature(lon, lat, radius_m, feat, alt_m):
            result["uas_hits"].append(feat.get("properties", {}))

    # NOTAM zones
    notam_data = _load("notam")
    for feat in notam_data.get("features", []):
        if circle_intersects_feature(lon, lat, radius_m, feat, alt_m):
            result["notam_hits"].append(feat.get("properties", {}))

    # TMA (with altitude filter)
    tma_data = _load("tma")
    for feat in tma_data.get("features", []):
        if circle_intersects_feature(lon, lat, radius_m, feat, alt_m):
            result["tma_hits"].append(feat.get("properties", {}))

    # Risk level
    ctr_count  = len(result["ctr_hits"])
    uas_count  = len(result["uas_hits"])
    notam_count = len(result["notam_hits"])

    if ctr_count > 0 or uas_count > 0:
        result["risk_level"] = "HIGH"
    elif notam_count > 0 or len(result["tma_hits"]) > 0:
        result["risk_level"] = "MEDIUM"
    else:
        result["risk_level"] = "LOW"

    # Summary
    parts = []
    if ctr_count:
        names = [h.get("name") or h.get("arsp_name") or "CTR" for h in result["ctr_hits"]]
        parts.append(f"Inside CTR: {', '.join(names)}")
    if uas_count:
        parts.append(f"{uas_count} UAS restriction zone(s)")
    if notam_count:
        parts.append(f"{notam_count} active NOTAM zone(s)")
    if result["tma_hits"]:
        parts.append(f"{len(result['tma_hits'])} TMA zone(s) at {alt_m} m")
    if not parts:
        parts.append("No conflicting airspace found")

    result["summary"] = ". ".join(parts) + "."
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
        from pypdf.generic import NameObject, create_string_object
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

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)

    return out


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
    fp.add_argument("--template", default="/home/vlad/Downloads/ANEXA1.pdf",
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
