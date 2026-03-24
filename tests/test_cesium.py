from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.cesium import resolve_cesium_ion_token


class CesiumTokenTests(unittest.TestCase):
    def test_env_value_wins_over_file_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            token_file = Path(tmp_dir) / "drone-cesium-ion-token"
            token_file.write_text("file-token", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "DRONE_CESIUM_ION_TOKEN": "env-token",
                    "DRONE_CESIUM_ION_TOKEN_FILE": str(token_file),
                },
                clear=False,
            ):
                token, source = resolve_cesium_ion_token(root_dir=Path(tmp_dir))

        self.assertEqual(token, "env-token")
        self.assertEqual(source, "env")

    def test_configured_file_is_used_when_env_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            token_file = Path(tmp_dir) / "drone-cesium-ion-token"
            token_file.write_text("file-token", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "DRONE_CESIUM_ION_TOKEN": "",
                    "DRONE_CESIUM_ION_TOKEN_FILE": str(token_file),
                },
                clear=False,
            ):
                token, source = resolve_cesium_ion_token(root_dir=Path(tmp_dir))

        self.assertEqual(token, "file-token")
        self.assertEqual(source, str(token_file))

    def test_repo_secret_file_is_used_as_last_fallback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            token_file = root_dir / ".data" / "secrets" / "drone-cesium-ion-token"
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text("repo-token", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "DRONE_CESIUM_ION_TOKEN": "",
                    "DRONE_CESIUM_ION_TOKEN_FILE": "",
                },
                clear=False,
            ):
                token, source = resolve_cesium_ion_token(root_dir=root_dir)

        self.assertEqual(token, "repo-token")
        self.assertEqual(source, str(token_file))
