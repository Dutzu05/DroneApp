from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class AirspaceZone:
    zone_id: str
    version_id: str
    source: str
    name: str
    category: str
    lower_altitude_m: float | None
    upper_altitude_m: float | None
    geometry: dict[str, Any]
    valid_from: datetime | None
    valid_to: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            'zone_id': self.zone_id,
            'version_id': self.version_id,
            'source': self.source,
            'name': self.name,
            'category': self.category,
            'lower_altitude_m': self.lower_altitude_m,
            'upper_altitude_m': self.upper_altitude_m,
            'geometry': self.geometry,
            'valid_from': self.valid_from,
            'valid_to': self.valid_to,
            'metadata': self.metadata,
        }
