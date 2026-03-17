from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.drone_tracking.services.mock_telemetry_service import DroneMockTelemetryService


class _RepoStub:
    def __init__(self):
        self.devices = []
        self.telemetry = []

    def list_mock_candidate_plans(self, *, include_upcoming: bool = True):
        return [
            {
                'public_id': 'FP-ONGOING-1',
                'owner_user_id': 1,
                'owner_email': 'pilot@example.com',
                'owner_display_name': 'Pilot',
                'location_name': 'Cluj',
                'selected_twr': 'LRCL',
                'area_kind': 'circle',
                'center_lon': 23.598,
                'center_lat': 46.771,
                'radius_m': 120,
                'area_geojson': {},
                'max_altitude_m': 120,
                'runtime_state': 'ongoing',
                'drone_id': None,
                'drone_label': None,
            },
            {
                'public_id': 'FP-UPCOMING-1',
                'owner_user_id': 2,
                'owner_email': 'pilot2@example.com',
                'owner_display_name': 'Pilot 2',
                'location_name': 'Brasov',
                'selected_twr': 'LRBV',
                'area_kind': 'polygon',
                'area_geojson': {
                    'type': 'Polygon',
                    'coordinates': [[[25.59, 45.64], [25.60, 45.64], [25.60, 45.65], [25.59, 45.65], [25.59, 45.64]]],
                },
                'max_altitude_m': 90,
                'runtime_state': 'upcoming',
                'drone_id': None,
                'drone_label': None,
            },
        ]

    def upsert_drone_device(self, **kwargs):
        kwargs['id'] = len(self.devices) + 1
        self.devices.append(kwargs)
        return kwargs

    def insert_telemetry(self, **kwargs):
        self.telemetry.append(kwargs)
        return kwargs

    def now_utc(self):
        return datetime(2026, 3, 17, 10, 0, 0, tzinfo=timezone.utc)


class MockTelemetryServiceTests(unittest.TestCase):
    def test_generate_tick_creates_mock_snapshots_for_ongoing_and_upcoming_plans(self):
        repo = _RepoStub()
        service = DroneMockTelemetryService(repo)

        snapshots = service.generate_tick()

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(len(repo.devices), 2)
        self.assertEqual(len(repo.telemetry), 2)
        ongoing = next(item for item in snapshots if item['flight_plan_public_id'] == 'FP-ONGOING-1')
        upcoming = next(item for item in snapshots if item['flight_plan_public_id'] == 'FP-UPCOMING-1')
        self.assertEqual(ongoing['status'], 'flying')
        self.assertGreater(ongoing['altitude'], 10)
        self.assertGreater(ongoing['speed'], 5)
        self.assertEqual(upcoming['status'], 'ready')
        self.assertLess(upcoming['altitude'], 10)
        self.assertLess(upcoming['speed'], 2)
        self.assertTrue(str(ongoing['drone_id']).startswith('MOCK-'))


if __name__ == '__main__':
    unittest.main()
