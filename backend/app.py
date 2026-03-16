from __future__ import annotations

from fastapi import FastAPI

from backend.airspace.api.routes import build_airspace_router
from backend.airspace.services.airspace_query_service import build_airspace_query_service
from backend.airspace.services.route_check_service import RouteCheckService
from backend.airspace.repositories.airspace_zone_repository import AirspaceZoneRepository


def build_app() -> FastAPI:
    app = FastAPI(title='Drone Backend', version='1.0.0')
    query_service = build_airspace_query_service()
    route_service = RouteCheckService(AirspaceZoneRepository())
    app.include_router(build_airspace_router(query_service, route_service))

    @app.get('/healthz')
    def healthz() -> dict[str, bool]:
        return {'ok': True}

    return app


app = build_app()
