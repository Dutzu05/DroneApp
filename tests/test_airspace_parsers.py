from __future__ import annotations

import unittest

from backend.airspace.parsers.restriction_parser import parse_restriction_feature_collection
from backend.airspace.parsers.wfs_parser import WfsParserError, parse_wfs_feature_collection


class AirspaceParsersTests(unittest.TestCase):
    def test_parse_wfs_feature_collection_requires_features(self):
        with self.assertRaises(WfsParserError):
            parse_wfs_feature_collection({'type': 'FeatureCollection', 'features': None})

    def test_parse_restriction_feature_collection_returns_features(self):
        features = parse_restriction_feature_collection(
            {
                'type': 'FeatureCollection',
                'features': [
                    {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Polygon', 'coordinates': []}}
                ],
            }
        )
        self.assertEqual(len(features), 1)
