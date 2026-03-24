from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from backend.airspace.repositories.db import get_connection
from scripts import visualise_zones as vz


_logger = logging.getLogger("drone.web")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _client_ip(request: Request) -> str:
    forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is None:
        return ""
    return request.client.host or ""


def _log(event: str, **fields) -> None:
    payload = {
        "event": event,
        "service": "drone-web",
        "environment": vz.APP_ENV,
        **fields,
    }
    _logger.info(json.dumps(payload, ensure_ascii=False, default=str))


def _production_config_errors() -> list[str]:
    errors: list[str] = []
    if vz.APP_ENV != "production":
        return errors
    if not vz.PUBLIC_BASE_URL:
        errors.append("DRONE_PUBLIC_BASE_URL is required")
    if not vz.ALLOWED_ORIGINS:
        errors.append("DRONE_ALLOWED_ORIGINS is required")
    if not vz.ADMIN_EMAILS:
        errors.append("DRONE_ADMIN_EMAILS is required")
    if not (os.environ.get("DRONE_SESSION_SECRET") or "").strip():
        errors.append("DRONE_SESSION_SECRET is required")
    if not (os.environ.get("DRONE_GOOGLE_WEB_CLIENT_ID") or "").strip():
        errors.append("DRONE_GOOGLE_WEB_CLIENT_ID is required")
    errors.extend(_airspace_readiness_errors())
    return errors


def _airspace_readiness_errors() -> list[str]:
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
            if cur.fetchone() is None:
                return ["PostGIS extension is not installed in the application database"]

            cur.execute(
                """
                SELECT
                    to_regclass('public.airspace_zones')::text AS airspace_zones,
                    to_regclass('public.airspace_zones_active')::text AS airspace_zones_active
                """
            )
            row = cur.fetchone() or {}
    except Exception as exc:
        return [f"Airspace database readiness check failed: {exc}"]

    errors: list[str] = []
    if not row.get("airspace_zones"):
        errors.append("Required relation public.airspace_zones is missing")
    if not row.get("airspace_zones_active"):
        errors.append("Required relation public.airspace_zones_active is missing")
    return errors


def _json_response(payload, *, status_code: int = 200, ensure_ascii: bool = False, headers: dict[str, str] | None = None) -> Response:
    return Response(
        content=vz._json_bytes(payload, ensure_ascii=ensure_ascii),
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )


def _html_response(content: str | bytes, *, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(content=content, status_code=status_code)


def _render_index_html() -> bytes:
    page = vz.HTML.replace(b"__GOOGLE_CLIENT_ID__", vz.GOOGLE_WEB_CLIENT_ID.encode("utf-8"))
    page = page.replace(
        b"__TOWER_CONTACTS_JSON__",
        json.dumps(vz.TOWER_CONTACTS, ensure_ascii=False).encode("utf-8"),
    )
    page = page.replace(
        b"__CESIUM_ION_TOKEN__",
        vz.CESIUM_ION_TOKEN.encode("utf-8"),
    )
    page = page.replace(
        b"__AUTHORIZED_ORIGIN__",
        (vz.PUBLIC_ORIGIN or "http://localhost:5174").encode("utf-8"),
    )
    return page


def _parse_json_bytes(raw: bytes) -> dict:
    if not raw:
        return {}
    payload = json.loads(raw)
    if isinstance(payload, dict):
        return payload
    raise ValueError("JSON object expected")


def _pdf_path_from_plan(plan: dict) -> Path:
    return vz.SCRIPT_DIR.parent / plan["pdf_rel_path"]


@asynccontextmanager
async def _lifespan(_: FastAPI):
    config_errors = _production_config_errors()
    _log(
        "startup",
        public_base_url=vz.PUBLIC_BASE_URL or None,
        allowed_origins=sorted(vz.ALLOWED_ORIGINS),
        admin_emails=sorted(vz.ADMIN_EMAILS),
        mock_drone_enabled=vz.MOCK_DRONE_ENABLED,
        auto_demo_enabled=vz.AUTO_DEMO_FLIGHT_PLAN_ENABLED,
        cesium_ion_configured=bool(vz.CESIUM_ION_TOKEN),
        cesium_ion_token_source=vz.CESIUM_ION_TOKEN_SOURCE,
        config_errors=config_errors,
    )
    if vz.MOCK_DRONE_ENABLED:
        vz._mock_drone_stop.clear()
        vz._start_mock_drone_loop()
    try:
        yield
    finally:
        vz._mock_drone_stop.set()
        _log("shutdown")


app = FastAPI(title="Drone Web", version="1.0.0", lifespan=_lifespan)

if vz.ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(vz.ALLOWED_ORIGINS),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )


