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
- Additional tables:
  - `app_users`
  - `flight_plans`

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
4. for frontend .venv/bin/python scripts/visualise_zones.py --no-browser

## Google Login + Admin Dashboard
- Google client ID used by the browser frontend and Flutter app:
  - `1082596673448-0k7mnlrj1vt9pkrs1vuh8ar68arsj6mt.apps.googleusercontent.com`
- Frontend map page now opens behind a Google login gate:
  - `http://localhost:5174/`
- Backend endpoint used by mobile app to register logins:
  - `POST /api/auth/google-session`
- Unified admin dashboard:
  - `http://localhost:5174/admin`
- Legacy login audit URL:
  - `http://localhost:5174/admin/logged-accounts`
- Login history is persisted here:
  - `.data/logged_accounts.json`

## Flight Plans
- Frontend wizard:
  - `http://localhost:5174/` then sign in and open `New UAS Notification`
- Backend API used by the frontend:
  - `POST /api/flight-plans/assess`
  - `POST /api/flight-plans`
  - `GET /api/flight-plans?scope=mine&include_past=1`
- Legacy flight plan admin URL:
  - `http://localhost:5174/admin/flight-plans`
- Admin overview API used by the dashboard:
  - `GET /api/admin/overview`
- Generated PDFs are stored here:
  - `.data/flight_plans/`

### OAuth Settings for Admin Page
For local testing with this setup, add this as an Authorized JavaScript origin in your Google OAuth client:

1. `http://localhost:5174`

Start server:
- `.venv/bin/python scripts/visualise_zones.py --no-browser`

## Backend Architecture
- The backend is still a modular monolith, but auth and flight-plan orchestration now live under `modules/`
- Architecture notes:
  - `docs/backend-architecture.md`
- Extracted modules:
  - `modules/auth`
  - `modules/flight_plans`

## Docker
- Files added for containerization:
  - `Dockerfile`
  - `docker-compose.yml`
  - `docker/entrypoint.sh`
  - `requirements.txt`
- Health endpoint:
  - `GET /healthz`
- Default in-container bind:
  - `0.0.0.0:$PORT`
- Default ANEXA 1 template path in repo:
  - `assets/templates/ANEXA1.pdf`

### Run with Docker Compose
1. Set `DRONE_GOOGLE_WEB_CLIENT_ID`
2. Run `docker compose up --build`
3. Open `http://localhost:5174/`

## Airspace Backend
- FastAPI app: `uvicorn backend.app:app --host 0.0.0.0 --port 8080`
- PostGIS schema: `sql/airspace_schema.sql`
- One-shot ingestion: `python3 scripts/ingest_airspace.py`
- Scheduler: `python3 -m backend.airspace.ingestion.scheduler`
- Docker services:
  - legacy map UI: `http://localhost:5174/`
  - airspace API: `http://localhost:8080/healthz`
- Main endpoints:
  - `GET /airspace/zones?bbox=minLon,minLat,maxLon,maxLat`
  - `GET /airspace/zones/near?lat=46.77&lon=23.59&radius_km=10`
  - `GET /airspace/check-point?lat=46.77&lon=23.59&alt_m=120`
  - `POST /airspace/check-route`
- The browser flight-plan flow now uses the PostGIS-backed airspace checks for:
  - circle centre blocking
  - flight-area prechecks
  - `/api/crosscheck`
- Architecture notes: `docs/airspace-backend.md`

## DigitalOcean
- Deployment notes:
  - `docs/digitalocean-deploy.md`
- The backend can now run either:
  - on a Droplet with `docker compose`
  - or in App Platform with an external PostgreSQL database

## Testing
- Unit test runner:
  - `./scripts/run-unit-tests.sh`
- Pre-commit hook setup:
  - `./scripts/setup-git-hooks.sh`
- Pre-commit hook path:
  - `.githooks/pre-commit`
- Coverage gate:
  - `80%` minimum on the extracted backend modules
- Current unit suite runtime:
  - well under `5 minutes`

### E2E
- Playwright config:
  - `playwright.config.ts`
- E2E specs:
  - `e2e/flight-plans.spec.ts`
- Local Docker E2E:
  - `./scripts/run-e2e-compose.sh`
- Staging E2E:
  - `E2E_BASE_URL=https://staging.example.com ./scripts/run-e2e-staging.sh`

## GitHub Actions
- CI/CD pipeline:
  - `.github/workflows/ci-cd.yml`
- Manual Docker E2E workflow:
  - `.github/workflows/e2e-compose.yml`
- Daily staging E2E workflow:
  - `.github/workflows/e2e-staging.yml`
- Runner requirement:
  - all workflows target `self-hosted, build-01`

## Environment Separation
- Example staging env:
  - `env/.env.staging.example`
- Example production env:
  - `env/.env.production.example`
