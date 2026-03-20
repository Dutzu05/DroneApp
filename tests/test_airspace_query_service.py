from __future__ import annotations

import unittest

from backend.airspace.services.airspace_query_service import AirspaceQueryService


class _ZoneRepoStub:
    def __init__(self, zones):
        self.zones = zones

    def zones_in_bbox(self, bbox, *, categories=None):
        return list(self.zones)

    def zones_near_point(self, *, lat, lon, radius_km, categories=None):
        return list(self.zones)

    def zones_for_point(self, *, lat, lon, alt_m):
        return list(self.zones)


class AirspaceQueryServiceTests(unittest.TestCase):
    def test_get_zones_near_normalizes_web_mercator_geometry(self):
        service = AirspaceQueryService(
            _ZoneRepoStub(
                [
                    {
                        'zone_id': 'tma-bucharest',
                        'source': 'romatsa_wfs_tma',
                        'name': 'Bucharest TMA',
                        'category': 'tma',
                        'lower_altitude_m': 300.0,
                        'upper_altitude_m': 2400.0,
                        'distance_m': 900.0,
                        'geometry': {
                            'type': 'Polygon',
                            'coordinates': [[
                                [2894303.63, 5543147.20],
                                [2898756.41, 5543147.20],
                                [2898756.41, 5546294.78],
                                [2894303.63, 5546294.78],
                            ]],
                        },
                    }
                ]
            )
        )

        response = service.get_zones_near(lat=44.5, lon=26.0, radius_km=5.0, categories={'tma'})

        self.assertEqual(response['count'], 1)
        first = response['zones'][0]['geometry']['coordinates'][0][0]
        self.assertAlmostEqual(first[0], 26.0, places=1)
        self.assertAlmostEqual(first[1], 44.5, places=1)

    def test_get_zones_near_drops_invalid_geometry(self):
        service = AirspaceQueryService(
            _ZoneRepoStub(
                [
                    {
                        'zone_id': 'broken-zone',
                        'source': 'romatsa_wfs_tma',
                        'name': 'Broken',
                        'category': 'tma',
                        'lower_altitude_m': 300.0,
                        'upper_altitude_m': 2400.0,
                        'distance_m': 900.0,
                        'geometry': {
                            'type': 'Polygon',
                            'coordinates': [[[99999999.0, 99999999.0], [99999999.0, 99999999.0], [99999999.0, 99999999.0]]],
                        },
                    }
                ]
            )
        )

        response = service.get_zones_near(lat=44.5, lon=26.0, radius_km=5.0, categories={'tma'})

        self.assertEqual(response['count'], 0)
        self.assertEqual(response['zones'], [])


if __name__ == '__main__':
    unittest.main()
