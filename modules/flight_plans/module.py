from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from modules.flight_plans.application.use_cases.assess_flight_area import assess_flight_area
from modules.flight_plans.application.use_cases.cancel_flight_plan import cancel_flight_plan
from modules.flight_plans.application.use_cases.create_flight_plan import create_flight_plan
from modules.flight_plans.application.use_cases.list_flight_plans import list_flight_plans
from modules.flight_plans.gateways.pdf_gateway import FlightPlanGateway
from modules.flight_plans.repo.flight_plans_repo import FlightPlansRepository


@dataclass(slots=True)
class FlightPlansModule:
    repo: FlightPlansRepository
    gateway: FlightPlanGateway
    pdf_dir: Path

    def assess(self, payload: dict[str, Any]) -> dict[str, Any]:
        return assess_flight_area(payload, gateway=self.gateway)

    def create(self, payload: dict[str, Any], owner: dict[str, Any]) -> dict[str, Any]:
        return create_flight_plan(payload, owner, repo=self.repo, gateway=self.gateway, pdf_dir=self.pdf_dir)

    def list(self, *, owner_email: str | None, include_past: bool, include_cancelled: bool) -> list[dict[str, Any]]:
        return list_flight_plans(repo=self.repo, owner_email=owner_email, include_past=include_past, include_cancelled=include_cancelled)

    def cancel(self, public_id: str, owner: dict[str, Any]) -> dict[str, Any]:
        return cancel_flight_plan(public_id, owner, repo=self.repo)

    def approve(self, public_id: str, *, approver_email: str, note: str = '') -> dict[str, Any]:
        approved = self.repo.approve(public_id, approver_email=approver_email, note=note)
        if not approved:
            raise ValueError("Flight plan cannot be approved")
        return approved

    def get(self, public_id: str, *, owner_email: str | None = None) -> dict[str, Any] | None:
        return self.repo.get(public_id, owner_email=owner_email)

    def twr_options(self) -> list[dict[str, Any]]:
        return self.gateway.twr_options()


def build_flight_plans_module(
    *,
    pdf_dir: Path,
    create_plan_repo: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    list_plans_repo: Callable[..., list[dict[str, Any]]],
    get_plan_repo: Callable[..., dict[str, Any] | None],
    cancel_plan_repo: Callable[..., dict[str, Any] | None],
    approve_plan_repo: Callable[..., dict[str, Any] | None],
    build_flight_plan: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    build_anexa_payload: Callable[[dict[str, Any]], dict[str, Any]],
    generate_pdf: Callable[[dict[str, Any], Path], Path],
    assess_flight_area_fn: Callable[[dict[str, Any], float], dict[str, Any]],
    build_circle_area: Callable[[float, float, float], dict[str, Any]],
    build_polygon_area: Callable[[list[list[float]]], dict[str, Any]],
    twr_options: Callable[[], list[dict[str, Any]]],
) -> FlightPlansModule:
    return FlightPlansModule(
        repo=FlightPlansRepository(
            create_plan=create_plan_repo,
            list_plans=list_plans_repo,
            get_plan=get_plan_repo,
            cancel_plan=cancel_plan_repo,
            approve_plan=approve_plan_repo,
        ),
        gateway=FlightPlanGateway(
            build_flight_plan=build_flight_plan,
            build_anexa_payload=build_anexa_payload,
            generate_pdf=generate_pdf,
            assess_flight_area=assess_flight_area_fn,
            build_circle_area=build_circle_area,
            build_polygon_area=build_polygon_area,
            twr_options=twr_options,
        ),
        pdf_dir=pdf_dir,
    )
