from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from mira_stylist.vton.catvton_engine import CatVTONConfig, CatVTONEngine
from mira_stylist.models.vton import VTONRunResult


class CatVTONEngineTests(unittest.TestCase):
    def test_local_mode_enabled_when_repo_path_present(self) -> None:
        config = CatVTONConfig(mode="local_mps", repo_path="/tmp/catvton", python_bin="python3")
        engine = CatVTONEngine(config=config)
        self.assertTrue(engine.is_enabled())

    def test_remote_mode_requires_base_url(self) -> None:
        config = CatVTONConfig(mode="remote_gpu_api", remote_base_url="")
        engine = CatVTONEngine(config=config)
        self.assertFalse(engine.is_enabled())

    def test_remote_mode_enabled_when_base_url_present(self) -> None:
        config = CatVTONConfig(mode="remote_gpu_api", remote_base_url="http://127.0.0.1:9000")
        engine = CatVTONEngine(config=config)
        self.assertTrue(engine.is_enabled())

    def test_runner_command_template_is_used_when_configured(self) -> None:
        config = CatVTONConfig(
            mode="local_cpu",
            repo_path="/tmp/catvton",
            python_bin="python3",
            runner_command="python3 custom_runner.py {request_json} {output_dir}",
        )
        engine = CatVTONEngine(config=config)
        command = engine._render_command(request_path=Path("/tmp/request.json"), output_dir=Path("/tmp/out"))
        self.assertEqual(command[:2], ["python3", "custom_runner.py"])

    def test_extract_result_payload_uses_last_json_line(self) -> None:
        engine = CatVTONEngine(config=CatVTONConfig(mode="local_cpu", repo_path="/tmp/catvton", python_bin="python3"))
        payload = engine._extract_result_payload(
            "progress line\nanother line\n{\"status\":\"ok\",\"backend\":\"catvton\",\"generated_preview_path\":\"/tmp/out.png\",\"generated_auxiliary_paths\":{},\"notes\":[]}\n"
        )
        self.assertIsNotNone(payload)
        result = VTONRunResult.model_validate(payload) if hasattr(VTONRunResult, "model_validate") else VTONRunResult.parse_obj(payload)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.generated_preview_path, "/tmp/out.png")


if __name__ == "__main__":
    unittest.main()
