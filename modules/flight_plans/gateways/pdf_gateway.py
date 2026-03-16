from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


class FlightPlanGateway:
    def __init__(
        self,
        *,
        build_flight_plan: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        build_anexa_payload: Callable[[dict[str, Any]], dict[str, Any]],
        generate_pdf: Callable[[dict[str, Any], Path], Path],
        assess_flight_area: Callable[[dict[str, Any], float], dict[str, Any]],
        build_circle_area: Callable[[float, float, float], dict[str, Any]],
        build_polygon_area: Callable[[list[list[float]]], dict[str, Any]],
        twr_options: Callable[[], list[dict[str, Any]]],
    ):
        self._build_flight_plan = build_flight_plan
        self._build_anexa_payload = build_anexa_payload
        self._generate_pdf = generate_pdf
        self._assess_flight_area = assess_flight_area
        self._build_circle_area = build_circle_area
        self._build_polygon_area = build_polygon_area
        self._twr_options = twr_options

    def build_plan(self, payload: dict[str, Any], owner: dict[str, Any]) -> dict[str, Any]:
        return self._build_flight_plan(payload, owner)

    def build_pdf_payload(self, plan: dict[str, Any]) -> dict[str, Any]:
        return self._build_anexa_payload(plan)

    def generate_pdf(self, plan: dict[str, Any], output_path: Path) -> Path:
        return self._generate_pdf(plan, output_path)

    def assess(self, payload: dict[str, Any]) -> dict[str, Any]:
        area_kind = (payload.get("area_kind") or "circle").lower()
        alt_m = float(payload.get("max_altitude_m") or 120)
        if area_kind == "polygon":
            area = self._build_polygon_area(payload.get("polygon_points") or [])
        else:
            area = self._build_circle_area(
                float(payload.get("center_lon")),
                float(payload.get("center_lat")),
                float(payload.get("radius_m")),
            )
        return self._assess_flight_area(area, alt_m)

    def twr_options(self) -> list[dict[str, Any]]:
        return self._twr_options()
