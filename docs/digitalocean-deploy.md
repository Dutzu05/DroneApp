# DigitalOcean Deployment

## Container baseline
This repo now includes:
- `Dockerfile`
- `docker-compose.yml`
- `docker/entrypoint.sh`
- `requirements.txt`
- `.dockerignore`

The backend serves both the frontend and API from the same Python container.

## Required environment variables
- `DRONE_GOOGLE_WEB_CLIENT_ID`
- `DRONE_PUBLIC_BASE_URL`
- `DRONE_ALLOWED_ORIGINS`
- `DRONE_ADMIN_EMAILS`
- `DRONE_ENV=production`
- `DRONE_SESSION_SECRET`
- `DRONE_DB_NAME` default: `drone_app`
- `PGHOST`
- `PGPORT`
- `PGUSER`
- `PGPASSWORD`
- `DRONE_ANEXA1_TEMPLATE_PATH` default: `/app/assets/templates/ANEXA1.pdf`
- `PORT` default: `5174`
- `DRONE_BIND_HOST` default: `0.0.0.0`

## Local container test
```bash
docker compose up --build
```

App URLs:
- frontend: `http://localhost:5174/`
- logged accounts admin: `http://localhost:5174/admin/logged-accounts`
- flight plans admin: `http://localhost:5174/admin/flight-plans`
- health: `http://localhost:5174/healthz`

## DigitalOcean options
### Option 1: Droplet + Docker Compose
Use when you want full control and persistent local Postgres in the same stack.

High-level steps:
1. create a Docker-ready Ubuntu droplet
2. install Docker + Docker Compose plugin
3. clone the repo onto the droplet
4. set `DRONE_GOOGLE_WEB_CLIENT_ID`
5. run `docker compose up -d --build`
6. put Nginx or Caddy in front for TLS and domain routing

### Option 2: DigitalOcean App Platform + Managed PostgreSQL
Use when you want less server maintenance.

Recommended approach:
1. build from this `Dockerfile`
2. provision a Managed PostgreSQL database in DigitalOcean
3. set `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `DRONE_DB_NAME`
4. set `DRONE_GOOGLE_WEB_CLIENT_ID`
5. keep a persistent volume or object-storage strategy for `.data/`

## Production notes
- `.data/` contains session state, login logs, and generated PDFs. Mount it to persistent storage.
- Update your Google OAuth allowed origins to include the real public domain, not just localhost.
- Restrict `DRONE_ALLOWED_ORIGINS` to the real public frontend origins instead of using wildcard CORS.
- Set `DRONE_ADMIN_EMAILS` so admin dashboard and approval endpoints require an explicit allowlist.
- Set `DRONE_SESSION_SECRET` from your secret manager; do not rely on auto-generated local secrets in production.
- The production web entrypoint is now `backend.web_app:app` and should run behind the default Docker command.
- The backend exposes `/healthz` for container health checks.
- The web app exposes `/readyz` for readiness/config validation.
- `scripts/init-db.sh` is now env-driven and can run against local Postgres, Docker Postgres, or managed Postgres.
