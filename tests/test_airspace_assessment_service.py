from __future__ import annotations

import unittest

from backend.airspace.services.flight_area_assessment_service import (
    FlightAreaAssessmentService,
    area_to_geometry,
)


def _polygon() -> dict[str, object]:
    return {
        'type': 'Polygon',
        'coordinates': [[[23.0, 46.0], [23.1, 46.0], [23.1, 46.1], [23.0, 46.1], [23.0, 46.0]]],
    }


class _FakeZoneRepo:
    def __init__(self, zones):
        self._zones = zones

    def zones_for_geometry(self, *, geometry_geojson, alt_m):
        return list(self._zones)

    def zones_for_point(self, *, lat, lon, alt_m):
        return list(self._zones)

    def zones_for_route(self, *, path):
        return list(self._zones)


class FlightAreaAssessmentServiceTests(unittest.TestCase):
    def test_area_to_geometry_closes_polygon(self):
        geometry = area_to_geometry({'kind': 'polygon', 'points': [[23.0, 46.0], [23.1, 46.0], [23.1, 46.1]]})
        self.assertEqual(geometry['type'], 'Polygon')
        self.assertEqual(geometry['coordinates'][0][0], geometry['coordinates'][0][-1])

    def test_assess_area_groups_hits_and_contacts(self):
        repo = _FakeZoneRepo(
            [
                {
                    'zone_id': 'romatsa_wfs_ctr_LRCL',
                    'source': 'romatsa_wfs_ctr',
                    'name': 'CLUJ CTR',
                    'category': 'ctr',
                    'lower_altitude_m': 0.0,
                    'upper_altitude_m': 120.0,
                    'metadata': {'properties': {'name': 'CLUJ CTR'}},
                    'geometry': _polygon(),
                },
                {
                    'zone_id': 'restriction_zones_json_RZ_1',
                    'source': 'restriction_zones_json',
                    'name': 'RZ 1',
                    'category': 'restricted',
                    'lower_altitude_m': 0.0,
                    'upper_altitude_m': 120.0,
                    'metadata': {'properties': {'zone_id': 'RZ 1'}},
                    'geometry': _polygon(),
                },
            ]
        )
        service = FlightAreaAssessmentService(zone_repo=repo)
        result = service.assess_area(
            area={'kind': 'polygon', 'points': [[23.0, 46.0], [23.1, 46.0], [23.1, 46.1]]},
            alt_m=120,
            tower_contacts={'LRCL': {'city': 'Cluj-Napoca', 'email': 'tower@example.com'}},
            resolve_icao=lambda name: 'LRCL' if 'CLUJ' in name else None,
        )
        self.assertEqual(result['risk_level'], 'HIGH')
        self.assertTrue(result['approval_required'])
        self.assertTrue(result['approval_possible'])
        self.assertEqual(result['eligibility_status'], 'manual_review')
        self.assertEqual(len(result['ctr_hits']), 1)
        self.assertEqual(len(result['uas_hits']), 1)
        self.assertEqual(result['tower_contacts'][0]['icao'], 'LRCL')

    def test_assess_area_marks_prohibited_hits_as_blocked(self):
        repo = _FakeZoneRepo(
            [
                {
                    'zone_id': 'restriction_zones_json_RZ_9',
                    'source': 'restriction_zones_json',
                    'name': 'RZ 9',
                    'category': 'restricted',
                    'lower_altitude_m': 0.0,
                    'upper_altitude_m': 120.0,
                    'metadata': {'properties': {'zone_id': 'RZ 9', 'status': 'PROHIBITED'}},
                    'geometry': _polygon(),
                }
            ]
        )
        service = FlightAreaAssessmentService(zone_repo=repo)

        result = service.assess_area(
            area={'kind': 'polygon', 'points': [[23.0, 46.0], [23.1, 46.0], [23.1, 46.1]]},
            alt_m=120,
            tower_contacts={},
            resolve_icao=lambda name: None,
        )

        self.assertEqual(result['eligibility_status'], 'blocked')
        self.assertFalse(result['approval_required'])
        self.assertFalse(result['approval_possible'])
        self.assertEqual(len(result['prohibited_hits']), 1)

    def test_crosscheck_groups_results_by_legacy_layer(self):
        repo = _FakeZoneRepo(
            [
                {
                    'zone_id': 'notam_wfs_N1',
                    'source': 'notam_wfs',
                    'name': 'NOTAM N1',
                    'category': 'temporary_restriction',
                    'lower_altitude_m': 0.0,
                    'upper_altitude_m': 120.0,
                    'metadata': {'properties': {'notam_id': 'N1'}},
                    'geometry': _polygon(),
                }
            ]
        )
        service = FlightAreaAssessmentService(zone_repo=repo)
        result = service.crosscheck_point(lon=23.5, lat=46.7, alt_m=100)
        self.assertIn('notam', result)
        self.assertEqual(result['notam'][0]['notam_id'], 'N1')
