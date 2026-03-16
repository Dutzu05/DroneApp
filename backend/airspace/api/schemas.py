from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RoutePoint(BaseModel):
    lat: float
    lon: float
    alt_m: float | None = None


class CheckRouteRequest(BaseModel):
    path: list[RoutePoint] = Field(min_length=2)


class ZoneResponse(BaseModel):
    zone_id: str
    source: str
    name: str
    category: str
    lower_altitude_m: float | None = None
    upper_altitude_m: float | None = None
    geometry: dict[str, Any]
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    distance_m: float | None = None


class ZonesEnvelopeResponse(BaseModel):
    zones: list[ZoneResponse]
    count: int | None = None


class PointCheckResponse(ZonesEnvelopeResponse):
    warning_severity: str


class RouteCheckResponse(ZonesEnvelopeResponse):
    warning_severity: str


class BBoxQuery(BaseModel):
    bbox: str

    @field_validator('bbox')
    @classmethod
    def validate_bbox(cls, value: str) -> str:
        parts = value.split(',')
        if len(parts) != 4:
            raise ValueError('bbox must contain minLon,minLat,maxLon,maxLat')
        [float(part) for part in parts]
        return value
