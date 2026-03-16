# Airspace Backend Module

This module is the backend source of truth for airspace ingestion and spatial queries.

## Stack
- Python
- FastAPI
- PostgreSQL + PostGIS
- APScheduler
- GeoJSON as the normalized interchange format

## Module layout
- `backend/airspace/api/`
- `backend/airspace/ingestion/`
- `backend/airspace/parsers/`
- `backend/airspace/normalizers/`
- `backend/airspace/validators/`
- `backend/airspace/services/`
- `backend/airspace/repositories/`
- `backend/airspace/models/`

## Ingestion pipeline
1. Fetch source data
2. Validate geometry
3. Normalize schema
4. Store raw payload in `raw_airspace_sources`
5. Store normalized zones in `airspace_zones`
6. Activate the new dataset version in `airspace_versions`

## Sources
- `romatsa_wfs_ctr` every 24h
- `romatsa_wfs_tma` every 24h
- `restriction_zones_json` every 24h
- `notam_wfs` every 5m

## Run API
```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8080
```

## Run ingestion once
```bash
python3 scripts/ingest_airspace.py
python3 scripts/ingest_airspace.py --source romatsa_wfs_ctr --source restriction_zones_json
```

## Run scheduler
```bash
python3 -m backend.airspace.ingestion.scheduler
```

## Endpoints
- `GET /airspace/zones?bbox=minLon,minLat,maxLon,maxLat`
- `GET /airspace/zones/near?lat=46.77&lon=23.59&radius_km=10`
- `GET /airspace/check-point?lat=46.77&lon=23.59&alt_m=120`
- `POST /airspace/check-route`

## Database objects
- `raw_airspace_sources`
- `airspace_versions`
- `airspace_zones`
- `airspace_zones_active`
