#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.airspace.ingestion.pipeline import SOURCES, build_airspace_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description='Run backend airspace ingestion sources.')
    parser.add_argument('--source', choices=sorted(SOURCES.keys()), action='append', help='Specific source to ingest. Can be repeated.')
    args = parser.parse_args()

    pipeline = build_airspace_pipeline()
    sources = args.source or sorted(SOURCES.keys())
    for source in sources:
        result = pipeline.ingest(source)
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
