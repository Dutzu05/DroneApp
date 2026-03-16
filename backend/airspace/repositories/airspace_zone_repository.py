from __future__ import annotations

import json
from typing import Any

from backend.airspace.models.airspace_zone import AirspaceZone
from backend.airspace.repositories.db import get_connection


class AirspaceZoneRepository:
    def _category_clause(self, categories: set[str] | None) -> str:
        if not categories:
            return ''
        clauses: list[str] = []
        if 'ctr' in categories:
            clauses.append("category = 'ctr'")
        if 'tma' in categories:
            clauses.append("category = 'tma'")
        if 'notam' in categories:
            clauses.append("(category = 'temporary_restriction' OR source LIKE 'notam%%')")
        if 'restricted' in categories:
            clauses.append("(category = 'restricted' AND source NOT LIKE 'notam%%')")
        if not clauses:
            return ''
        return ' AND (' + ' OR '.join(clauses) + ')'

    def replace_version(self, *, source: str, version_id: str, zones: list[AirspaceZone], conn=None) -> None:
        if conn is None:
            with get_connection() as managed_conn:
                self.replace_version(source=source, version_id=version_id, zones=zones, conn=managed_conn)
            return
        cur = conn.cursor()
        cur.execute('DELETE FROM airspace_zones WHERE source = %s AND version_id = %s', (source, version_id))
        for zone in zones:
            record = zone.as_record()
            cur.execute(
                """
                INSERT INTO airspace_zones (
                    zone_id,
                    version_id,
                    source,
                    name,
                    category,
                    lower_altitude_m,
                    upper_altitude_m,
                    geometry,
                    valid_from,
                    valid_to,
                    metadata
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                    %s,
                    %s,
                    %s::jsonb
                )
                ON CONFLICT (zone_id, version_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    category = EXCLUDED.category,
                    lower_altitude_m = EXCLUDED.lower_altitude_m,
                    upper_altitude_m = EXCLUDED.upper_altitude_m,
                    geometry = EXCLUDED.geometry,
                    valid_from = EXCLUDED.valid_from,
                    valid_to = EXCLUDED.valid_to,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                (
                    record['zone_id'],
                    record['version_id'],
                    record['source'],
                    record['name'],
                    record['category'],
                    record['lower_altitude_m'],
                    record['upper_altitude_m'],
                    json.dumps(record['geometry']),
                    record['valid_from'],
                    record['valid_to'],
                    json.dumps(record['metadata']),
                ),
            )

    def zones_in_bbox(self, bbox: tuple[float, float, float, float], *, categories: set[str] | None = None) -> list[dict[str, Any]]:
        min_lon, min_lat, max_lon, max_lat = bbox
        category_clause = self._category_clause(categories)
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT zone_id, source, name, category, lower_altitude_m, upper_altitude_m,
                       valid_from, valid_to, metadata,
                       ST_AsGeoJSON(geometry)::jsonb AS geometry
                FROM airspace_zones_active
                WHERE geometry && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
                  AND ST_Intersects(geometry, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
                  {category_clause}
                ORDER BY source, name
                """,
                (min_lon, min_lat, max_lon, max_lat, min_lon, min_lat, max_lon, max_lat),
            )
            return list(cur.fetchall())

    def zones_near_point(self, *, lat: float, lon: float, radius_km: float, categories: set[str] | None = None) -> list[dict[str, Any]]:
        radius_m = radius_km * 1000.0
        category_clause = self._category_clause(categories)
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT zone_id, source, name, category, lower_altitude_m, upper_altitude_m,
                       valid_from, valid_to, metadata,
                       ST_AsGeoJSON(geometry)::jsonb AS geometry,
                       ST_Distance(
                         geography(geometry),
                         geography(ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                       ) AS distance_m
                FROM airspace_zones_active
                WHERE ST_DWithin(
                    geography(geometry),
                    geography(ST_SetSRID(ST_MakePoint(%s, %s), 4326)),
                    %s
                )
                {category_clause}
                ORDER BY distance_m ASC, source, name
                """,
                (lon, lat, lon, lat, radius_m),
            )
            return list(cur.fetchall())

    def zones_for_point(self, *, lat: float, lon: float, alt_m: float | None) -> list[dict[str, Any]]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT zone_id, source, name, category, lower_altitude_m, upper_altitude_m,
                       valid_from, valid_to, metadata,
                       ST_AsGeoJSON(geometry)::jsonb AS geometry
                FROM airspace_zones_active
                WHERE ST_Intersects(geometry, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                  AND (%s IS NULL OR lower_altitude_m IS NULL OR lower_altitude_m <= %s)
                  AND (%s IS NULL OR upper_altitude_m IS NULL OR upper_altitude_m >= %s)
                ORDER BY source, name
                """,
                (lon, lat, alt_m, alt_m, alt_m, alt_m),
            )
            return list(cur.fetchall())

    def zones_for_route(self, *, path: list[dict[str, float]]) -> list[dict[str, Any]]:
        if len(path) < 2:
            return []
        wkt = 'LINESTRING(' + ', '.join(f"{point['lon']} {point['lat']}" for point in path) + ')'
        max_alt = max((point.get('alt_m') for point in path if point.get('alt_m') is not None), default=None)
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT zone_id, source, name, category, lower_altitude_m, upper_altitude_m,
                       valid_from, valid_to, metadata,
                       ST_AsGeoJSON(geometry)::jsonb AS geometry
                FROM airspace_zones_active
                WHERE ST_Intersects(geometry, ST_GeomFromText(%s, 4326))
                  AND (%s IS NULL OR lower_altitude_m IS NULL OR lower_altitude_m <= %s)
                  AND (%s IS NULL OR upper_altitude_m IS NULL OR upper_altitude_m >= %s)
                ORDER BY source, name
                """,
                (wkt, max_alt, max_alt, max_alt, max_alt),
            )
            return list(cur.fetchall())

    def zones_for_geometry(self, *, geometry_geojson: dict[str, Any], alt_m: float | None) -> list[dict[str, Any]]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT zone_id, source, name, category, lower_altitude_m, upper_altitude_m,
                       valid_from, valid_to, metadata,
                       ST_AsGeoJSON(geometry)::jsonb AS geometry
                FROM airspace_zones_active
                WHERE ST_Intersects(
                    geometry,
                    ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                )
                  AND (%s IS NULL OR lower_altitude_m IS NULL OR lower_altitude_m <= %s)
                  AND (%s IS NULL OR upper_altitude_m IS NULL OR upper_altitude_m >= %s)
                ORDER BY source, name
                """,
                (json.dumps(geometry_geojson), alt_m, alt_m, alt_m, alt_m),
            )
            return list(cur.fetchall())