@app.middleware("http")
async def _request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        _log(
            "request_error",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            query=request.url.query or None,
            client_ip=_client_ip(request),
            duration_ms=duration_ms,
            error=str(exc),
        )
        raise
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    _log(
        "request_complete",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        query=request.url.query or None,
        status_code=response.status_code,
        client_ip=_client_ip(request),
        duration_ms=duration_ms,
        user_agent=request.headers.get("user-agent") or None,
    )
    return response


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return _html_response(_render_index_html())


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/admin", response_class=HTMLResponse, response_model=None)
@app.get("/admin/logged-accounts", response_class=HTMLResponse, response_model=None)
@app.get("/admin/flight-plans", response_class=HTMLResponse, response_model=None)
async def admin_page(request: Request) -> Response:
    try:
        vz._require_admin_user(request.headers)
        return _html_response(vz.ADMIN_DASHBOARD_HTML)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=403)


@app.get("/api/auth/sessions")
async def auth_sessions(request: Request) -> Response:
    try:
        vz._require_admin_user(request.headers)
        return _json_response({"accounts": vz._list_logged_accounts()})
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=403)


@app.get("/api/admin/overview")
async def admin_overview(request: Request) -> Response:
    try:
        vz._require_admin_user(request.headers)
        return _json_response(vz._build_admin_overview_response())
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=403)


@app.get("/api/admin/drones/live")
async def admin_drones_live(request: Request) -> Response:
    try:
        vz._require_admin_user(request.headers)
        return _json_response({"drones": vz._list_live_drones_for_admin()})
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=403)


@app.get("/api/auth/me")
async def auth_me(request: Request) -> Response:
    return _json_response({"user": vz._safe_session_user(request.headers)})


@app.get("/api/drones/live")
async def drones_live(request: Request) -> Response:
    try:
        user = vz._require_session_user(request.headers)
        return _json_response(vz._build_live_drone_workspace(user["email"]))
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=401)


@app.get("/api/drones/{drone_id}/scene-3d")
async def drone_scene_3d(drone_id: str, request: Request) -> Response:
    try:
        user = vz._require_session_user(request.headers)
        scene = vz._build_drone_3d_scene(drone_id, owner_email=user["email"], admin_view=False)
        return _json_response(scene, ensure_ascii=False)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=401)
    except LookupError as exc:
        return _json_response({"error": str(exc)}, status_code=404)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.get("/api/admin/drones/{drone_id}/scene-3d")
async def admin_drone_scene_3d(drone_id: str, request: Request) -> Response:
    try:
        vz._require_admin_user(request.headers)
        scene = vz._build_drone_3d_scene(drone_id, owner_email=None, admin_view=True)
        return _json_response(scene, ensure_ascii=False)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=403)
    except LookupError as exc:
        return _json_response({"error": str(exc)}, status_code=404)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.get("/healthz")
async def healthz() -> Response:
    return _json_response({"ok": True})


@app.get("/readyz")
async def readyz() -> Response:
    errors = _production_config_errors()
    if errors:
        return _json_response({"ok": False, "errors": errors}, status_code=503)
    return _json_response({"ok": True})


@app.get("/airspace/zones")
async def airspace_zones(request: Request) -> Response:
    try:
        bbox = vz._parse_bbox_query(request.query_params.get("bbox", ""))
        categories = vz._normalize_airspace_categories(request.query_params.get("categories"))
        result = vz.AIRSPACE_QUERY_SERVICE.get_zones_in_bbox(bbox, categories=categories)
        return _json_response(result, ensure_ascii=False)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=400)


@app.get("/airspace/zones/near")
async def airspace_zones_near(request: Request) -> Response:
    try:
        lat = float(request.query_params.get("lat", 0))
        lon = float(request.query_params.get("lon", 0))
        radius_km = float(request.query_params.get("radius_km", 10))
        categories = vz._normalize_airspace_categories(request.query_params.get("categories"))
        result = vz.AIRSPACE_QUERY_SERVICE.get_zones_near(
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            categories=categories,
        )
        return _json_response(result, ensure_ascii=False)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=400)


@app.get("/airspace/check-point")
async def airspace_check_point(request: Request) -> Response:
    try:
        if vz._check_point is None:
            raise RuntimeError("Airspace backend is not available")
        lon = float(request.query_params.get("lon", 0))
        lat = float(request.query_params.get("lat", 0))
        alt = float(request.query_params.get("alt_m", 120))
        return _json_response(vz._check_point(lon, lat, alt), ensure_ascii=False)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=503)


