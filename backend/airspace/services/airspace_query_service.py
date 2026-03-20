from __future__ import annotations

from typing import Any

from backend.airspace.repositories.airspace_zone_repository import AirspaceZoneRepository
from backend.airspace.validators.geometry_validator import GeometryValidationError, validate_geometry


def _severity_for_category(category: str) -> str:
    if category in {'restricted', 'temporary_restriction'}:
        return 'high'
    if category in {'ctr', 'tma'}:
        return 'medium'
    return 'info'


def normalize_categories(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    allowed = {'ctr', 'tma', 'notam', 'restricted'}
    categories = {part.strip().lower() for part in raw.split(',') if part.strip()}
    return {category for category in categories if category in allowed} or None


class AirspaceQueryService:
    def __init__(self, zone_repo: AirspaceZoneRepository):
        self.zone_repo = zone_repo

    def _normalize_zone_geometry(self, zone: dict[str, Any]) -> dict[str, Any] | None:
        geometry = zone.get('geometry')
        if not geometry:
            return None
        try:
            normalized_geometry = validate_geometry(dict(geometry))
        except (GeometryValidationError, TypeError, ValueError):
            return None
        return {
            **zone,
            'geometry': normalized_geometry,
        }

    def _normalize_zone_list(self, zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for zone in zones:
            normalized_zone = self._normalize_zone_geometry(zone)
            if normalized_zone is not None:
                normalized.append(normalized_zone)
        return normalized

    def get_zones_in_bbox(self, bbox: tuple[float, float, float, float], *, categories: set[str] | None = None) -> dict[str, Any]:
        zones = self._normalize_zone_list(self.zone_repo.zones_in_bbox(bbox, categories=categories))
        return {'zones': zones, 'count': len(zones)}

    def get_zones_near(self, *, lat: float, lon: float, radius_km: float, categories: set[str] | None = None) -> dict[str, Any]:
        zones = self._normalize_zone_list(
            self.zone_repo.zones_near_point(lat=lat, lon=lon, radius_km=radius_km, categories=categories)
        )
        return {'zones': zones, 'count': len(zones)}

    def check_point(self, *, lat: float, lon: float, alt_m: float | None) -> dict[str, Any]:
        zones = self._normalize_zone_list(self.zone_repo.zones_for_point(lat=lat, lon=lon, alt_m=alt_m))
        severity = 'none'
        if zones:
            severity = max((_severity_for_category(zone['category']) for zone in zones), key=lambda item: ['none', 'info', 'medium', 'high'].index(item))
        return {'zones': zones, 'count': len(zones), 'warning_severity': severity}


def build_airspace_query_service() -> AirspaceQueryService:
    return AirspaceQueryService(AirspaceZoneRepository())
