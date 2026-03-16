from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def format_schedule_label(minutes: int | None) -> str:
    if minutes is None:
        return 'manual'
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"every {days} day{'s' if days != 1 else ''}"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"every {hours} hour{'s' if hours != 1 else ''}"
    return f'every {minutes} min'


@dataclass(slots=True)
class AirspaceAdminOverviewService:
    admin_repo: Any
    sources: dict[str, Any]

    def overview(self, *, event_limit: int = 20, issue_limit: int = 20) -> dict[str, Any]:
        active_versions = self.admin_repo.list_active_versions()
        source_status = self.admin_repo.list_source_status()
        recent_events = self.admin_repo.list_recent_raw_events(limit=event_limit)
        recent_issues = self.admin_repo.list_recent_issues(limit=issue_limit)

        for source in source_status:
            config = self.sources.get(source.get('source'))
            schedule_minutes = getattr(config, 'schedule_minutes', None)
            source['schedule_minutes'] = schedule_minutes
            source['schedule_label'] = format_schedule_label(schedule_minutes)
            source['label'] = (source.get('source') or '').replace('_', ' ').upper()

        return {
            'sources': source_status,
            'active_versions': active_versions,
            'recent_events': recent_events,
            'recent_issues': recent_issues,
        }
