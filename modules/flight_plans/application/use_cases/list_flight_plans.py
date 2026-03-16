from __future__ import annotations

from modules.flight_plans.domain.policies import enrich_flight_plan


def list_flight_plans(*, repo, owner_email: str | None, include_past: bool, include_cancelled: bool) -> list[dict]:
    return [
        enrich_flight_plan(plan)
        for plan in repo.list(
            owner_email=owner_email,
            include_past=include_past,
            include_cancelled=include_cancelled,
        )
    ]
