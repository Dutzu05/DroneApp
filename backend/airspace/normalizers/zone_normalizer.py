from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from backend.airspace.models.airspace_zone import AirspaceZone
from backend.airspace.validators.geometry_validator import validate_geometry

FT_TO_M = 0.3048
_RE_METRES = re.compile(r'^\s*(?P<val>\d+(?:\.\d+)?)\s*m?\s*(?:AGL)?\s*$', re.IGNORECASE)
_RE_FEET = re.compile(r'^\s*(?P<val>\d+(?:\.\d+)?)\s*(?:FT|FEET)\s*(?:AGL|AMSL|STD|QNH)?\s*$', re.IGNORECASE)
_RE_FL = re.compile(r'^\s*FL\s*(?P<val>\d+)\s*$', re.IGNORECASE)
_RE_BARE_NUM = re.compile(r'^\s*(?P<val>\d{3,5})\s*$')


class ZoneNormalizationError(ValueError):
    pass


def parse_altitude_to_metres(raw: str | None, unit_hint: str | None = None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None

    upper = raw.upper()
    if upper in ('GND', 'GROUND', '0', '0M AGL', '0 M AGL'):
        return 0.0
    if upper in ('UNLTD', 'UNLIMITED', 'UNL'):
        return 99999.0
    if upper in ('NESTB', 'BY NOTAM'):
        return None

    m = _RE_METRES.match(raw)
    if m:
        return float(m.group('val'))
    m = _RE_FEET.match(raw)
    if m:
        return round(float(m.group('val')) * FT_TO_M, 1)
    m = _RE_FL.match(raw)
    if m:
        return round(int(m.group('val')) * 100 * FT_TO_M, 1)
    m = _RE_BARE_NUM.match(raw)
    if m:
        val = int(m.group('val'))
        if unit_hint == 'FL':
            return round(val * 100 * FT_TO_M, 1)
        return round(val * FT_TO_M, 1)
    return None


def parse_timestamp(value: str | None, fallback: datetime | None = None) -> datetime | None:
    if not value:
        return fallback
    parsed = value
    if parsed.endswith('Z'):
        parsed = parsed[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(parsed)
    except ValueError:
        return fallback
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def category_for_source(source: str, properties: dict[str, Any]) -> str:
    layer = (properties.get('layer') or '').lower()
    status = (properties.get('status') or '').lower()
    if 'notam' in source or 'notam' in layer or status == 'by notam':
        return 'temporary_restriction'
    if 'tma' in source or layer == 'tma':
        return 'tma'
    if 'ctr' in source or layer == 'ctr':
        return 'ctr'
    return 'restricted'


def zone_name_for_feature(source: str, properties: dict[str, Any]) -> str:
    return (
        properties.get('name')
        or properties.get('zone_id')
        or properties.get('zone_code')
        or properties.get('notam_id')
        or properties.get('arsp_name')
        or f'{source} zone'
    )


def stable_zone_id(source: str, properties: dict[str, Any], geometry: dict[str, Any]) -> str:
    candidate = (
        properties.get('zone_id')
        or properties.get('zone_code')
        or properties.get('notam_id')
        or properties.get('id')
        or properties.get('name')
    )
    if candidate:
        return f'{source}_{str(candidate).strip().replace(" ", "_")}'
    digest = hashlib.sha256(str(geometry).encode('utf-8')).hexdigest()[:12]
    return f'{source}_{digest}'


def normalize_feature(*, source: str, feature: dict[str, Any], version_id: str, fetched_at: datetime) -> AirspaceZone:
    properties = dict(feature.get('properties') or {})
    geometry = validate_geometry(feature.get('geometry') or {})

    lower_raw = properties.get('lower_lim') or properties.get('lower_lim_raw') or properties.get('lowerLimit')
    upper_raw = properties.get('upper_lim') or properties.get('upper_lim_raw') or properties.get('upperLimit')
    lower_altitude_m = parse_altitude_to_metres(lower_raw)
    upper_altitude_m = parse_altitude_to_metres(upper_raw, unit_hint='FT')

    valid_from = parse_timestamp(properties.get('valid_from') or properties.get('from')) or fetched_at
    valid_to = parse_timestamp(properties.get('valid_to') or properties.get('to'))

    normalized = AirspaceZone(
        zone_id=stable_zone_id(source, properties, geometry),
        version_id=version_id,
        source=source,
        name=zone_name_for_feature(source, properties),
        category=category_for_source(source, properties),
        lower_altitude_m=lower_altitude_m,
        upper_altitude_m=upper_altitude_m,
        geometry=geometry,
        valid_from=valid_from,
        valid_to=valid_to,
        metadata={'properties': properties},
    )

    if normalized.valid_from and normalized.valid_from.tzinfo is None:
        raise ZoneNormalizationError('valid_from must be ISO-8601 with timezone information.')
    if normalized.valid_to and normalized.valid_to.tzinfo is None:
        raise ZoneNormalizationError('valid_to must be ISO-8601 with timezone information.')
    return normalized
