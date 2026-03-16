from __future__ import annotations

from typing import Any

from backend.airspace.parsers.wfs_parser import WfsParserError


def parse_restriction_feature_collection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get('type') != 'FeatureCollection':
        raise WfsParserError('Expected a GeoJSON FeatureCollection payload.')
    features = payload.get('features')
    if not isinstance(features, list):
        raise WfsParserError('Restriction payload is missing the features array.')
    return [feature for feature in features if isinstance(feature, dict)]
