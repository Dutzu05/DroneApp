from __future__ import annotations

from typing import Any, Callable


class FlightPlansRepository:
    def __init__(
        self,
        *,
        create_plan: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        list_plans: Callable[..., list[dict[str, Any]]],
        get_plan: Callable[..., dict[str, Any] | None],
        cancel_plan: Callable[..., dict[str, Any] | None],
        approve_plan: Callable[..., dict[str, Any] | None],
    ):
        self._create_plan = create_plan
        self._list_plans = list_plans
        self._get_plan = get_plan
        self._cancel_plan = cancel_plan
        self._approve_plan = approve_plan

    def create(self, owner: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        return self._create_plan(owner, plan)

    def list(self, *, owner_email: str | None, include_past: bool, include_cancelled: bool) -> list[dict[str, Any]]:
        return self._list_plans(owner_email=owner_email, include_past=include_past, include_cancelled=include_cancelled)

    def get(self, public_id: str, *, owner_email: str | None = None) -> dict[str, Any] | None:
        return self._get_plan(public_id, owner_email=owner_email)

    def cancel(self, public_id: str, *, owner_email: str) -> dict[str, Any] | None:
        return self._cancel_plan(public_id, owner_email=owner_email)

    def approve(self, public_id: str, *, approver_email: str, note: str = '') -> dict[str, Any] | None:
        return self._approve_plan(public_id, approver_email=approver_email, note=note)
