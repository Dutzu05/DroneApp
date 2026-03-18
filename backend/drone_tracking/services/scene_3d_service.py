from __future__ import annotations

import hashlib
import math
from typing import Any


def _meters_per_lon_degree(lat: float) -> float:
    return max(math.cos(math.radians(lat)), 0.25) * 111_320.0


def _offset_point(lat: float, lon: float, *, distance_m: float, bearing_deg: float) -> tuple[float, float]:
    lat_delta = math.cos(math.radians(bearing_deg)) * distance_m / 111_320.0
    lon_delta = math.sin(math.radians(bearing_deg)) * distance_m / _meters_per_lon_degree(lat)
    return lat + lat_delta, lon + lon_delta


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * radius_m * math.asin(math.sqrt(a))


def _zone_color(category: str) -> str:
    category = (category or '').lower()
    if category == 'restricted':
        return '#e94560'
    if category == 'temporary_restriction':
        return '#ff9800'
    if category == 'ctr':
        return '#58a6ff'
    if category == 'tma':
        return '#3fb950'
    return '#8b949e'


def _region_bounds(lat: float, lon: float, *, radius_m: float) -> dict[str, float]:
    north_lat, _ = _offset_point(lat, lon, distance_m=radius_m, bearing_deg=0.0)
    south_lat, _ = _offset_point(lat, lon, distance_m=radius_m, bearing_deg=180.0)
    _, east_lon = _offset_point(lat, lon, distance_m=radius_m, bearing_deg=90.0)
    _, west_lon = _offset_point(lat, lon, distance_m=radius_m, bearing_deg=270.0)
    return {
        'min_latitude': min(south_lat, north_lat),
        'max_latitude': max(south_lat, north_lat),
        'min_longitude': min(west_lon, east_lon),
        'max_longitude': max(west_lon, east_lon),
    }


