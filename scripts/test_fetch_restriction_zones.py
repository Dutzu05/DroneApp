#!/usr/bin/env python3
"""
test_fetch_restriction_zones.py
────────────────────────────────
Unit tests for scripts/fetch_restriction_zones.py

Covers:
  1. parse_altitude_to_metres  – every real-world value from the live dataset
  2. enrich_feature            – properties added correctly, raw strings preserved
  3. convert                   – FeatureCollection structure, metadata fields
  4. write_geojson             – file write + idempotency (no re-write when unchanged)
  5. filterByAltitude logic    – mirrors the Dart isRelevantAtAltitude() rules
  6. Integration smoke-test    – load the real generated asset and spot-check zones

Run:
  python3 scripts/test_fetch_restriction_zones.py
  python3 scripts/test_fetch_restriction_zones.py -v    # verbose
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# ── make sure we can import the module regardless of cwd ──────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from fetch_restriction_zones import (
    FT_TO_M,
    convert,
    enrich_feature,
    parse_altitude_to_metres,
    write_geojson,
)

ASSET_PATH = SCRIPT_DIR.parent / "mobile_app" / "assets" / "restriction_zones.geojson"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Altitude parser
# ═══════════════════════════════════════════════════════════════════════════

class TestParseAltitudeToMetres(unittest.TestCase):
    """Every altitude variant actually present in the live ROMATSA dataset."""

    # ── ground ────────────────────────────────────────────────────────────

    def test_gnd_uppercase(self):
        self.assertEqual(parse_altitude_to_metres("GND"), 0.0)

    def test_gnd_lowercase(self):
        self.assertEqual(parse_altitude_to_metres("gnd"), 0.0)

    def test_ground_word(self):
        self.assertEqual(parse_altitude_to_metres("GROUND"), 0.0)

    def test_zero_string(self):
        self.assertEqual(parse_altitude_to_metres("0"), 0.0)

    def test_zero_m_agl(self):
        self.assertEqual(parse_altitude_to_metres("0m AGL"), 0.0)

    def test_zero_m_agl_spaced(self):
        self.assertEqual(parse_altitude_to_metres("0 M AGL"), 0.0)

    # ── metres AGL ────────────────────────────────────────────────────────

    def test_120m_agl_uppercase_nospace(self):
        self.assertEqual(parse_altitude_to_metres("120M AGL"), 120.0)

    def test_120m_agl_lowercase_space(self):
        self.assertEqual(parse_altitude_to_metres("120 m AGL"), 120.0)

    def test_120m_agl_lowercase_nospace(self):
        self.assertEqual(parse_altitude_to_metres("120m AGL"), 120.0)

    def test_600m_agl(self):
        self.assertEqual(parse_altitude_to_metres("600 M AGL"), 600.0)

    def test_1050m_agl(self):
        self.assertEqual(parse_altitude_to_metres("1050m AGL"), 1050.0)

    def test_1800m_agl(self):
        self.assertEqual(parse_altitude_to_metres("1800 M AGL"), 1800.0)

    def test_2750m_agl(self):
        self.assertEqual(parse_altitude_to_metres("2750m AGL"), 2750.0)

    # ── feet (various suffixes) ────────────────────────────────────────────

    def test_ft_amsl_no_space(self):
        result = parse_altitude_to_metres("2500FT AMSL")
        self.assertAlmostEqual(result, 2500 * FT_TO_M, places=1)

    def test_ft_amsl_space(self):
        result = parse_altitude_to_metres("5500 FT AMSL")
        self.assertAlmostEqual(result, 5500 * FT_TO_M, places=1)

    def test_ft_std(self):
        result = parse_altitude_to_metres("6500 FT STD")
        self.assertAlmostEqual(result, 6500 * FT_TO_M, places=1)

    def test_ft_std_no_space(self):
        result = parse_altitude_to_metres("9500FT STD")
        self.assertAlmostEqual(result, 9500 * FT_TO_M, places=1)

    def test_ft_qnh(self):
        result = parse_altitude_to_metres("4000FT QNH")
        self.assertAlmostEqual(result, 4000 * FT_TO_M, places=1)

    def test_ft_agl(self):
        result = parse_altitude_to_metres("8687 FT AGL")
        self.assertAlmostEqual(result, 8687 * FT_TO_M, places=1)

    def test_ft_plain_lowercase(self):
        # "6500ft" – no reference suffix
        result = parse_altitude_to_metres("6500ft")
        self.assertAlmostEqual(result, 6500 * FT_TO_M, places=1)

    def test_3000ft_amsl(self):
        result = parse_altitude_to_metres("3000 FT AMSL")
        self.assertAlmostEqual(result, 3000 * FT_TO_M, places=1)

    def test_2000ft_amsl(self):
        result = parse_altitude_to_metres("2000 FT AMSL")
        self.assertAlmostEqual(result, 2000 * FT_TO_M, places=1)

    # ── flight levels ─────────────────────────────────────────────────────

    def test_fl105(self):
        # FL105 = 10 500 ft → 10500 × 0.3048
        expected = round(105 * 100 * FT_TO_M, 1)
        self.assertEqual(parse_altitude_to_metres("FL105"), expected)

    def test_fl_lowercase(self):
        expected = round(75 * 100 * FT_TO_M, 1)
        self.assertEqual(parse_altitude_to_metres("fl75"), expected)

    def test_fl_with_space(self):
        expected = round(50 * 100 * FT_TO_M, 1)
        self.assertEqual(parse_altitude_to_metres("FL 50"), expected)

    # ── unknowns → None ───────────────────────────────────────────────────

    def test_by_notam(self):
        self.assertIsNone(parse_altitude_to_metres("BY NOTAM"))

    def test_by_notam_lowercase(self):
        self.assertIsNone(parse_altitude_to_metres("by notam"))

    def test_none_input(self):
        self.assertIsNone(parse_altitude_to_metres(None))

    def test_empty_string(self):
        # empty string is not a known keyword → None
        self.assertIsNone(parse_altitude_to_metres(""))

    def test_garbage(self):
        self.assertIsNone(parse_altitude_to_metres("UNKNOWN XYZ"))

    # ── numeric precision check ───────────────────────────────────────────

    def test_ft_to_m_constant(self):
        """1 foot must equal exactly 0.3048 m (ICAO standard)."""
        self.assertEqual(FT_TO_M, 0.3048)

    def test_2500ft_exact_value(self):
        self.assertEqual(parse_altitude_to_metres("2500FT AMSL"), 762.0)

    def test_6500ft_exact_value(self):
        self.assertEqual(parse_altitude_to_metres("6500 FT STD"), 1981.2)


# ═══════════════════════════════════════════════════════════════════════════
# 2. enrich_feature
# ═══════════════════════════════════════════════════════════════════════════

def _make_feature(lower_lim: str, upper_lim: str, zone_id: str = "RZ TEST") -> dict:
    """Helper – minimal GeoJSON Feature for testing."""
    return {
        "type": "Feature",
        "id": f"zone.{zone_id}",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[26.0, 44.0], [26.1, 44.0], [26.1, 44.1], [26.0, 44.0]]],
        },
        "properties": {
            "zone_id": zone_id,
            "lower_lim": lower_lim,
            "upper_lim": upper_lim,
            "contact": "test@example.com",
            "status": "RESTRICTED",
        },
    }


class TestEnrichFeature(unittest.TestCase):

    def test_raw_strings_preserved(self):
        feat = enrich_feature(_make_feature("GND", "120M AGL"))
        self.assertEqual(feat["properties"]["lower_lim_raw"], "GND")
        self.assertEqual(feat["properties"]["upper_lim_raw"], "120M AGL")

    def test_numeric_lower_gnd(self):
        feat = enrich_feature(_make_feature("GND", "120M AGL"))
        self.assertEqual(feat["properties"]["lower_limit_m"], 0.0)

    def test_numeric_upper_120m(self):
        feat = enrich_feature(_make_feature("GND", "120M AGL"))
        self.assertEqual(feat["properties"]["upper_limit_m"], 120.0)

    def test_feet_conversion_in_feature(self):
        feat = enrich_feature(_make_feature("120M AGL", "2500FT AMSL"))
        self.assertEqual(feat["properties"]["lower_limit_m"], 120.0)
        self.assertAlmostEqual(feat["properties"]["upper_limit_m"], 762.0, places=1)

    def test_by_notam_upper_is_none(self):
        feat = enrich_feature(_make_feature("GND", "BY NOTAM"))
        self.assertEqual(feat["properties"]["lower_limit_m"], 0.0)
        self.assertIsNone(feat["properties"]["upper_limit_m"])

    def test_original_lower_lim_field_kept(self):
        """The original lower_lim / upper_lim fields must NOT be removed."""
        feat = enrich_feature(_make_feature("GND", "120M AGL"))
        self.assertIn("lower_lim", feat["properties"])
        self.assertIn("upper_lim", feat["properties"])

    def test_geometry_untouched(self):
        original = _make_feature("GND", "120M AGL")
        enriched = enrich_feature(original)
        self.assertEqual(enriched["geometry"]["type"], "Polygon")
        self.assertEqual(len(enriched["geometry"]["coordinates"][0]), 4)


# ═══════════════════════════════════════════════════════════════════════════
# 3. convert (FeatureCollection)
# ═══════════════════════════════════════════════════════════════════════════

class TestConvert(unittest.TestCase):

    def _raw_fc(self, n: int = 3) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                _make_feature("GND", "120M AGL", f"RZ {i:04d}") for i in range(n)
            ],
        }

    def test_output_type(self):
        result = convert(self._raw_fc())
        self.assertEqual(result["type"], "FeatureCollection")

    def test_feature_count_preserved(self):
        result = convert(self._raw_fc(7))
        self.assertEqual(len(result["features"]), 7)

    def test_metadata_present(self):
        result = convert(self._raw_fc())
        self.assertIn("metadata", result)

    def test_metadata_source_url(self):
        result = convert(self._raw_fc())
        self.assertIn("romatsa.ro", result["metadata"]["source"])

    def test_metadata_fetched_at_is_iso8601(self):
        from datetime import datetime
        result = convert(self._raw_fc())
        ts = result["metadata"]["fetched_at"]
        # Should parse without raising
        dt = datetime.fromisoformat(ts)
        self.assertIsNotNone(dt)

    def test_metadata_total_zones(self):
        result = convert(self._raw_fc(5))
        self.assertEqual(result["metadata"]["total_zones"], 5)

    def test_all_features_enriched(self):
        result = convert(self._raw_fc(4))
        for feat in result["features"]:
            self.assertIn("lower_limit_m", feat["properties"])
            self.assertIn("upper_limit_m", feat["properties"])
            self.assertIn("lower_lim_raw", feat["properties"])
            self.assertIn("upper_lim_raw", feat["properties"])


# ═══════════════════════════════════════════════════════════════════════════
# 4. write_geojson – idempotency
# ═══════════════════════════════════════════════════════════════════════════

class TestWriteGeojson(unittest.TestCase):

    def _patch_output(self, tmp_path: Path):
        """Temporarily override OUTPUT_PATH used inside write_geojson."""
        import fetch_restriction_zones as mod
        self._original = mod.OUTPUT_PATH
        mod.OUTPUT_PATH = tmp_path
        return tmp_path

    def _restore_output(self):
        import fetch_restriction_zones as mod
        mod.OUTPUT_PATH = self._original

    def _sample_geojson(self) -> dict:
        return convert({"type": "FeatureCollection", "features": [
            _make_feature("GND", "120M AGL"),
        ]})

    def test_first_write_returns_true(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.geojson"
            self._patch_output(out)
            try:
                result = write_geojson(self._sample_geojson())
                self.assertTrue(result)
            finally:
                self._restore_output()

    def test_file_is_valid_json_after_write(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.geojson"
            self._patch_output(out)
            try:
                write_geojson(self._sample_geojson())
                data = json.loads(out.read_bytes())
                self.assertEqual(data["type"], "FeatureCollection")
            finally:
                self._restore_output()

    def test_same_content_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.geojson"
            self._patch_output(out)
            try:
                geojson = self._sample_geojson()
                write_geojson(geojson)
                result = write_geojson(geojson)  # identical content
                self.assertFalse(result)
            finally:
                self._restore_output()

    def test_changed_content_returns_true(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.geojson"
            self._patch_output(out)
            try:
                geojson_v1 = convert({"type": "FeatureCollection", "features": [
                    _make_feature("GND", "120M AGL"),
                ]})
                geojson_v2 = convert({"type": "FeatureCollection", "features": [
                    _make_feature("GND", "120M AGL"),
                    _make_feature("120M AGL", "2500FT AMSL", "RZ 9999"),
                ]})
                write_geojson(geojson_v1)
                result = write_geojson(geojson_v2)
                self.assertTrue(result)
            finally:
                self._restore_output()

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "deep" / "nested" / "out.geojson"
            self._patch_output(out)
            try:
                write_geojson(self._sample_geojson())
                self.assertTrue(out.exists())
            finally:
                self._restore_output()


# ═══════════════════════════════════════════════════════════════════════════
# 5. Altitude-based filtering logic (mirrors Dart isRelevantAtAltitude)
# ═══════════════════════════════════════════════════════════════════════════

def _is_relevant(lower_m: float | None, upper_m: float | None, flight_m: float) -> bool:
    """
    Pure-Python mirror of the Dart RestrictionZone.isRelevantAtAltitude() method.
    Unknown limits → always relevant.
    """
    if lower_m is None or upper_m is None:
        return True
    return lower_m <= flight_m <= upper_m


class TestAltitudeFilterLogic(unittest.TestCase):
    """
    Tests for the filtering logic that decides which zones are shown
    to the user at a given flight altitude.
    """

    # ── zone: GND → 120m ──────────────────────────────────────────────────

    def test_at_ground_inside_gnd_120(self):
        self.assertTrue(_is_relevant(0, 120, 0))

    def test_at_50m_inside_gnd_120(self):
        self.assertTrue(_is_relevant(0, 120, 50))

    def test_at_120m_boundary_gnd_120(self):
        self.assertTrue(_is_relevant(0, 120, 120))

    def test_at_121m_above_gnd_120(self):
        self.assertFalse(_is_relevant(0, 120, 121))

    def test_at_200m_above_gnd_120(self):
        self.assertFalse(_is_relevant(0, 120, 200))

    # ── zone: 120m → 762m (2500ft AMSL) ──────────────────────────────────

    def test_at_50m_below_120_762_zone(self):
        """Flight at 50m is BELOW this zone – should NOT see it."""
        self.assertFalse(_is_relevant(120, 762, 50))

    def test_at_119m_just_below_120_762_zone(self):
        self.assertFalse(_is_relevant(120, 762, 119))

    def test_at_120m_on_boundary_of_120_762(self):
        self.assertTrue(_is_relevant(120, 762, 120))

    def test_at_400m_inside_120_762(self):
        self.assertTrue(_is_relevant(120, 762, 400))

    def test_at_762m_upper_boundary(self):
        self.assertTrue(_is_relevant(120, 762, 762))

    def test_at_763m_above_762(self):
        self.assertFalse(_is_relevant(120, 762, 763))

    # ── zone: BY NOTAM (None upper) ───────────────────────────────────────

    def test_by_notam_shown_at_50m(self):
        self.assertTrue(_is_relevant(0, None, 50))

    def test_by_notam_shown_at_0m(self):
        self.assertTrue(_is_relevant(0, None, 0))

    def test_none_lower_always_shown(self):
        self.assertTrue(_is_relevant(None, 120, 50))

    def test_both_none_always_shown(self):
        self.assertTrue(_is_relevant(None, None, 9999))

    # ── edge cases ────────────────────────────────────────────────────────

    def test_exact_lower_boundary_inclusive(self):
        self.assertTrue(_is_relevant(100, 200, 100))

    def test_exact_upper_boundary_inclusive(self):
        self.assertTrue(_is_relevant(100, 200, 200))

    def test_one_metre_below_lower(self):
        self.assertFalse(_is_relevant(100, 200, 99))

    def test_one_metre_above_upper(self):
        self.assertFalse(_is_relevant(100, 200, 201))


# ═══════════════════════════════════════════════════════════════════════════
# 6. Integration – load the real generated asset and spot-check
# ═══════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(ASSET_PATH.exists(), "Generated asset not found – run fetch_restriction_zones.py first")
class TestRealAsset(unittest.TestCase):
    """
    Loads the actual restriction_zones.geojson that the Flutter app uses and
    validates structure, completeness, and specific known zones.
    """

    @classmethod
    def setUpClass(cls):
        with open(ASSET_PATH, encoding="utf-8") as f:
            cls.data = json.load(f)
        cls.features = cls.data["features"]
        cls.by_id = {
            f["properties"]["zone_id"]: f["properties"]
            for f in cls.features
        }

    # ── top-level structure ───────────────────────────────────────────────

    def test_type_is_feature_collection(self):
        self.assertEqual(self.data["type"], "FeatureCollection")

    def test_has_metadata(self):
        self.assertIn("metadata", self.data)

    def test_metadata_has_source(self):
        self.assertIn("source", self.data["metadata"])

    def test_metadata_has_fetched_at(self):
        self.assertIn("fetched_at", self.data["metadata"])

    def test_total_zones_in_metadata_matches_features(self):
        self.assertEqual(self.data["metadata"]["total_zones"], len(self.features))

    def test_at_least_1000_zones(self):
        """Sanity: ROMATSA currently publishes >1000 zones."""
        self.assertGreater(len(self.features), 1000)

    # ── per-feature required fields ───────────────────────────────────────

    def test_every_feature_has_zone_id(self):
        missing = [
            f.get("id", "?")
            for f in self.features
            if not f["properties"].get("zone_id")
        ]
        self.assertEqual(missing, [], f"Features missing zone_id: {missing[:5]}")

    def test_every_feature_has_lower_lim_raw(self):
        missing = [
            f["properties"].get("zone_id", "?")
            for f in self.features
            if "lower_lim_raw" not in f["properties"]
        ]
        self.assertEqual(missing, [])

    def test_every_feature_has_upper_lim_raw(self):
        missing = [
            f["properties"].get("zone_id", "?")
            for f in self.features
            if "upper_lim_raw" not in f["properties"]
        ]
        self.assertEqual(missing, [])

    def test_every_feature_has_lower_limit_m_key(self):
        missing = [
            f["properties"].get("zone_id", "?")
            for f in self.features
            if "lower_limit_m" not in f["properties"]
        ]
        self.assertEqual(missing, [])

    def test_every_feature_has_upper_limit_m_key(self):
        missing = [
            f["properties"].get("zone_id", "?")
            for f in self.features
            if "upper_limit_m" not in f["properties"]
        ]
        self.assertEqual(missing, [])

    def test_every_feature_geometry_is_polygon(self):
        non_polygon = [
            f["properties"].get("zone_id", "?")
            for f in self.features
            if f["geometry"]["type"] != "Polygon"
        ]
        self.assertEqual(non_polygon, [])

    def test_every_polygon_has_coordinates(self):
        for feat in self.features:
            coords = feat["geometry"]["coordinates"]
            self.assertGreater(len(coords), 0)
            self.assertGreater(len(coords[0]), 2, "Ring must have > 2 points")

    # ── altitude plausibility ─────────────────────────────────────────────

    def test_lower_limit_m_is_float_or_none(self):
        bad = [
            f["properties"]["zone_id"]
            for f in self.features
            if f["properties"]["lower_limit_m"] is not None
            and not isinstance(f["properties"]["lower_limit_m"], (int, float))
        ]
        self.assertEqual(bad, [])

    def test_upper_limit_m_is_float_or_none(self):
        bad = [
            f["properties"]["zone_id"]
            for f in self.features
            if f["properties"]["upper_limit_m"] is not None
            and not isinstance(f["properties"]["upper_limit_m"], (int, float))
        ]
        self.assertEqual(bad, [])

    def test_lower_limit_never_negative(self):
        neg = [
            f["properties"]["zone_id"]
            for f in self.features
            if (f["properties"]["lower_limit_m"] or 0) < 0
        ]
        self.assertEqual(neg, [])

    def test_upper_limit_never_negative(self):
        neg = [
            f["properties"]["zone_id"]
            for f in self.features
            if (f["properties"]["upper_limit_m"] or 0) < 0
        ]
        self.assertEqual(neg, [])

    def test_when_both_known_lower_lte_upper(self):
        """Lower limit must not exceed upper limit."""
        bad = [
            f["properties"]["zone_id"]
            for f in self.features
            if f["properties"]["lower_limit_m"] is not None
            and f["properties"]["upper_limit_m"] is not None
            and f["properties"]["lower_limit_m"] > f["properties"]["upper_limit_m"]
        ]
        self.assertEqual(bad, [], f"Zones with lower > upper: {bad[:5]}")

    def test_majority_have_numeric_altitudes(self):
        """At least 95 % of zones should have parseable altitude values."""
        with_alt = sum(
            1 for f in self.features
            if f["properties"].get("upper_limit_m") is not None
        )
        pct = with_alt / len(self.features) * 100
        self.assertGreater(pct, 95, f"Only {pct:.1f}% zones have numeric altitudes")

    # ── known zone spot-checks (data as of Nov 2025 validity date) ────────

    def test_rz_1001_exists(self):
        self.assertIn("RZ 1001", self.by_id, "Zone RZ 1001 not found in asset")

    def test_rz_1001_lower_is_gnd(self):
        props = self.by_id.get("RZ 1001", {})
        self.assertEqual(props.get("lower_limit_m"), 0.0)

    def test_rz_1001_upper_is_120m(self):
        props = self.by_id.get("RZ 1001", {})
        self.assertEqual(props.get("upper_limit_m"), 120.0)

    def test_rz_1002_lower_is_120m(self):
        """RZ 1002 starts at 120m AGL – should NOT appear at 50m flight."""
        props = self.by_id.get("RZ 1002", {})
        self.assertEqual(props.get("lower_limit_m"), 120.0)

    def test_rz_1002_not_relevant_at_50m(self):
        props = self.by_id.get("RZ 1002", {})
        lower = props.get("lower_limit_m")
        upper = props.get("upper_limit_m")
        self.assertFalse(_is_relevant(lower, upper, 50))

    def test_rz_1001_relevant_at_50m(self):
        props = self.by_id.get("RZ 1001", {})
        lower = props.get("lower_limit_m")
        upper = props.get("upper_limit_m")
        self.assertTrue(_is_relevant(lower, upper, 50))

    # ── coordinates are in Romania ────────────────────────────────────────

    def test_coordinates_roughly_in_romania(self):
        """
        Romania bounding box (approx):
          lon: 20.2 – 30.0
          lat: 43.6 – 48.3
        All polygon vertices should fall within ±1 degree of this box.
        """
        lon_min, lon_max = 19.2, 31.0
        lat_min, lat_max = 42.6, 49.3
        outliers = []
        for feat in self.features:
            for ring in feat["geometry"]["coordinates"]:
                for (lon, lat) in ring:
                    if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
                        outliers.append((feat["properties"]["zone_id"], lon, lat))
        self.assertEqual(
            outliers, [],
            f"Coordinates outside Romania bounding box: {outliers[:3]}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
