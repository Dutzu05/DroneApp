from __future__ import annotations


def can_cancel_flight_plan(plan: dict) -> bool:
    runtime_state = (plan.get("runtime_state") or "").lower()
    workflow_status = (plan.get("workflow_status") or "").lower()
    return workflow_status != "cancelled" and runtime_state != "completed"


def enrich_flight_plan(plan: dict) -> dict:
    enriched = dict(plan)
    if enriched.get("public_id"):
        enriched["download_url"] = f"/api/flight-plans/{enriched['public_id']}/pdf"
    enriched["can_cancel"] = can_cancel_flight_plan(enriched)
    return enriched
