from __future__ import annotations

from typing import Any, Callable


class SessionGateway:
    def __init__(
        self,
        *,
        create_token: Callable[[dict[str, Any]], str],
        cookie_header: Callable[[str], str],
        clear_cookie_header: Callable[[], str],
        session_user_from_headers: Callable[[Any], dict[str, Any] | None],
    ):
        self._create_token = create_token
        self._cookie_header = cookie_header
        self._clear_cookie_header = clear_cookie_header
        self._session_user_from_headers = session_user_from_headers

    def issue_cookie(self, user: dict[str, Any]) -> str:
        return self._cookie_header(self._create_token(user))

    def clear_cookie(self) -> str:
        return self._clear_cookie_header()

    def current_user(self, headers: Any) -> dict[str, Any] | None:
        return self._session_user_from_headers(headers)
