from __future__ import annotations

import unittest
from pathlib import Path

from mira_stylist.config import StylistSettings
from mira_stylist.services.vton_service import VTONService


class VTONServiceCommandSelectionTests(unittest.TestCase):
    def test_explicit_runner_takes_priority(self) -> None:
        settings = StylistSettings(
            storage_root=Path("output/mira_stylist"),
            api_title="MIRA Stylist API",
            api_version="0.1.0",
            max_source_images=12,
            default_render_mode="styled_overlay",
            default_camera_angle="front",
            vton_runner_command="python3 custom_runner.py {request_json} {output_dir}",
            vton_timeout_seconds=45,
            vton_model_path="",
            vton_device="auto",
            vton_dtype="float32",
            vton_num_inference_steps=24,
            vton_guidance_scale=6.5,
            vton_strength=0.88,
            idm_vton_repo_path="/tmp/idm-vton",
            idm_vton_python_bin="python",
            idm_vton_server_url="http://127.0.0.1:7860",
            idm_vton_denoise_steps=30,
            idm_vton_seed=42,
            idm_vton_auto_mask=True,
            idm_vton_auto_crop=False,
        )
        service = VTONService(settings=settings)
        command = service._render_command(request_json=Path("/tmp/request.json"), output_dir=Path("/tmp/out"))
        self.assertEqual(command[:2], ["python3", "custom_runner.py"])

    def test_idm_runner_selected_when_repo_or_server_configured(self) -> None:
        settings = StylistSettings(
            storage_root=Path("output/mira_stylist"),
            api_title="MIRA Stylist API",
            api_version="0.1.0",
            max_source_images=12,
            default_render_mode="styled_overlay",
            default_camera_angle="front",
            vton_runner_command="",
            vton_timeout_seconds=45,
            vton_model_path="",
            vton_device="auto",
            vton_dtype="float32",
            vton_num_inference_steps=24,
            vton_guidance_scale=6.5,
            vton_strength=0.88,
            idm_vton_repo_path="/tmp/idm-vton",
            idm_vton_python_bin="python-idm",
            idm_vton_server_url="http://127.0.0.1:7860",
            idm_vton_denoise_steps=30,
            idm_vton_seed=42,
            idm_vton_auto_mask=True,
            idm_vton_auto_crop=False,
        )
        service = VTONService(settings=settings)
        command = service._render_command(request_json=Path("/tmp/request.json"), output_dir=Path("/tmp/out"))
        self.assertEqual(command[0], "python-idm")
        self.assertTrue(command[1].endswith("idm_vton_runner.py"))

    def test_diffusers_runner_selected_when_model_path_set(self) -> None:
        settings = StylistSettings(
            storage_root=Path("output/mira_stylist"),
            api_title="MIRA Stylist API",
            api_version="0.1.0",
            max_source_images=12,
            default_render_mode="styled_overlay",
            default_camera_angle="front",
            vton_runner_command="",
            vton_timeout_seconds=45,
            vton_model_path="/tmp/model",
            vton_device="auto",
            vton_dtype="float32",
            vton_num_inference_steps=24,
            vton_guidance_scale=6.5,
            vton_strength=0.88,
            idm_vton_repo_path="",
            idm_vton_python_bin="python",
            idm_vton_server_url="",
            idm_vton_denoise_steps=30,
            idm_vton_seed=42,
            idm_vton_auto_mask=True,
            idm_vton_auto_crop=False,
        )
        service = VTONService(settings=settings)
        command = service._render_command(request_json=Path("/tmp/request.json"), output_dir=Path("/tmp/out"))
        self.assertTrue(command[1].endswith("vton_diffusers_runner.py"))


if __name__ == "__main__":
    unittest.main()
