#!/usr/bin/env python3
"""
fetch_restriction_zones.py
──────────────────────────
Downloads the ROMATSA "Restricted_zones_for_UAS" GeoJSON, normalises every
altitude value to **metres AGL**, and writes an enriched GeoJSON that the
Flutter app can consume directly (including altitude-based filtering).

Output file: ../mobile_app/assets/restriction_zones.geojson

Usage
─────
  # one-shot download + convert
  python scripts/fetch_restriction_zones.py

  # auto-refresh: poll every N seconds and overwrite only when content changes
  python scripts/fetch_restriction_zones.py --watch 300   # every 5 min

Altitude conversion rules
─────────────────────────
  GND / 0m AGL          → 0
  120M AGL / 120 m AGL  → 120    (metres AGL as-is)
  2500FT AMSL            → ≈762   (feet → metres; AMSL kept as approximation)
  FL105                  → ≈3200  (flight level × 30.48)
  6500 FT STD            → ≈1981  (feet → metres)
  BY NOTAM               → None   (unknown until published)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────── constants ────────────────────────────────────

ROMATSA_URL = (
    "https://flightplan.romatsa.ro/init/static/zone_restrictionate_uav.json"
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUTPUT_PATH = PROJECT_ROOT / "mobile_app" / "assets" / "restriction_zones.geojson"

FT_TO_M = 0.3048

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)


# ──────────────────────────── altitude parsing ─────────────────────────────

_RE_METRES = re.compile(
    r"^\s*(?P<val>[\d]+(?:\.\d+)?)\s*m?\s*(?:AGL)?\s*$", re.IGNORECASE
)
_RE_FEET = re.compile(
    r"^\s*(?P<val>[\d]+(?:\.\d+)?)\s*(?:FT|FEET)\s*(?:AGL|AMSL|STD|QNH)?\s*$",
    re.IGNORECASE,
)
_RE_FL = re.compile(r"^\s*FL\s*(?P<val>\d+)\s*$", re.IGNORECASE)


def parse_altitude_to_metres(raw: str | None) -> float | None:
    """
    Convert a ROMATSA altitude string to metres (AGL approximation).

    Returns *None* when the value cannot be determined (e.g. "BY NOTAM").
    """
    if raw is None:
        return None

    raw = raw.strip()

    # GND / ground
    if raw.upper() in ("GND", "GROUND", "0", "0M AGL", "0 M AGL"):
        return 0.0

    # Pure metres  – "120M AGL", "120 m AGL", "1050m AGL"
    m = _RE_METRES.match(raw)
    if m:
        return float(m.group("val"))

    # Feet – "2500FT AMSL", "6500 FT STD", etc.
    m = _RE_FEET.match(raw)
    if m:
        return round(float(m.group("val")) * FT_TO_M, 1)

    # Flight level – "FL105" → 10 500 ft → metres
    m = _RE_FL.match(raw)
    if m:
        return round(int(m.group("val")) * 100 * FT_TO_M, 1)

    # Unknown / dynamic – "BY NOTAM" etc.
    return None


# ──────────────────────────── download ─────────────────────────────────────


def _build_ssl_context() -> ssl.SSLContext:
    """
    ROMATSA's server uses a weak DH key that OpenSSL 3 rejects by default.
    We create a context that tolerates it – acceptable because we only read
    public aviation data.
    """
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:!DH")
    return ctx


def download_romatsa_json() -> dict:
    """Download and return the parsed JSON from ROMATSA."""
    log.info("Downloading %s …", ROMATSA_URL)
    req = urllib.request.Request(
        ROMATSA_URL,
        headers={"User-Agent": "DroneApp-ZoneFetcher/1.0"},
    )
    ctx = _build_ssl_context()
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        raw = resp.read()
    log.info("Downloaded %d bytes", len(raw))
    return json.loads(raw)


# ──────────────────────────── transform ────────────────────────────────────


def enrich_feature(feature: dict) -> dict:
    """
    Add computed numeric altitude fields to each Feature's properties
    so the Flutter app can filter without re-parsing strings.

    Added properties
    ────────────────
      lower_limit_m   float | null   lower limit in metres
      upper_limit_m   float | null   upper limit in metres
      lower_lim_raw   str            original string (kept for display)
      upper_lim_raw   str            original string (kept for display)
    """
    props = feature.get("properties", {})

    lower_raw = props.get("lower_lim", "")
    upper_raw = props.get("upper_lim", "")

    props["lower_lim_raw"] = lower_raw
    props["upper_lim_raw"] = upper_raw
    props["lower_limit_m"] = parse_altitude_to_metres(lower_raw)
    props["upper_limit_m"] = parse_altitude_to_metres(upper_raw)

    feature["properties"] = props
    return feature


def convert(source: dict) -> dict:
    """
    Take the raw ROMATSA FeatureCollection and return an enriched GeoJSON
    ready for the Flutter app.
    """
    features = [enrich_feature(f) for f in source.get("features", [])]

    return {
        "type": "FeatureCollection",
        "metadata": {
            "source": ROMATSA_URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "total_zones": len(features),
            "description": (
                "ROMATSA UAS restriction zones with normalised altitude "
                "values in metres.  Use lower_limit_m / upper_limit_m for "
                "altitude-based filtering."
            ),
        },
        "features": features,
    }


# ──────────────────────────── persistence ──────────────────────────────────


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_geojson(geojson: dict) -> bool:
    """
    Write to OUTPUT_PATH.  Returns True if the file was actually updated
    (content differs from what was already on disk).
    """
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    new_bytes = json.dumps(geojson, ensure_ascii=False).encode("utf-8")
    new_hash = _content_hash(new_bytes)

    if OUTPUT_PATH.exists():
        old_hash = _content_hash(OUTPUT_PATH.read_bytes())
        if old_hash == new_hash:
            log.info("No changes detected – skipping write.")
            return False

    OUTPUT_PATH.write_bytes(new_bytes)
    log.info("Wrote %s  (%d bytes)", OUTPUT_PATH, len(new_bytes))
    return True


# ──────────────────────────── main ─────────────────────────────────────────


def run_once() -> bool:
    """Download → convert → write.  Returns True if file was updated."""
    raw = download_romatsa_json()
    geojson = convert(raw)
    updated = write_geojson(geojson)

    stats = summarise(geojson)
    log.info(
        "Zones: %d total | %d with numeric altitudes | %d 'BY NOTAM'",
        stats["total"],
        stats["with_altitude"],
        stats["by_notam"],
    )
    return updated


def summarise(geojson: dict) -> dict:
    total = len(geojson["features"])
    with_alt = sum(
        1
        for f in geojson["features"]
        if f["properties"].get("upper_limit_m") is not None
    )
    by_notam = sum(
        1
        for f in geojson["features"]
        if (f["properties"].get("upper_lim_raw") or "").upper() == "BY NOTAM"
    )
    return {"total": total, "with_altitude": with_alt, "by_notam": by_notam}


def watch_loop(interval: int) -> None:
    """Poll ROMATSA every *interval* seconds; overwrite only on changes."""
    log.info("Watch mode – polling every %d s.  Press Ctrl-C to stop.", interval)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Error during fetch – will retry next cycle")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch & convert ROMATSA UAS restriction zones to GeoJSON."
    )
    parser.add_argument(
        "--watch",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Poll interval in seconds.  0 = one-shot (default).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Override default output path.",
    )
    args = parser.parse_args()

    if args.output:
        global OUTPUT_PATH
        OUTPUT_PATH = Path(args.output).resolve()

    if args.watch > 0:
        watch_loop(args.watch)
    else:
        run_once()


if __name__ == "__main__":
    main()
