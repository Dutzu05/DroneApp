from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.airspace.normalizers.zone_normalizer import normalize_feature, parse_altitude_to_metres


class AirspaceNormalizerTests(unittest.TestCase):
    def test_parse_altitude_handles_feet_and_flight_levels(self):
        self.assertEqual(parse_altitude_to_metres('GND'), 0.0)
        self.assertEqual(parse_altitude_to_metres('120M AGL'), 120.0)
        self.assertEqual(parse_altitude_to_metres('2500FT AMSL'), 762.0)
        self.assertEqual(parse_altitude_to_metres('FL105'), 3200.4)
        self.assertIsNone(parse_altitude_to_metres('BY NOTAM'))

    def test_normalize_feature_builds_unified_zone(self):
        zone = normalize_feature(
            source='restriction_zones_json',
            version_id='00000000-0000-0000-0000-000000000001',
            fetched_at=datetime(2026, 3, 16, tzinfo=timezone.utc),
            feature={
                'type': 'Feature',
                'properties': {
                    'zone_id': 'RZ 1001',
                    'name': 'Restricted Area',
                    'lower_lim': 'GND',
                    'upper_lim': '120M AGL',
                },
                'geometry': {
                    'type': 'Polygon',
                    'coordinates': [[[23.0, 46.0], [23.1, 46.0], [23.1, 46.1], [23.0, 46.1]]],
                },
            },
        )
        self.assertEqual(zone.zone_id, 'restriction_zones_json_RZ_1001')
        self.assertEqual(zone.category, 'restricted')
        self.assertEqual(zone.lower_altitude_m, 0.0)
        self.assertEqual(zone.upper_altitude_m, 120.0)
        self.assertEqual(zone.geometry['coordinates'][0][0], zone.geometry['coordinates'][0][-1])
