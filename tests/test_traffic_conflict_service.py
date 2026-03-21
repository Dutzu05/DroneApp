from __future__ import annotations

import unittest

from backend.drone_tracking.services.traffic_conflict_service import TrafficConflictService


class TrafficConflictServiceTests(unittest.TestCase):
    def test_marks_imminent_for_close_intruder(self):
        service = TrafficConflictService()

        result = service.evaluate_conflicts(
            focus_drone={
                'drone_id': 'FOCUS-1',
                'latitude': 44.5030,
                'longitude': 26.1020,
                'altitude': 82.0,
            },
            other_drones=[
                {
                    'drone_id': 'TRAFFIC-LRBS-01',
                    'latitude': 44.5031,
                    'longitude': 26.1028,
                    'altitude': 90.0,
                }
            ],
        )

        self.assertEqual(result['traffic'][0]['traffic_severity'], 'imminent')
        self.assertEqual(result['alerts'][0]['severity'], 'imminent')

    def test_marks_possible_for_separated_intruder(self):
        service = TrafficConflictService()

        result = service.evaluate_conflicts(
            focus_drone={
                'drone_id': 'FOCUS-1',
                'latitude': 44.5030,
                'longitude': 26.1020,
                'altitude': 82.0,
            },
            other_drones=[
                {
                    'drone_id': 'TRAFFIC-LRBS-02',
                    'latitude': 44.5047,
                    'longitude': 26.1045,
                    'altitude': 140.0,
                }
            ],
        )

        self.assertEqual(result['traffic'][0]['traffic_severity'], 'possible')
        self.assertEqual(result['alerts'][0]['severity'], 'possible')


if __name__ == '__main__':
    unittest.main()
