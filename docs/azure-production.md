# Azure Production Notes

## Deployment Order
1. Deploy the PC/web container with the ASGI entrypoint `backend.web_app:app`.
2. Set production app settings and secrets in Azure before the first public start.
3. Mount persistent storage at `/app/.data`.
4. Validate Google OAuth against the real public origin.
5. On Azure Database for PostgreSQL Flexible Server, allowlist and create `postgis` in the app database before enabling public traffic.
6. Enable monitoring, backups, and rollback using image tags and persistent data snapshots.

## Required Azure App Settings
- `DRONE_ENV=production`
- `DRONE_PUBLIC_BASE_URL=https://your-public-domain`
- `DRONE_ALLOWED_ORIGINS=https://your-public-domain`
- `DRONE_ADMIN_EMAILS=admin1@example.com,admin2@example.com`
- `DRONE_SESSION_SECRET=<long-random-secret>`
- `DRONE_COOKIE_SECURE=1`
- `DRONE_COOKIE_DOMAIN=your-public-domain`
- `DRONE_GOOGLE_WEB_CLIENT_ID=<your-google-web-client-id>`
- `DRONE_CESIUM_ION_TOKEN=<optional-if-3d-terrain-is-needed>`
- `PGHOST=<postgres-host>`
- `PGPORT=5432`
- `PGUSER=<postgres-user>`
- `PGPASSWORD=<postgres-password>`
- `DRONE_DB_NAME=drone_app`
- `WEB_CONCURRENCY=2`

## Azure PostgreSQL Requirements
- This app's airspace module requires PostGIS.
- On Azure Database for PostgreSQL Flexible Server, allowlist `postgis` in the server parameter `azure.extensions`.
- Then connect to the target database and run:
  - `CREATE EXTENSION IF NOT EXISTS postgis;`
- Verify:
  - `SELECT extname FROM pg_extension WHERE extname='postgis';`
  - `SELECT to_regclass('public.airspace_zones');`
  - `SELECT to_regclass('public.airspace_zones_active');`

## Google OAuth Changes
For the current web login flow, you need to update Google OAuth with Authorized JavaScript origins for every real frontend origin that will serve the app.

Add:
- `https://your-public-domain`
- `https://<your-app>.azurewebsites.net`

Keep localhost only if you still use local development:
- `http://localhost:5174`

This web flow does not currently require a redirect URI because it uses the Google Identity Services client-side sign-in flow and sends the ID token to the backend.

## Storage on Azure
The application writes runtime state under `/app/.data`.

Persist this path:
- session secret file if you are not using only `DRONE_SESSION_SECRET`
- login audit JSON
- generated flight-plan PDFs

Recommended:
- mount Azure Files to `/app/.data`
- keep PostgreSQL on Azure Database for PostgreSQL

## Monitoring
Use these endpoints:
- `/healthz` for liveness
- `/readyz` for readiness/configuration validation

The web app now writes structured JSON request logs to stdout. Forward container stdout/stderr to Azure Log Analytics or Application Insights.

## Backups
- Use Azure Database for PostgreSQL automated backups for the database.
- Snapshot or back up the Azure Files share mounted at `/app/.data`.

## Rollback
- Deploy immutable container image tags, not only `latest`.
- Keep the previous image tag available.
- Roll back by redeploying the previous image tag and keeping the same database and `/app/.data` mount.

## CDN Strategy
Current explicit strategy:
- keep pinned external CDN versions for Leaflet and Cesium as referenced in `scripts/visualise_zones.py`
- keep Google Identity Services loaded from Google

If your security policy later requires self-hosting frontend dependencies, that can be done as a separate hardening step.
