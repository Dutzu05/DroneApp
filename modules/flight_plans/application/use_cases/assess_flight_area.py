from __future__ import annotations


def assess_flight_area(payload: dict, *, gateway):
    return gateway.assess(payload)
