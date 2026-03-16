from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from backend.airspace.ingestion.pipeline import SOURCES, build_airspace_pipeline

log = logging.getLogger(__name__)


def run_scheduler() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    pipeline = build_airspace_pipeline()
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
