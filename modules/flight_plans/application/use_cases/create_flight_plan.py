from __future__ import annotations

from pathlib import Path

from modules.flight_plans.domain.policies import enrich_flight_plan


def create_flight_plan(payload: dict, owner: dict, *, repo, gateway, pdf_dir: Path) -> dict:
    plan = gateway.build_plan(payload, owner)
    pdf_path = pdf_dir / f"{plan['public_id']}.pdf"
    plan["pdf_rel_path"] = str(pdf_path.relative_to(pdf_dir.parent.parent if pdf_dir.is_absolute() else pdf_dir.parent))
    plan["anexa_payload"] = gateway.build_pdf_payload(plan)
    gateway.generate_pdf(plan, pdf_path)
    try:
        stored = repo.create(owner, plan)
    except Exception:
        if pdf_path.exists():
            pdf_path.unlink(missing_ok=True)
        raise
    stored["airspace_assessment"] = plan["airspace_assessment"]
    return enrich_flight_plan(stored)
