from __future__ import annotations

import math
from typing import Any, Callable

from backend.airspace.repositories.airspace_zone_repository import AirspaceZoneRepository
from backend.airspace.services.airspace_query_service import AirspaceQueryService
from backend.airspace.services.route_check_service import RouteCheckService

EARTH_RADIUS_M = 6371000.0


def _layer_key_for_zone(zone: dict[str, Any]) -> str:
    source = (zone.get('source') or '').lower()
    category = (zone.get('category') or '').lower()
    if category == 'ctr' or source.endswith('_ctr'):
        return 'ctr'
    if category == 'tma' or source.endswith('_tma'):
        return 'tma'
    if category == 'temporary_restriction' or 'notam' in source:
        return 'notam'
    return 'uas_zones'


def _label_for_layer_key(layer_key: str) -> str:
    return {
        'ctr': 'CTR',
        'tma': 'TMA',
        'notam': 'NOTAM',
        'uas_zones': 'UAS ZONE',
    }.get(layer_key, layer_key.replace('_', ' ').upper())


def _legacy_zone_payload(zone: dict[str, Any]) -> dict[str, Any]:
    props = dict(((zone.get('metadata') or {}).get('properties')) or {})
    props.setdefault('zone_id', zone.get('zone_id'))
    props.setdefault('name', zone.get('name'))
    props.setdefault('source', zone.get('source'))
    props.setdefault('category', zone.get('category'))
    props.setdefault('lower_limit_m', zone.get('lower_altitude_m'))
    props.setdefault('upper_limit_m', zone.get('upper_altitude_m'))
    if zone.get('geometry') is not None:
        props.setdefault('geometry', zone.get('geometry'))
    return props


def _circle_polygon(lon: float, lat: float, radius_m: float, *, steps: int = 48) -> dict[str, Any]:
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    angular_distance = radius_m / EARTH_RADIUS_M
    coordinates: list[list[float]] = []
    for step in range(steps):
        bearing = (2.0 * math.pi * step) / steps
        lat2 = math.asin(
            math.sin(lat1) * math.cos(angular_distance)
            + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
        )
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
            math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
        )
        coordinates.append([math.degrees(lon2), math.degrees(lat2)])
    coordinates.append(coordinates[0])
    return {'type': 'Polygon', 'coordinates': [coordinates]}


def area_to_geometry(area: dict[str, Any]) -> dict[str, Any]:
    if area['kind'] == 'circle':
        return _circle_polygon(area['center_lon'], area['center_lat'], area['radius_m'])
    polygon = [list(point) for point in area['points']]
    if polygon[0] != polygon[-1]:
        polygon.append(polygon[0])
    return {'type': 'Polygon', 'coordinates': [polygon]}


class FlightAreaAssessmentService:
    def __init__(self, *, zone_repo: AirspaceZoneRepository):
        self.zone_repo = zone_repo
        self.query_service = AirspaceQueryService(zone_repo)
        self.route_service = RouteCheckService(zone_repo)

    def crosscheck_point(self, *, lon: float, lat: float, alt_m: float) -> dict[str, list[dict[str, Any]]]:
        result = self.check_point(lon=lon, lat=lat, alt_m=alt_m)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for zone in result['zones']:
            layer_key = _layer_key_for_zone(zone)
            grouped.setdefault(layer_key, []).append(_legacy_zone_payload(zone))
        return grouped

    def check_point(self, *, lon: float, lat: float, alt_m: float) -> dict[str, Any]:
        return self.query_service.check_point(lat=lat, lon=lon, alt_m=alt_m)

    def blocking_center_hits(self, *, lon: float, lat: float, alt_m: float) -> list[dict[str, Any]]:
        result = self.check_point(lon=lon, lat=lat, alt_m=alt_m)
        hits: list[dict[str, Any]] = []
        for zone in result['zones']:
            layer_key = _layer_key_for_zone(zone)
            payload = _legacy_zone_payload(zone)
            hits.append(
                {
                    'layer_key': layer_key,
                    'label': _label_for_layer_key(layer_key),
                    'name': payload.get('zone_id') or payload.get('zone_code') or payload.get('notam_id') or payload.get('name') or payload.get('arsp_name') or layer_key,
                }
            )
        return hits

    def check_route(self, path: list[dict[str, float]]) -> dict[str, Any]:
        return self.route_service.check_route(path)

    def assess_area(
        self,
        *,
        area: dict[str, Any],
        alt_m: float,
        tower_contacts: dict[str, dict[str, Any]],
        resolve_icao: Callable[[str], str | None],
    ) -> dict[str, Any]:
        geometry = area_to_geometry(area)
        zones = self.zone_repo.zones_for_geometry(geometry_geojson=geometry, alt_m=alt_m)
        result: dict[str, Any] = {
            'area': area,
            'alt_m': alt_m,
            'ctr_hits': [],
            'uas_hits': [],
            'notam_hits': [],
            'tma_hits': [],
            'tower_contacts': [],
            'risk_level': 'LOW',
            'summary': '',
            'eligibility_status': 'ready',
            'warnings': [],
        }
        for zone in zones:
            payload = _legacy_zone_payload(zone)
            layer_key = _layer_key_for_zone(zone)
            if layer_key == 'ctr':
                result['ctr_hits'].append(payload)
                name = (payload.get('name') or payload.get('arsp_name') or '').upper()
                icao = (payload.get('icao') or resolve_icao(name)) if name else payload.get('icao')
                if icao and icao in tower_contacts:
                    contact = {**tower_contacts[icao], 'icao': icao}
                    if contact not in result['tower_contacts']:
                        result['tower_contacts'].append(contact)
            elif layer_key == 'tma':
                result['tma_hits'].append(payload)
            elif layer_key == 'notam':
                result['notam_hits'].append(payload)
            else:
                result['uas_hits'].append(payload)

        ctr_count = len(result['ctr_hits'])
        uas_count = len(result['uas_hits'])
        notam_count = len(result['notam_hits'])
        tma_count = len(result['tma_hits'])
        if ctr_count or uas_count:
            result['risk_level'] = 'HIGH'
        elif notam_count or tma_count:
            result['risk_level'] = 'MEDIUM'

        summary_parts: list[str] = []
        if ctr_count:
            ctr_names = [hit.get('name') or hit.get('arsp_name') or 'CTR' for hit in result['ctr_hits']]
            summary_parts.append(f"CTR overlap: {', '.join(ctr_names)}")
        if uas_count:
            summary_parts.append(f'{uas_count} UAS restriction zone(s)')
        if notam_count:
            summary_parts.append(f'{notam_count} NOTAM zone(s)')
        if tma_count:
            summary_parts.append(f'{tma_count} TMA zone(s) at {alt_m:.0f} m')
        if not summary_parts:
            summary_parts.append('No conflicting airspace found')

        if uas_count:
            result['eligibility_status'] = 'manual_review'
            result['warnings'].append(
                'ANEXA 1 notes say open-category flights in CTR are considered authorized only outside restricted UAS geographical zones.'
            )
        if result['risk_level'] == 'HIGH':
            result['warnings'].append('High-risk airspace overlap detected. Manual review is required before relying on this plan.')
        elif result['risk_level'] == 'MEDIUM':
            result['warnings'].append('Additional coordination may be needed because NOTAM/TMA overlaps were detected.')

        result['summary'] = '. '.join(summary_parts) + '.'
        return result


def build_flight_area_assessment_service() -> FlightAreaAssessmentService:
    return FlightAreaAssessmentService(zone_repo=AirspaceZoneRepository())
