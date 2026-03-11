# Drone App Local Setup

## Flutter (project-local)
- SDK location: `.tooling/flutter`
- Use it directly: `./.tooling/flutter/bin/flutter --version`

## PostgreSQL (project-local)
- Data directory: `.postgres/data`
- Socket directory: `.postgres/run`
- Port: `5433`

## Quick Start
1. `source scripts/dev-env.sh`
2. `scripts/start-postgres.sh`
3. `scripts/init-db.sh`
4. `flutter --version`

## DB Connection
- `PGHOST=/home/vlad/Projects/Drone/.postgres/run`
- `PGPORT=5433`
- `DB name: drone_app`
- Table: `restriction_zones`

## Mobile Offline Cache (SQFlite)
- Flutter app: `mobile_app`
- Cache DB on device: `drone_cache.db` (app support directory)
- Cache tables:
  - `map_tiles` (tile bytes + TTL)
  - `zone_cache` (restriction zone JSON + TTL)

### Run mobile app
1. `cd mobile_app`
2. `../.tooling/flutter/bin/flutter pub get`
3. `../.tooling/flutter/bin/flutter run`
