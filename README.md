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

## Windows Quick Start
1. Open a new PowerShell window after installing Docker Desktop, Git, and Node.js so the updated `PATH` is picked up.
2. Load the project environment:
   `.\scripts\dev-env.ps1`
3. Start the Docker stack:
   `.\scripts\docker-compose.ps1 up --build`
4. In a second PowerShell window, load the environment again and resolve Flutter packages:
   `.\scripts\dev-env.ps1`
   `Set-Location mobile_app`
   `& ..\.tooling\flutter\bin\flutter.bat pub get`

Notes:
- If Flutter reports that plugin builds require symlink support, enable Windows Developer Mode and restart your terminal.
- The Windows flow uses Docker Compose for PostgreSQL/PostGIS instead of the Bash-only local socket scripts.

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

## 3D Drone View
- The 2D Leaflet map remains the primary interface.
- A lazy-loaded Cesium 3D view can be opened from an active drone card with `3D view`.
- The 3D scene now builds a dedicated `5 km` operating region around the active drone and refreshes while the drone remains live.
- The 3D map is built from:
  - Cesium World Terrain when `DRONE_CESIUM_ION_TOKEN` is configured
  - Google Photorealistic 3D Tiles through Cesium ion when token-backed 3D streaming is available
  - OpenStreetMap imagery tiles as the non-ion fallback basemap
  - the selected drone and its recent telemetry track
  - nearby ongoing aircraft within the same `5 km` region
  - nearby airspace zones rendered as extruded blocks
  - supplemental mock obstacles rendered around the focused drone
- New backend endpoint:
  - `GET /api/drones/<drone_id>/scene-3d`
- Optional terrain relief token:
  - env var `DRONE_CESIUM_ION_TOKEN`
- If `DRONE_CESIUM_ION_TOKEN` is not set, 3D still opens with imagery and airspace, but Cesium terrain and photorealistic 3D tiles stay disabled.

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
   On Windows, if `docker` is not recognized in your current shell, use `.\scripts\docker-compose.ps1 up --build` or open a fresh PowerShell window after Docker Desktop installation.
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
  - Windows: `.\scripts\run-unit-tests.ps1`
- PostGIS ingestion smoke:
  - `./scripts/run-airspace-compose-smoke.sh`
- Drone telemetry compose smoke:
  - `./scripts/run-drone-telemetry-compose-smoke.sh`
- HTTP airspace smoke for staging/production:
  - `AIRSPACE_SMOKE_BASE_URL=https://staging.example.com ./scripts/run-airspace-http-smoke.sh`
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
  - includes real airspace ingestion smoke against PostGIS before Playwright
  - includes mock drone telemetry smoke before Playwright
- Staging E2E:
  - `E2E_BASE_URL=https://staging.example.com ./scripts/run-e2e-staging.sh`
  - includes HTTP airspace smoke before Playwright

## GitHub Actions
- CI/CD pipeline:
  - `.github/workflows/ci-cd.yml`
  - runs syntax, unit coverage, compose integration smoke, then image build
- Manual Docker E2E workflow:
  - `.github/workflows/e2e-compose.yml`
- Daily staging E2E workflow:
  - `.github/workflows/e2e-staging.yml`
  - runs both airspace smoke and Playwright against staging
- Runner requirement:
  - all workflows target `self-hosted, build-01`

## Environment Separation
- Example staging env:
  - `env/.env.staging.example`
- Example production env:
  - `env/.env.production.example`