@app.get("/api/flight-plans/options")
async def flight_plan_options() -> Response:
    return _json_response({"twr_options": vz.FLIGHT_PLANS_MODULE.twr_options()}, ensure_ascii=False)


@app.get("/api/flight-plans")
async def list_flight_plans(request: Request) -> Response:
    try:
        scope = (request.query_params.get("scope", "mine") or "mine").lower()
        include_past = (request.query_params.get("include_past", "0") or "0") in ("1", "true", "yes")
        include_cancelled = (request.query_params.get("include_cancelled", "1") or "1") in ("1", "true", "yes")
        if scope == "all":
            vz._require_admin_user(request.headers)
            owner_email = None
        else:
            owner_email = vz._require_session_user(request.headers)["email"]
        plans = vz._list_flight_plans_response(
            owner_email=owner_email,
            include_past=include_past,
            include_cancelled=include_cancelled,
        )
        return _json_response({"flight_plans": plans}, ensure_ascii=False)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=401)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.get("/api/flight-plans/{public_id}/pdf")
async def flight_plan_pdf(public_id: str, request: Request) -> Response:
    try:
        user = vz._require_session_user(request.headers)
        owner_email = None if vz._is_admin_user(user) else user["email"]
        plan = vz.FLIGHT_PLANS_MODULE.get(public_id, owner_email=owner_email)
        if not plan:
            return _json_response({"error": "Flight plan not found"}, status_code=404)
        pdf_path = _pdf_path_from_plan(plan)
        if not pdf_path.exists():
            raise FileNotFoundError(f"Generated PDF missing for {public_id}")
        return Response(
            content=pdf_path.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{public_id}.pdf"'},
        )
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=401)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.get("/api/crosscheck")
async def crosscheck(request: Request) -> Response:
    try:
        lon = float(request.query_params.get("lon", 0))
        lat = float(request.query_params.get("lat", 0))
        alt = float(request.query_params.get("alt", 120))
        return _json_response(vz.do_crosscheck(lon, lat, alt))
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.get("/api/area_check")
async def area_check(request: Request) -> Response:
    try:
        lon = float(request.query_params.get("lon", 0))
        lat = float(request.query_params.get("lat", 0))
        radius = float(request.query_params.get("radius", 200))
        alt = float(request.query_params.get("alt", 120))
        if vz._area_check is None:
            raise RuntimeError("flight_plan_manager not loaded")
        return _json_response(vz._area_check(lon, lat, radius, alt), ensure_ascii=False)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.post("/api/auth/google-session")
async def auth_google_session(request: Request) -> Response:
    try:
        payload = _parse_json_bytes(await request.body())
        result = vz.AUTH_MODULE.register_google_session(payload, _client_ip(request))
        return _json_response(
            {"ok": True, "user": result["user"]},
            headers={"Set-Cookie": result["set_cookie"]},
        )
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=400)


@app.post("/api/auth/logout")
async def auth_logout() -> Response:
    return _json_response({"ok": True}, headers={"Set-Cookie": vz.AUTH_MODULE.clear_cookie_header()})


@app.post("/api/demo/bootstrap")
async def demo_bootstrap(request: Request) -> Response:
    try:
        owner = vz._require_session_user(request.headers)
        result = vz._bootstrap_demo_flight_plan(owner)
        return _json_response(result, ensure_ascii=False)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=401)
    except vz._flight_plan_error as exc:
        return _json_response({"error": str(exc)}, status_code=400)
    except vz.FlightPlanRepositoryError as exc:
        return _json_response({"error": str(exc)}, status_code=500)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.post("/api/flight-plans/assess")
async def assess_flight_plan(request: Request) -> Response:
    try:
        data = _parse_json_bytes(await request.body())
        result = vz.FLIGHT_PLANS_MODULE.assess(data)
        return _json_response(result, ensure_ascii=False)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=400)


@app.post("/airspace/check-route")
async def airspace_check_route(request: Request) -> Response:
    try:
        if vz._check_route is None:
            raise RuntimeError("Airspace backend is not available")
        data = _parse_json_bytes(await request.body())
        path_points = data.get("path") or []
        result = vz._check_route(path_points)
        return _json_response(result, ensure_ascii=False)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=400)


