from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DOCKER_TOKEN_PATH = Path("/run/drone-secrets/drone-cesium-ion-token")


def resolve_cesium_ion_token(*, root_dir: Path | None = None) -> tuple[str, str | None]:
    raw_value = os.environ.get("DRONE_CESIUM_ION_TOKEN")
    if raw_value is not None and raw_value.strip():
        return raw_value.strip(), "env"

    repo_root = root_dir or ROOT_DIR
    configured_path = (os.environ.get("DRONE_CESIUM_ION_TOKEN_FILE") or "").strip()
    candidate_paths = []
    if configured_path:
        candidate_paths.append(Path(configured_path))
    candidate_paths.append(DEFAULT_DOCKER_TOKEN_PATH)
    candidate_paths.append(repo_root / ".data" / "secrets" / "drone-cesium-ion-token")

    seen_paths: set[Path] = set()
    for path in candidate_paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            token = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        if token:
            return token, str(path)

    return "", None
