from __future__ import annotations

from modules.flight_plans.domain.policies import enrich_flight_plan


def cancel_flight_plan(public_id: str, owner: dict, *, repo) -> dict:
    cancelled = repo.cancel(public_id, owner_email=owner["email"])
    if not cancelled:
        raise ValueError("Flight plan cannot be cancelled")
    return enrich_flight_plan(cancelled)
