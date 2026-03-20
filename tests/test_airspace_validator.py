from __future__ import annotations

import unittest

from backend.airspace.validators.geometry_validator import GeometryValidationError, ensure_polygon_closed, validate_geometry


class AirspaceGeometryValidatorTests(unittest.TestCase):
    def test_validate_geometry_closes_polygon(self):
        geometry = validate_geometry(
            {
                'type': 'Polygon',
                'coordinates': [[[23.0, 46.0], [23.1, 46.0], [23.1, 46.1], [23.0, 46.1]]],
            }
        )
        self.assertTrue(ensure_polygon_closed(geometry))

    def test_validate_geometry_rejects_points(self):
        with self.assertRaises(GeometryValidationError):
            validate_geometry({'type': 'Point', 'coordinates': [23.0, 46.0]})

    def test_validate_geometry_converts_web_mercator_polygon_to_wgs84(self):
        geometry = validate_geometry(
            {
                'type': 'Polygon',
                'coordinates': [[
                    [2894303.63, 5543147.20],
                    [2898756.41, 5543147.20],
                    [2898756.41, 5546294.78],
                    [2894303.63, 5546294.78],
                ]],
            }
        )

        first = geometry['coordinates'][0][0]
        self.assertAlmostEqual(first[0], 26.0, places=1)
        self.assertAlmostEqual(first[1], 44.5, places=1)
        self.assertTrue(ensure_polygon_closed(geometry))
