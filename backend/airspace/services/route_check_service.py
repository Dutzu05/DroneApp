from __future__ import annotations

from typing import Any

from backend.airspace.repositories.airspace_zone_repository import AirspaceZoneRepository


class RouteCheckService:
    def __init__(self, zone_repo: AirspaceZoneRepository):
        self.zone_repo = zone_repo

    def check_route(self, path: list[dict[str, float]]) -> dict[str, Any]:
        zones = self.zone_repo.zones_for_route(path=path)
        severity = 'none'
        if zones:
            severity = 'high' if any(zone['category'] in {'restricted', 'temporary_restriction'} for zone in zones) else 'medium'
        return {
            'zones': zones,
            'count': len(zones),
            'warning_severity': severity,
        }
