from __future__ import annotations

import unittest

from modules.flight_plans.domain.policies import can_cancel_flight_plan, enrich_flight_plan


class FlightPlanPoliciesTest(unittest.TestCase):
    def test_can_cancel_for_upcoming_plan(self):
        self.assertTrue(can_cancel_flight_plan({'workflow_status': 'planned', 'runtime_state': 'upcoming'}))

    def test_cannot_cancel_completed_or_cancelled_plan(self):
        self.assertFalse(can_cancel_flight_plan({'workflow_status': 'planned', 'runtime_state': 'completed'}))
        self.assertFalse(can_cancel_flight_plan({'workflow_status': 'cancelled', 'runtime_state': 'cancelled'}))

    def test_enrich_adds_download_url_and_flag(self):
        plan = enrich_flight_plan({'public_id': 'FP-1', 'workflow_status': 'planned', 'runtime_state': 'ongoing'})
        self.assertEqual(plan['download_url'], '/api/flight-plans/FP-1/pdf')
        self.assertTrue(plan['can_cancel'])


if __name__ == '__main__':
    unittest.main()