class Drone3DSceneService:
    def __init__(self, *, drone_repo, airspace_query_service, cesium_ion_token: str = ''):
        self.drone_repo = drone_repo
        self.airspace_query_service = airspace_query_service
        self.cesium_ion_token = cesium_ion_token.strip()

    def _mock_obstacles(self, focus_drone: dict[str, Any], *, radius_km: float) -> list[dict[str, Any]]:
        drone_id = str(focus_drone.get('drone_id') or '')
        lat = float(focus_drone['latitude'])
        lon = float(focus_drone['longitude'])
        seed = int(hashlib.sha256(drone_id.encode('utf-8')).hexdigest()[:8], 16) if drone_id else 0
        kinds = ['mast', 'tower', 'building', 'ridge']
        obstacles: list[dict[str, Any]] = []
        max_distance_m = radius_km * 1000.0
        for index in range(6):
            distance_m = min(900.0 + index * 1350.0 + (seed % 300), max_distance_m * 0.88)
            bearing = (seed % 360 + index * 57) % 360
            obstacle_lat, obstacle_lon = _offset_point(lat, lon, distance_m=distance_m, bearing_deg=bearing)
            kind = kinds[index % len(kinds)]
            height_m = 22.0 + ((seed >> (index % 8)) % 120)
            footprint_m = 14.0 + ((seed >> ((index + 2) % 8)) % 24)
            obstacles.append(
                {
                    'obstacle_id': f'OBS-{drone_id}-{index + 1}',
                    'name': f'Mock {kind.title()} {index + 1}',
                    'kind': kind,
                    'latitude': obstacle_lat,
                    'longitude': obstacle_lon,
                    'base_altitude_m': 0.0,
                    'height_m': float(height_m),
                    'footprint_radius_m': float(footprint_m),
                    'distance_m': float(distance_m),
                    'source': 'mock',
                }
            )
        return obstacles

    def _normalize_zone(self, zone: dict[str, Any]) -> dict[str, Any]:
        lower_m = float(zone.get('lower_altitude_m') or 0.0)
        upper_raw = zone.get('upper_altitude_m')
        upper_m = float(upper_raw) if upper_raw is not None else max(lower_m + 120.0, 120.0)
        if upper_m <= lower_m:
            upper_m = lower_m + 60.0
        return {
            'zone_id': zone.get('zone_id'),
            'source': zone.get('source'),
            'name': zone.get('name'),
            'category': zone.get('category'),
            'geometry': zone.get('geometry'),
            'distance_m': float(zone.get('distance_m') or 0.0),
            'lower_altitude_m': lower_m,
            'upper_altitude_m': upper_m,
            'color': _zone_color(str(zone.get('category') or '')),
        }

    def build_scene(
        self,
        drone_id: str,
        *,
        owner_email: str | None,
        radius_km: float = 5.0,
        admin_view: bool = False,
    ) -> dict[str, Any]:
        focus_drone = self.drone_repo.get_live_drone(
            drone_id,
            owner_email=None if admin_view else owner_email,
            include_upcoming=False,
            only_ongoing=True,
        )
        if not focus_drone:
            raise LookupError('Live drone not found for this account')

        focus_lat = float(focus_drone['latitude'])
        focus_lon = float(focus_drone['longitude'])
        radius_km = max(1.0, min(float(radius_km), 25.0))
        radius_m = radius_km * 1000.0
        region_bounds = _region_bounds(focus_lat, focus_lon, radius_m=radius_m)

        all_live = self.drone_repo.list_live_drones(owner_email=None, include_upcoming=False, only_ongoing=True)
        nearby_aircraft = []
        for drone in all_live:
            if str(drone.get('drone_id') or '') == drone_id:
                continue
            distance_m = _haversine_m(
                focus_lat,
                focus_lon,
                float(drone.get('latitude') or 0.0),
                float(drone.get('longitude') or 0.0),
            )
            if distance_m <= radius_km * 1000.0:
                nearby_aircraft.append(
                    {
                        **drone,
                        'distance_m': round(distance_m, 1),
                    }
                )
        nearby_aircraft.sort(key=lambda item: item['distance_m'])

        zones_response = self.airspace_query_service.get_zones_near(
            lat=focus_lat,
            lon=focus_lon,
            radius_km=radius_km,
            categories={'ctr', 'tma', 'notam', 'restricted'},
        )
        zones = [self._normalize_zone(zone) for zone in zones_response.get('zones', [])]
        track = self.drone_repo.telemetry_history(drone_id, limit=40)

        return {
            'drone': {
                **focus_drone,
                'track': track,
            },
            'nearby_aircraft': nearby_aircraft,
            'obstacles': self._mock_obstacles(focus_drone, radius_km=radius_km),
            'zones': zones,
            'scene': {
                'radius_km': radius_km,
                'focus_region': {
                    'shape': 'circle',
                    'center_latitude': focus_lat,
                    'center_longitude': focus_lon,
                    'radius_m': radius_m,
                    'bounds': region_bounds,
                },
                'terrain': {
                    'provider': 'ion' if self.cesium_ion_token else 'ellipsoid',
                    'ion_enabled': bool(self.cesium_ion_token),
                    'ion_token': self.cesium_ion_token,
                },
                'imagery': {
                    'provider': 'openstreetmap',
                    'url': 'https://tile.openstreetmap.org/',
                    'attribution': '(c) OpenStreetMap contributors',
                    'kind': 'street',
                },
                'buildings': {
                    'provider': 'cesium_osm_buildings' if self.cesium_ion_token else 'none',
                    'ion_enabled': bool(self.cesium_ion_token),
                    'source': 'openstreetmap',
                },
                'follow': {
                    'mode': 'tracked_entity',
                    'refresh_interval_s': 5,
                },
                'camera': {
                    'latitude': focus_lat,
                    'longitude': focus_lon,
                    'altitude_m': max(float(focus_drone.get('altitude') or 0.0) + radius_m * 0.42, 1800.0),
                    'heading_deg': float(focus_drone.get('heading') or 0.0),
                    'pitch_deg': -35.0,
                },
                'rendering': {
                    'load_mode': 'lazy',
                    'zone_limit': len(zones),
                    'obstacle_limit': 6,
                    'nearby_aircraft_limit': len(nearby_aircraft),
                    'terrain_shadows': bool(self.cesium_ion_token),
                    'building_layer_enabled': bool(self.cesium_ion_token),
                },
                'data_sources': [
                    {
                        'name': 'Cesium World Terrain',
                        'type': 'terrain',
                        'enabled': bool(self.cesium_ion_token),
                    },
                    {
                        'name': 'Cesium OSM Buildings',
                        'type': 'buildings',
                        'enabled': bool(self.cesium_ion_token),
                    },
                    {
                        'name': 'OpenStreetMap Raster Tiles',
                        'type': 'imagery',
                        'enabled': True,
                    },
                ],
            },
        }
