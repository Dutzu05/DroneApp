from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.airspace.api.schemas import CheckRouteRequest, PointCheckResponse, RouteCheckResponse, ZonesEnvelopeResponse
from backend.airspace.services.airspace_query_service import AirspaceQueryService, normalize_categories
from backend.airspace.services.route_check_service import RouteCheckService


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = raw.split(',')
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail='bbox must contain minLon,minLat,maxLon,maxLat')
    try:
        values = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='bbox values must be numeric') from exc
    return values  # type: ignore[return-value]


def build_airspace_router(query_service: AirspaceQueryService, route_service: RouteCheckService) -> APIRouter:
    router = APIRouter(prefix='/airspace', tags=['airspace'])

    @router.get('/zones', response_model=ZonesEnvelopeResponse)
    def get_zones(
        bbox: str = Query(..., description='minLon,minLat,maxLon,maxLat'),
        categories: str | None = Query(None, description='Comma-separated: ctr,tma,notam,restricted'),
    ):
        return query_service.get_zones_in_bbox(_parse_bbox(bbox), categories=normalize_categories(categories))

    @router.get('/zones/near', response_model=ZonesEnvelopeResponse)
    def get_zones_near(
        lat: float,
        lon: float,
        radius_km: float = 10.0,
        categories: str | None = Query(None, description='Comma-separated: ctr,tma,notam,restricted'),
    ):
        return query_service.get_zones_near(lat=lat, lon=lon, radius_km=radius_km, categories=normalize_categories(categories))

    @router.get('/check-point', response_model=PointCheckResponse)
    def check_point(lat: float, lon: float, alt_m: float | None = None):
        return query_service.check_point(lat=lat, lon=lon, alt_m=alt_m)

    @router.post('/check-route', response_model=RouteCheckResponse)
    def check_route(body: CheckRouteRequest):
        return route_service.check_route([point.model_dump() for point in body.path])

    return router
