from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from modules.auth.application.use_cases.get_current_session_user import get_current_session_user
from modules.auth.application.use_cases.list_logged_accounts import list_logged_accounts
from modules.auth.application.use_cases.register_google_session import register_google_session
from modules.auth.gateways.session_gateway import SessionGateway
from modules.auth.repo.app_user_repo import AppUserRepository
from modules.auth.repo.login_audit_repo import LoginAuditRepository
from modules.auth.schemas.requests import GoogleSessionRequest


@dataclass(slots=True)
class AuthModule:
    login_audit_repo: LoginAuditRepository
    app_user_repo: AppUserRepository
    session_gateway: SessionGateway
    token_payload_decoder: Callable[[str], dict[str, Any]]
    app_user_upsert_errors: tuple[type[BaseException], ...]

    def register_google_session(self, payload: dict[str, Any], source_ip: str) -> dict[str, Any]:
        request = GoogleSessionRequest(
            email=payload.get("email") or "",
            display_name=payload.get("display_name") or "",
            google_user_id=payload.get("google_user_id") or "",
            id_token=payload.get("id_token") or "",
            app=payload.get("app") or "",
            source_ip=source_ip,
        )
        return register_google_session(
            request,
            login_audit_repo=self.login_audit_repo,
            app_user_repo=self.app_user_repo,
            session_gateway=self.session_gateway,
            token_payload_decoder=self.token_payload_decoder,
            app_user_upsert_errors=self.app_user_upsert_errors,
        )

    def current_user(self, headers) -> dict[str, Any] | None:
        return get_current_session_user(headers, session_gateway=self.session_gateway)

    def list_logged_accounts(self) -> list[dict[str, Any]]:
        return list_logged_accounts(login_audit_repo=self.login_audit_repo)

    def clear_cookie_header(self) -> str:
        return self.session_gateway.clear_cookie()


def build_auth_module(
    *,
    logged_accounts_file: Path,
    upsert_user: Callable[[dict[str, Any], str], dict[str, Any]],
    create_token: Callable[[dict[str, Any]], str],
    cookie_header: Callable[[str], str],
    clear_cookie_header: Callable[[], str],
    session_user_from_headers: Callable[[Any], dict[str, Any] | None],
    token_payload_decoder: Callable[[str], dict[str, Any]],
    app_user_upsert_errors: tuple[type[BaseException], ...],
) -> AuthModule:
    return AuthModule(
        login_audit_repo=LoginAuditRepository(logged_accounts_file),
        app_user_repo=AppUserRepository(upsert_user),
        session_gateway=SessionGateway(
            create_token=create_token,
            cookie_header=cookie_header,
            clear_cookie_header=clear_cookie_header,
            session_user_from_headers=session_user_from_headers,
        ),
        token_payload_decoder=token_payload_decoder,
        app_user_upsert_errors=app_user_upsert_errors,
    )