@app.post("/api/admin/flight-plans/{public_id}/approve")
async def approve_flight_plan(public_id: str, request: Request) -> Response:
    try:
        admin_user = vz._require_admin_user(request.headers)
        raw_body = await request.body()
        payload = _parse_json_bytes(raw_body) if raw_body else {}
        approver_email = str((payload or {}).get("approver_email") or admin_user["email"])
        note = str((payload or {}).get("note") or "")
        approved = vz._approve_flight_plan(public_id, approver_email=approver_email, note=note)
        return _json_response({"ok": True, "flight_plan": approved}, ensure_ascii=False)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.post("/api/flight-plans/{public_id}/cancel")
async def cancel_flight_plan(public_id: str, request: Request) -> Response:
    try:
        owner = vz._require_session_user(request.headers)
        cancelled = vz._cancel_owned_flight_plan(public_id, owner)
        return _json_response({"ok": True, "flight_plan": cancelled}, ensure_ascii=False)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=401)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.post("/api/flight-plans")
async def create_flight_plan(request: Request) -> Response:
    try:
        owner = vz._require_session_user(request.headers)
        payload = _parse_json_bytes(await request.body())
        plan = vz._create_flight_plan_from_payload(payload, owner)
        return _json_response({"flight_plan": plan}, status_code=201, ensure_ascii=False)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, status_code=401)
    except vz._flight_plan_error as exc:
        return _json_response({"error": str(exc)}, status_code=400)
    except vz.FlightPlanRepositoryError as exc:
        return _json_response({"error": str(exc)}, status_code=500)
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.post("/api/generate_pdf")
async def generate_pdf(request: Request) -> Response:
    try:
        data = _parse_json_bytes(await request.body())
        if vz._generate_anexa1_pdf is None:
            raise RuntimeError("flight_plan_manager not loaded")
        legacy_email = " ".join(str(data.get("operator_email") or data.get("email") or "legacy@example.com").strip().split())
        legacy_name = " ".join(str(data.get("operator_name") or data.get("operator") or "Legacy User").strip().split())
        owner = {
            "email": legacy_email,
            "display_name": legacy_name,
            "google_user_id": "",
        }
        legacy_payload = {
            "operator_name": data.get("operator") or "",
            "operator_contact": data.get("date_contact") or "",
            "contact_person": data.get("pers_contact") or data.get("operator") or "",
            "phone_landline": data.get("telefon_fix") or "",
            "phone_mobile": data.get("mobil") or "",
            "fax": data.get("fax") or "",
            "operator_email": data.get("email") or "",
            "uas_registration": data.get("inmatriculare") or "",
            "uas_class_code": data.get("clasa") or "C2",
            "category": data.get("categorie") or "A2",
            "operation_mode": data.get("mod_operare") or "VLOS",
            "mtom_kg": data.get("greutate") or "1",
            "pilot_name": data.get("pilot_name") or "",
            "pilot_phone": data.get("pilot_phone") or "",
            "purpose": data.get("scop_zbor") or "",
            "max_altitude_m": data.get("alt_max_m") or "120",
            "start_date": vz.datetime.utcnow().strftime("%Y-%m-%d"),
            "end_date": vz.datetime.utcnow().strftime("%Y-%m-%d"),
            "start_time": data.get("ora_start") or "08:00",
            "end_time": data.get("ora_end") or "09:00",
            "location_name": data.get("localitatea") or "",
            "selected_twr": data.get("twr") or "LRBV",
            "timezone": "Europe/Bucharest",
            "created_from_app": "legacy_generate_pdf",
            "area_kind": "polygon" if data.get("polygon") else "circle",
            "center_lon": data.get("center_lon"),
            "center_lat": data.get("center_lat"),
            "radius_m": data.get("radius_m"),
            "polygon_points": data.get("polygon"),
        }
        plan = vz._build_flight_plan(legacy_payload, owner)
        pdf_path = Path("/tmp/anexa1_filled.pdf")
        vz._generate_anexa1_pdf(plan, pdf_path)
        return Response(
            content=pdf_path.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="ANEXA1_filled.pdf"'},
        )
    except Exception as exc:
        return _json_response({"error": str(exc)}, status_code=500)


@app.get("/api/{layer_key:path}")
async def api_layer(layer_key: str) -> Response:
    fpath = vz.LAYER_FILES.get(layer_key.rstrip("/"))
    if fpath and fpath.exists():
        return Response(content=fpath.read_bytes(), media_type="application/json")
    return _json_response({"error": f"Layer '{layer_key.rstrip('/')}' not found"}, status_code=404)
