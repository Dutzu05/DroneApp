from __future__ import annotations

import math
from typing import Any

IMMINENT_HORIZONTAL_M = 120.0
IMMINENT_VERTICAL_M = 45.0
POSSIBLE_HORIZONTAL_M = 350.0
POSSIBLE_VERTICAL_M = 90.0
MONITOR_HORIZONTAL_M = 1_500.0
MONITOR_VERTICAL_M = 180.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * radius_m * math.asin(math.sqrt(a))


def _severity_rank(severity: str) -> int:
    return {
        'clear': 0,
        'safe': 1,
        'monitor': 2,
        'possible': 3,
        'imminent': 4,
    }.get(str(severity or '').lower(), 0)


class TrafficConflictService:
    def evaluate_conflicts(self, *, focus_drone: dict[str, Any], other_drones: list[dict[str, Any]]) -> dict[str, Any]:
        focus_lat = float(focus_drone.get('latitude') or 0.0)
        focus_lon = float(focus_drone.get('longitude') or 0.0)
        focus_alt = float(focus_drone.get('altitude') or 0.0)

        assessed: list[dict[str, Any]] = []
        alerts: list[dict[str, Any]] = []

        for drone in other_drones or []:
            horizontal_m = _haversine_m(
                focus_lat,
                focus_lon,
                float(drone.get('latitude') or 0.0),
                float(drone.get('longitude') or 0.0),
            )
            vertical_m = abs(float(drone.get('altitude') or 0.0) - focus_alt)
            severity = 'safe'
            suggestion = ''
            if horizontal_m <= IMMINENT_HORIZONTAL_M and vertical_m <= IMMINENT_VERTICAL_M:
                severity = 'imminent'
                suggestion = 'Descend 20-30 m and keep the intruder on visual watch.'
            elif horizontal_m <= POSSIBLE_HORIZONTAL_M and vertical_m <= POSSIBLE_VERTICAL_M:
                severity = 'possible'
                suggestion = 'Hold position or widen separation until the closure trend improves.'
            elif horizontal_m <= MONITOR_HORIZONTAL_M and vertical_m <= MONITOR_VERTICAL_M:
                severity = 'monitor'
                suggestion = 'Monitor closure and heading while maintaining visual separation.'

            notice = (
                f"{str(drone.get('drone_id') or 'TRAFFIC')} at "
                f"{round(horizontal_m):.0f} m horizontal, {round(vertical_m):.0f} m vertical separation."
            )
            enriched = {
                **drone,
                'horizontal_distance_m': round(horizontal_m, 1),
                'vertical_distance_m': round(vertical_m, 1),
                'traffic_severity': severity,
                'traffic_notice': notice,
            }
            assessed.append(enriched)

            if severity in {'imminent', 'possible'}:
                alerts.append(
                    {
                        'drone_id': drone.get('drone_id'),
                        'severity': severity,
                        'notice': notice,
                        'suggestion': suggestion,
                    }
                )

        assessed.sort(
            key=lambda item: (
                -_severity_rank(str(item.get('traffic_severity') or '')),
                float(item.get('horizontal_distance_m') or 0.0),
                str(item.get('drone_id') or ''),
            )
        )
        alerts.sort(
            key=lambda item: (
                -_severity_rank(str(item.get('severity') or '')),
                str(item.get('drone_id') or ''),
            )
        )

        return {
            'traffic': assessed,
            'alerts': alerts,
            'top_severity': alerts[0]['severity'] if alerts else (assessed[0]['traffic_severity'] if assessed else 'clear'),
        }
