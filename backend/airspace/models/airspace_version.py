from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class AirspaceVersion:
    version_id: str
    source: str
    imported_at: datetime
    record_count: int
    checksum: str
    is_active: bool
