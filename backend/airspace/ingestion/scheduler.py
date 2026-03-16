from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from backend.airspace.ingestion.pipeline import SOURCES, build_airspace_pipeline
from backend.airspace.repositories.airspace_version_repository import AirspaceVersionRepository

log = logging.getLogger(__name__)


def seed_missing_sources() -> None:
    pipeline = build_airspace_pipeline()
    versions = AirspaceVersionRepository()
    for source in SOURCES.values():
        if versions.has_active_version(source=source.source):
            continue
        log.info('airspace_scheduler_bootstrap source=%s action=ingest_missing_active', source.source)
        pipeline.ingest(source.source)


def run_scheduler() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    pipeline = build_airspace_pipeline()
    seed_missing_sources()
    scheduler = BlockingScheduler(timezone='UTC')

    for source in SOURCES.values():
        scheduler.add_job(
            lambda source_name=source.source: pipeline.ingest(source_name),
            trigger='interval',
            minutes=source.schedule_minutes,
            id=f'airspace_{source.source}',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info('airspace_scheduler_registered source=%s interval_minutes=%s', source.source, source.schedule_minutes)

    scheduler.start()


if __name__ == '__main__':
    run_scheduler()
