from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any


class DroneMockTelemetryService:
    def __init__(self, repo):
        self.repo = repo

    def _seed(self, value: str) -> int:
        return int(hashlib.sha256(value.encode('utf-8')).hexdigest()[:8], 16)

    def _geometry_center(self, plan: dict[str, Any]) -> tuple[float, float, float, float]:
        if plan.get('area_kind') == 'circle' and plan.get('center_lon') is not None and plan.get('center_lat') is not None:
            radius_m = float(plan.get('radius_m') or 50.0)
            return float(plan['center_lon']), float(plan['center_lat']), radius_m, radius_m

        geometry = (plan.get('area_geojson') or {}).get('coordinates') or []
        points: list[tuple[float, float]] = []

        def walk(coords):
            if not isinstance(coords, list):
                return
            if coords and isinstance(coords[0], (int, float)):
                points.append((float(coords[0]), float(coords[1])))
                return
            for item in coords:
                walk(item)

        walk(geometry)
        if not points:
            return 25.0, 45.0, 60.0, 60.0
        lons = [point[0] for point in points]
        lats = [point[1] for point in points]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        radius_lon_m = max((max_lon - min_lon) * 111_320.0 / 2.0, 40.0)
        radius_lat_m = max((max_lat - min_lat) * 111_320.0 / 2.0, 40.0)
        return (min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0, radius_lon_m, radius_lat_m

    def _snapshot_for_plan(self, plan: dict[str, Any], now: datetime, drone_id: str) -> dict[str, Any]:
        seed = self._seed(drone_id)
        center_lon, center_lat, radius_lon_m, radius_lat_m = self._geometry_center(plan)
        phase = (now.timestamp() / 6.0) + (seed % 360)
        runtime_state = (plan.get('runtime_state') or 'upcoming').lower()
        max_altitude = max(float(plan.get('max_altitude_m') or 120.0), 15.0)
        lon_scale = max(math.cos(math.radians(center_lat)), 0.3)

        if runtime_state == 'ongoing':
            orbit_radius_lon_deg = min(max(radius_lon_m * 0.35, 25.0), 260.0) / (111_320.0 * lon_scale)
            orbit_radius_lat_deg = min(max(radius_lat_m * 0.35, 25.0), 260.0) / 111_320.0
            longitude = center_lon + math.cos(phase / 1.8) * orbit_radius_lon_deg
            latitude = center_lat + math.sin(phase / 1.8) * orbit_radius_lat_deg
            altitude = min(max_altitude, max(18.0, max_altitude * 0.72 + math.sin(phase / 3.0) * 6.0))
            speed = 8.0 + abs(math.sin(phase / 2.4)) * 5.0
            battery = max(22.0, 92.0 - ((int(now.timestamp()) + seed) % 2400) / 60.0)
            status = 'flying'
        else:
            idle_radius_lon_deg = min(max(radius_lon_m * 0.08, 8.0), 24.0) / (111_320.0 * lon_scale)
            idle_radius_lat_deg = min(max(radius_lat_m * 0.08, 8.0), 24.0) / 111_320.0
            longitude = center_lon + math.cos(phase / 4.5) * idle_radius_lon_deg
            latitude = center_lat + math.sin(phase / 4.5) * idle_radius_lat_deg
            altitude = 2.0 + abs(math.sin(phase / 5.0)) * 1.5
            speed = 0.6 + abs(math.sin(phase / 3.0)) * 0.8
            battery = max(35.0, 98.0 - ((int(now.timestamp()) + seed) % 3600) / 240.0)
            status = 'scheduled'

        heading = (math.degrees(phase / 1.8) + 90.0) % 360.0
        pitch = math.sin(phase / 2.8) * (5.0 if runtime_state == 'ongoing' else 1.2)
        roll = math.cos(phase / 2.3) * (8.0 if runtime_state == 'ongoing' else 2.0)
        return {
            'drone_id': drone_id,
            'flight_plan_public_id': plan['public_id'],
            'latitude': latitude,
            'longitude': longitude,
            'altitude': altitude,
            'heading': heading,
            'pitch': pitch,
            'roll': roll,
            'speed': speed,
            'timestamp': now,
            'battery_level': round(battery, 1),
            'status': status,
            'runtime_state': runtime_state,
        }

    def generate_tick(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current_time = now or self.repo.now_utc()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        plans = self.repo.list_mock_candidate_plans(include_upcoming=True)
        snapshots: list[dict[str, Any]] = []
        for plan in plans:
            drone_id = plan.get('drone_id') or f"MOCK-{plan['public_id'][-8:]}"
            device = self.repo.upsert_drone_device(
                drone_id=drone_id,
                owner_user_id=plan.get('owner_user_id'),
                owner_email=plan.get('owner_email') or '',
                owner_display_name=plan.get('owner_display_name') or '',
                flight_plan_public_id=plan['public_id'],
                label=plan.get('drone_label') or f"Mock {plan['public_id']}",
                is_mock=True,
            )
            snapshot = self._snapshot_for_plan(plan, current_time, drone_id)
            self.repo.insert_telemetry(
                drone_device_id=int(device['id']),
                drone_id=drone_id,
                flight_plan_public_id=plan['public_id'],
                latitude=float(snapshot['latitude']),
                longitude=float(snapshot['longitude']),
                altitude=float(snapshot['altitude']),
                heading=float(snapshot['heading']),
                pitch=float(snapshot['pitch']),
                roll=float(snapshot['roll']),
                speed=float(snapshot['speed']),
                telemetry_timestamp=current_time,
                battery_level=float(snapshot['battery_level']),
                status=str(snapshot['status']),
                source='mock',
            )
            snapshots.append({
                **snapshot,
                'owner_email': plan.get('owner_email'),
                'owner_display_name': plan.get('owner_display_name'),
                'location_name': plan.get('location_name'),
                'selected_twr': plan.get('selected_twr'),
                'label': device.get('label') or '',
                'is_mock': True,
            })
        return snapshots
