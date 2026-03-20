from __future__ import annotations

import math
from typing import Any

from shapely.geometry import LinearRing, MultiPolygon, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.validation import make_valid


class GeometryValidationError(ValueError):
    pass


SUPPORTED_TYPES = {'Polygon', 'MultiPolygon'}
_WEB_MERCATOR_HALF_WORLD = 20037508.34


def _web_mercator_to_lng_lat(x: float, y: float) -> tuple[float, float]:
    lng = (x / _WEB_MERCATOR_HALF_WORLD) * 180.0
    lat = (y / _WEB_MERCATOR_HALF_WORLD) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return lng, lat


def _normalize_position(position: list[float]) -> list[float]:
    if len(position) < 2:
        raise GeometryValidationError('Coordinate must contain longitude and latitude.')

    x = float(position[0])
    y = float(position[1])
    if not math.isfinite(x) or not math.isfinite(y):
        raise GeometryValidationError('Coordinate must be finite.')

    if abs(x) > 180.0 or abs(y) > 90.0:
        if abs(x) > _WEB_MERCATOR_HALF_WORLD or abs(y) > _WEB_MERCATOR_HALF_WORLD:
            raise GeometryValidationError('Coordinate is outside supported WGS84 and Web Mercator bounds.')
        x, y = _web_mercator_to_lng_lat(x, y)

    if abs(x) > 180.0 or abs(y) > 90.0:
        raise GeometryValidationError('Coordinate is outside WGS84 bounds after normalization.')

    return [x, y]


def _close_ring(ring: list[list[float]]) -> list[list[float]]:
    if not ring:
        raise GeometryValidationError('Empty polygon ring.')
    if ring[0] != ring[-1]:
        ring = [*ring, ring[0]]
    if len(ring) < 4:
        raise GeometryValidationError('Polygon ring must have at least 4 coordinates.')
    return ring


def _normalize_polygon_coordinates(coords: list[list[list[float]]]) -> list[list[list[float]]]:
    return [_close_ring([_normalize_position(pair) for pair in ring]) for ring in coords]


def validate_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    if geometry.get('type') not in SUPPORTED_TYPES:
        raise GeometryValidationError(f"Unsupported geometry type: {geometry.get('type')}")

    normalized = dict(geometry)
    if geometry['type'] == 'Polygon':
        normalized['coordinates'] = _normalize_polygon_coordinates(geometry.get('coordinates') or [])
    else:
        normalized['coordinates'] = [
            _normalize_polygon_coordinates(polygon)
            for polygon in (geometry.get('coordinates') or [])
        ]

    geom = shape(normalized)
    if geom.is_empty:
        raise GeometryValidationError('Geometry is empty.')
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.is_empty or not geom.is_valid:
        raise GeometryValidationError('Geometry is invalid after normalization.')

    if isinstance(geom, Polygon):
        geom = orient(geom, sign=1.0)
    elif isinstance(geom, MultiPolygon):
        geom = MultiPolygon([orient(poly, sign=1.0) for poly in geom.geoms])
    else:
        raise GeometryValidationError(f'Geometry normalized to unsupported type: {geom.geom_type}')

    validated = mapping(geom)
    if validated.get('type') not in SUPPORTED_TYPES:
        raise GeometryValidationError(f"Geometry normalized to unsupported type: {validated.get('type')}")
    return validated


def ensure_polygon_closed(geometry: dict[str, Any]) -> bool:
    if geometry.get('type') == 'Polygon':
        rings = geometry.get('coordinates') or []
    elif geometry.get('type') == 'MultiPolygon':
        polygons = geometry.get('coordinates') or []
        rings = [ring for polygon in polygons for ring in polygon]
    else:
        return False
    return all(LinearRing(ring).is_ring for ring in rings if ring)
