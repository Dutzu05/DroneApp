from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Any

from backend.airspace.ingestion.restriction_fetcher import fetch_restriction_zones
from backend.airspace.ingestion.romatsa_fetcher import fetch_romatsa_layer
from backend.airspace.models.airspace_zone import AirspaceZone
from backend.airspace.normalizers.zone_normalizer import normalize_feature
from backend.airspace.parsers.restriction_parser import parse_restriction_feature_collection
from backend.airspace.parsers.wfs_parser import parse_wfs_feature_collection
from backend.airspace.repositories.db import get_connection
from backend.airspace.repositories.airspace_version_repository import AirspaceVersionRepository
from backend.airspace.repositories.airspace_zone_repository import AirspaceZoneRepository
from backend.airspace.repositories.raw_source_repository import RawSourceRepository

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionSource:
    source: str
    fetcher: Callable[[], dict[str, Any]]
    parser: Callable[[dict[str, Any]], list[dict[str, Any]]]
    schedule_minutes: int


SOURCES = {
    'romatsa_wfs_ctr': IngestionSource(
        source='romatsa_wfs_ctr',
        fetcher=lambda: fetch_romatsa_layer('romatsa_wfs_ctr'),
        parser=parse_wfs_feature_collection,
        schedule_minutes=24 * 60,
    ),
    'romatsa_wfs_tma': IngestionSource(
        source='romatsa_wfs_tma',
        fetcher=lambda: fetch_romatsa_layer('romatsa_wfs_tma'),
        parser=parse_wfs_feature_collection,
        schedule_minutes=24 * 60,
    ),
    'restriction_zones_json': IngestionSource(
        source='restriction_zones_json',
        fetcher=fetch_restriction_zones,
        parser=parse_restriction_feature_collection,
        schedule_minutes=24 * 60,
    ),
    'notam_wfs': IngestionSource(
        source='notam_wfs',
        fetcher=lambda: fetch_romatsa_layer('notam_wfs'),
        parser=parse_wfs_feature_collection,
        schedule_minutes=5,
    ),
}


class AirspaceIngestionPipeline:
    def __init__(
        self,
        *,
        raw_repo: RawSourceRepository,
        version_repo: AirspaceVersionRepository,
        zone_repo: AirspaceZoneRepository,
    ):
        self.raw_repo = raw_repo
        self.version_repo = version_repo
        self.zone_repo = zone_repo

    def ingest(self, source_name: str) -> dict[str, Any]:
        source = SOURCES[source_name]
        started_at = datetime.now(timezone.utc)
        log.info('airspace_ingestion_started source=%s started_at=%s', source.source, started_at.isoformat())

        payload = source.fetcher()
        checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()
        with get_connection() as conn:
            raw_id = self.raw_repo.create(
                source=source.source,
                fetched_at=started_at,
                payload_json=payload,
                checksum=checksum,
                status='fetched',
                conn=conn,
            )

            if self.version_repo.find_active_by_checksum(source=source.source, checksum=checksum, conn=conn):
                self.raw_repo.update_status(raw_id, status='duplicate', conn=conn)
                log.info('airspace_ingestion_skipped source=%s checksum=%s reason=duplicate', source.source, checksum)
                return {
                    'source': source.source,
                    'started_at': started_at.isoformat(),
                    'record_count': 0,
                    'checksum': checksum,
                    'status': 'duplicate',
                    'errors': [],
                }

            features = source.parser(payload)
            version_id = str(uuid.uuid4())
            zones: list[AirspaceZone] = []
            errors: list[dict[str, Any]] = []
            for index, feature in enumerate(features):
                try:
                    zones.append(normalize_feature(source=source.source, feature=feature, version_id=version_id, fetched_at=started_at))
                except Exception as exc:
                    errors.append({'index': index, 'error': str(exc)})

            self.version_repo.create(
                version_id=version_id,
                source=source.source,
                imported_at=started_at,
                record_count=len(zones),
                checksum=checksum,
                is_active=False,
                conn=conn,
            )
            self.zone_repo.replace_version(source=source.source, version_id=version_id, zones=zones, conn=conn)
            self.version_repo.activate(source=source.source, version_id=version_id, conn=conn)
            self.raw_repo.update_status(raw_id, status='activated', conn=conn)

        finished_at = datetime.now(timezone.utc)
        log.info(
            'airspace_ingestion_completed source=%s record_count=%s errors=%s duration_s=%.3f',
            source.source,
            len(zones),
            len(errors),
            (finished_at - started_at).total_seconds(),
        )
        return {
            'source': source.source,
            'started_at': started_at.isoformat(),
            'finished_at': finished_at.isoformat(),
            'record_count': len(zones),
            'checksum': checksum,
            'status': 'activated',
            'errors': errors,
        }


def build_airspace_pipeline() -> AirspaceIngestionPipeline:
    return AirspaceIngestionPipeline(
        raw_repo=RawSourceRepository(),
        version_repo=AirspaceVersionRepository(),
        zone_repo=AirspaceZoneRepository(),
    )
