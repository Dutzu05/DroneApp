# Backend Architecture

This backend is still a modular monolith, but the auth and flight-plan flows now follow a clearer module boundary so the project can keep moving toward the target backend module standard.

## Current extracted modules

### `modules/auth`
- `application/use_cases/register_google_session.py`
- `application/use_cases/get_current_session_user.py`
- `application/use_cases/list_logged_accounts.py`
- `repo/login_audit_repo.py`
- `repo/app_user_repo.py`
- `gateways/session_gateway.py`
- `schemas/requests.py`
- `module.py`

Responsibilities:
- persist Google login audit history
- normalize current session user
- isolate signed session-cookie issuance from HTTP handlers
- keep DB user upsert behind a repository adapter

### `modules/flight_plans`
- `application/use_cases/assess_flight_area.py`
- `application/use_cases/create_flight_plan.py`
- `application/use_cases/list_flight_plans.py`
- `application/use_cases/cancel_flight_plan.py`
- `domain/policies.py`
- `repo/flight_plans_repo.py`
- `gateways/pdf_gateway.py`
- `module.py`

Responsibilities:
- assess flight areas
- create/cancel/list flight plans
- isolate PDF generation and ANEXA 1 mapping behind a gateway
- isolate persistence behind a repository adapter
- keep cancelability and response enrichment in module-local domain policy

## What still remains in `scripts/visualise_zones.py`
- HTTP transport handling
- HTML/JS frontend shell
- static asset serving
- PDF download transport response
- legacy `/api/generate_pdf` compatibility endpoint
- map cross-check transport logic

That is intentional for now. The route contracts remain stable while business orchestration is moved behind module boundaries.

## Next recommended refactor steps
- move the HTTP routes for auth and flight plans into `modules/<name>/handler/http.py`
- introduce request/response DTOs for the larger flight-plan payloads
- split admin HTML routes from the map frontend handler
- add repository tests against a disposable PostgreSQL instance
- add a dedicated health/readiness service instead of inline route responses
