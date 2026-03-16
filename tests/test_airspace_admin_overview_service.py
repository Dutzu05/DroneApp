from __future__ import annotations

import unittest

from backend.airspace.services.admin_overview_service import AirspaceAdminOverviewService, format_schedule_label


class _SourceConfig:
    def __init__(self, schedule_minutes: int):
        self.schedule_minutes = schedule_minutes


class _AdminRepoStub:
    def list_active_versions(self):
        return [{'source': 'restriction_zones_json', 'version_id': 'v1', 'record_count': 10}]

    def list_source_status(self):
        return [{'source': 'restriction_zones_json', 'record_count': 10, 'last_status': 'activated'}]

    def list_recent_raw_events(self, *, limit: int):
        return [{'source': 'restriction_zones_json', 'status': 'activated', 'limit': limit}]

    def list_recent_issues(self, *, limit: int):
        return [{'source': 'restriction_zones_json', 'status': 'failed', 'limit': limit}]


class AirspaceAdminOverviewServiceTests(unittest.TestCase):
    def test_format_schedule_label_handles_minutes_hours_days(self):
        self.assertEqual(format_schedule_label(None), 'manual')
        self.assertEqual(format_schedule_label(5), 'every 5 min')
        self.assertEqual(format_schedule_label(60), 'every 1 hour')
        self.assertEqual(format_schedule_label(120), 'every 2 hours')
        self.assertEqual(format_schedule_label(24 * 60), 'every 1 day')
        self.assertEqual(format_schedule_label(2 * 24 * 60), 'every 2 days')

    def test_overview_enriches_source_status_with_schedule_and_label(self):
        service = AirspaceAdminOverviewService(
            admin_repo=_AdminRepoStub(),
            sources={'restriction_zones_json': _SourceConfig(schedule_minutes=24 * 60)},
        )

        overview = service.overview(event_limit=5, issue_limit=3)

        self.assertEqual(len(overview['active_versions']), 1)
        self.assertEqual(len(overview['recent_events']), 1)
        self.assertEqual(len(overview['recent_issues']), 1)
        self.assertEqual(overview['recent_events'][0]['limit'], 5)
        self.assertEqual(overview['recent_issues'][0]['limit'], 3)
        self.assertEqual(overview['sources'][0]['schedule_minutes'], 24 * 60)
        self.assertEqual(overview['sources'][0]['schedule_label'], 'every 1 day')
        self.assertEqual(overview['sources'][0]['label'], 'RESTRICTION ZONES JSON')


if __name__ == '__main__':
    unittest.main()
