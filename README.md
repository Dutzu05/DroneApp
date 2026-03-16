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

## Google Login + Logged Accounts Viewer
- Google client ID used by the browser frontend and Flutter app:
  - `1082596673448-0k7mnlrj1vt9pkrs1vuh8ar68arsj6mt.apps.googleusercontent.com`
- Frontend map page now opens behind a Google login gate:
  - `http://localhost:5174/`
- Backend endpoint used by mobile app to register logins:
  - `POST /api/auth/google-session`
- Admin page showing logged accounts:
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
- Admin page showing all stored flight plans and who created them:
  - `http://localhost:5174/admin/flight-plans`
- Generated PDFs are stored here:
  - `.data/flight_plans/`

### OAuth Settings for Admin Page
For local testing with this setup, add this as an Authorized JavaScript origin in your Google OAuth client:

1. `http://localhost:5174`

Start server:
- `.venv/bin/python scripts/visualise_zones.py --no-browser`
