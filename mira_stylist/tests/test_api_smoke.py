from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


try:
    from fastapi.testclient import TestClient
    from mira_stylist.api.app import create_app
    from mira_stylist.api.dependencies import get_services

    HAS_FASTAPI = True
except ModuleNotFoundError:
    HAS_FASTAPI = False
    TestClient = None  # type: ignore[assignment]
    create_app = None  # type: ignore[assignment]
    get_services = None  # type: ignore[assignment]


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jx1cAAAAASUVORK5CYII="
)


@unittest.skipUnless(HAS_FASTAPI, "fastapi/testclient not installed")
class StylistApiSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="mira_stylist_test_")
        os.environ["MIRA_STYLIST_STORAGE_ROOT"] = self.temp_dir
        os.environ["MIRA_STYLIST_VTON_RUNNER"] = (
            "python3 mira_stylist/tools/vton_stub_runner.py {request_json} {output_dir}"
        )
        assert get_services is not None
        get_services.cache_clear()
        assert create_app is not None
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        assert get_services is not None
        get_services.cache_clear()
        os.environ.pop("MIRA_STYLIST_VTON_RUNNER", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "mira_stylist")

    def test_end_to_end_image_upload_to_preview(self) -> None:
        scan = self.client.post(
            "/avatars/scan-session",
            json={"user_id": "api_demo", "source_type": "image-estimated"},
        )
        self.assertEqual(scan.status_code, 200)
        scan_id = scan.json()["scan_session_id"]

        avatar = self.client.post(
            "/avatars",
            json={
                "user_id": "api_demo",
                "scan_session_id": scan_id,
                "source_type": "image-estimated",
                "display_name": "API Demo",
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_id = avatar.json()["avatar_id"]

        upload = self.client.post(
            "/garments/ingest/image-upload",
            data={"uploaded_by": "api_demo", "title": "Smoke Shirt"},
            files={"file": ("smoke.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(upload.status_code, 200)
        upload_data = upload.json()
        self.assertTrue(upload_data["garment_id"])
        garment_id = upload_data["garment_id"]

        garment = self.client.get(f"/garments/{garment_id}")
        self.assertEqual(garment.status_code, 200)
        self.assertEqual(garment.json()["category"], "top")

        preview = self.client.post(
            "/tryon/preview",
            json={
                "avatar_id": avatar_id,
                "garment_id": garment_id,
                "pose": "neutral",
                "camera_angle": "front",
                "render_mode": "styled_overlay",
            },
        )
        self.assertEqual(preview.status_code, 200)
        preview_data = preview.json()
        preview_path = Path(preview_data["result"]["output_asset_paths"]["preview_front"])
        self.assertTrue(preview_path.exists())
        self.assertIn("stylist_commentary", preview_data["result"])
        self.assertTrue(preview_data["result"]["stylist_commentary"]["summary"])
        feedback = self.client.post(
            "/tryon/feedback",
            json={
                "job_id": preview_data["job_id"],
                "question": "Is this flattering for dinner?",
                "occasion": "dinner",
                "style_goal": "polished",
            },
        )
        self.assertEqual(feedback.status_code, 200)
        feedback_data = feedback.json()
        self.assertEqual(feedback_data["job_id"], preview_data["job_id"])
        self.assertTrue(feedback_data["answer"])
        self.assertGreaterEqual(len(feedback_data["supporting_points"]), 1)
        pairing = self.client.post(
            "/tryon/pairing",
            json={
                "avatar_id": avatar_id,
                "garment_id": garment_id,
                "occasion": "dinner",
                "style_goal": "polished",
                "weather_hint": "cool",
            },
        )
        self.assertEqual(pairing.status_code, 200)
        pairing_data = pairing.json()
        self.assertEqual(pairing_data["avatar_id"], avatar_id)
        self.assertEqual(pairing_data["garment_id"], garment_id)
        self.assertGreaterEqual(len(pairing_data["recommendations"]), 2)
        self.assertIn("summary", pairing_data)

    def test_compare_two_looks(self) -> None:
        avatar = self.client.post(
            "/avatars/photo-profile",
            json={
                "user_id": "compare_demo",
                "display_name": "Compare Demo",
                "front_image_base64": base64.b64encode(PNG_1X1).decode("ascii"),
                "side_image_base64": base64.b64encode(PNG_1X1).decode("ascii"),
                "front_original_filename": "front.png",
                "side_original_filename": "side.png",
                "height_cm": 176,
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_id = avatar.json()["avatar_id"]

        top_upload = self.client.post(
            "/garments/ingest/image-upload",
            data={"uploaded_by": "compare_demo", "title": "Structured Blazer", "category_hint": "outerwear", "color": "black"},
            files={"file": ("blazer.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(top_upload.status_code, 200)
        top_garment_id = top_upload.json()["garment_id"]

        dress_upload = self.client.post(
            "/garments/ingest/image-upload",
            data={"uploaded_by": "compare_demo", "title": "Soft Dress", "category_hint": "dress", "color": "green"},
            files={"file": ("dress.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(dress_upload.status_code, 200)
        dress_garment_id = dress_upload.json()["garment_id"]

        preview_a = self.client.post(
            "/tryon/preview",
            json={
                "avatar_id": avatar_id,
                "garment_id": top_garment_id,
                "pose": "neutral",
                "camera_angle": "front",
                "render_mode": "styled_overlay",
            },
        )
        self.assertEqual(preview_a.status_code, 200)
        job_a = preview_a.json()["job_id"]

        preview_b = self.client.post(
            "/tryon/preview",
            json={
                "avatar_id": avatar_id,
                "garment_id": dress_garment_id,
                "pose": "neutral",
                "camera_angle": "front",
                "render_mode": "styled_overlay",
            },
        )
        self.assertEqual(preview_b.status_code, 200)
        job_b = preview_b.json()["job_id"]

        comparison = self.client.post(
            "/tryon/compare",
            json={
                "primary_job_id": job_a,
                "secondary_job_id": job_b,
                "occasion": "dinner",
                "style_goal": "polished",
            },
        )
        self.assertEqual(comparison.status_code, 200)
        comparison_data = comparison.json()
        self.assertIn(comparison_data["winner_job_id"], {job_a, job_b})
        self.assertTrue(comparison_data["verdict"])
        self.assertGreaterEqual(len(comparison_data["decision_factors"]), 1)

    def test_generate_composed_outfit(self) -> None:
        avatar = self.client.post(
            "/avatars/photo-profile",
            json={
                "user_id": "outfit_demo",
                "display_name": "Outfit Demo",
                "front_image_base64": base64.b64encode(PNG_1X1).decode("ascii"),
                "side_image_base64": base64.b64encode(PNG_1X1).decode("ascii"),
                "front_original_filename": "front.png",
                "side_original_filename": "side.png",
                "height_cm": 174,
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_id = avatar.json()["avatar_id"]

        upload = self.client.post(
            "/garments/ingest/image-upload",
            data={"uploaded_by": "outfit_demo", "title": "Dinner Top", "category_hint": "top", "color": "blue"},
            files={"file": ("top.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(upload.status_code, 200)
        garment_id = upload.json()["garment_id"]

        outfit = self.client.post(
            "/outfits/generate",
            json={
                "avatar_id": avatar_id,
                "anchor_garment_id": garment_id,
                "occasion": "dinner",
                "style_goal": "polished",
                "weather_hint": "cool",
            },
        )
        self.assertEqual(outfit.status_code, 200)
        outfit_data = outfit.json()
        self.assertGreaterEqual(len(outfit_data["components"]), 3)
        self.assertTrue(Path(outfit_data["output_asset_paths"]["preview_front"]).exists())
        self.assertIn("outfit_id", outfit_data)

        fetched = self.client.get(f"/outfits/{outfit_data['outfit_id']}")
        self.assertEqual(fetched.status_code, 200)
        fetched_data = fetched.json()
        self.assertEqual(fetched_data["outfit_id"], outfit_data["outfit_id"])

    def test_generate_composed_outfit_for_quick_avatar(self) -> None:
        payload = base64.b64encode(PNG_1X1).decode("ascii")
        avatar = self.client.post(
            "/avatars/quick-tryon",
            json={
                "user_id": "quick_outfit_demo",
                "display_name": "Quick Outfit Demo",
                "image_base64": payload,
                "original_filename": "quick.png",
                "mime_type": "image/png",
                "height_cm": 172,
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_id = avatar.json()["avatar_id"]

        upload = self.client.post(
            "/garments/ingest/image-upload",
            data={"uploaded_by": "quick_outfit_demo", "title": "Quick Demo Jacket", "category_hint": "outerwear", "color": "black"},
            files={"file": ("jacket.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(upload.status_code, 200)
        garment_id = upload.json()["garment_id"]

        outfit = self.client.post(
            "/outfits/generate",
            json={
                "avatar_id": avatar_id,
                "anchor_garment_id": garment_id,
                "occasion": "date",
                "style_goal": "clean",
                "weather_hint": "cool",
            },
        )
        self.assertEqual(outfit.status_code, 200)
        outfit_data = outfit.json()
        preview_markup = Path(outfit_data["output_asset_paths"]["preview_front"]).read_text(encoding="utf-8")
        self.assertIn("anchor_mode=quick_photo_stabilized", preview_markup)
        self.assertIn("composition=photo_grounded", preview_markup)
        self.assertIn("texture=source_image", preview_markup)
        self.assertIn("replacement=upper_body_heuristic", preview_markup)
        self.assertIn("Companion pieces are generated styling placeholders", preview_markup)

    def test_invalid_pasted_image_rejected(self) -> None:
        response = self.client.post(
            "/garments/ingest/pasted-image",
            json={
                "uploaded_by": "api_demo",
                "image_base64": "not-valid-base64",
                "title": "Bad Payload",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_photo_profile_avatar_flow(self) -> None:
        payload = base64.b64encode(PNG_1X1).decode("ascii")
        avatar = self.client.post(
            "/avatars/photo-profile",
            json={
                "user_id": "photo_demo",
                "display_name": "Photo Demo",
                "front_image_base64": payload,
                "side_image_base64": payload,
                "front_original_filename": "front.png",
                "side_original_filename": "side.png",
                "height_cm": 178,
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_data = avatar.json()
        self.assertEqual(avatar_data["source_type"], "image-estimated")
        self.assertTrue(Path(avatar_data["assets"]["front_capture_path"]).exists())
        self.assertTrue(Path(avatar_data["assets"]["side_capture_path"]).exists())
        self.assertTrue(Path(avatar_data["assets"]["side_preview_image_path"]).exists())
        self.assertGreaterEqual(avatar_data["body_profile"]["profile_confidence"], 0.5)
        metadata_dir = Path(avatar_data["assets"]["body_profile_path"]).parent
        (metadata_dir / "vision_side.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "provider": "apple_vision_body_pose",
                    "image_width": 100,
                    "image_height": 200,
                    "points": {
                        "leftShoulder": {"x": 0.41, "y": 0.74, "confidence": 0.9},
                        "rightShoulder": {"x": 0.56, "y": 0.74, "confidence": 0.92},
                        "leftHip": {"x": 0.44, "y": 0.47, "confidence": 0.88},
                        "rightHip": {"x": 0.54, "y": 0.47, "confidence": 0.9},
                        "leftKnee": {"x": 0.45, "y": 0.22, "confidence": 0.8},
                        "rightKnee": {"x": 0.54, "y": 0.22, "confidence": 0.82},
                        "leftAnkle": {"x": 0.45, "y": 0.08, "confidence": 0.76},
                        "rightAnkle": {"x": 0.54, "y": 0.08, "confidence": 0.78},
                    },
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )
        mask_path = metadata_dir.parent / "captures" / "side_person_mask.png"
        mask_path.write_bytes(PNG_1X1)
        (metadata_dir / "segmentation_side.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "provider": "apple_vision_person_segmentation",
                    "mask_path": str(mask_path),
                    "bbox_x": 0.28,
                    "bbox_y": 0.12,
                    "bbox_width": 0.44,
                    "bbox_height": 0.78,
                    "coverage_score": 0.41,
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )

        upload = self.client.post(
            "/garments/ingest/image-upload",
            data={"uploaded_by": "photo_demo", "title": "Photo Demo Blazer", "category_hint": "outerwear"},
            files={"file": ("photo-demo.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(upload.status_code, 200)
        garment_id = upload.json()["garment_id"]

        preview = self.client.post(
            "/tryon/preview",
            json={
                "avatar_id": avatar_data["avatar_id"],
                "garment_id": garment_id,
                "pose": "neutral",
                "camera_angle": "side",
                "render_mode": "styled_overlay",
            },
        )
        self.assertEqual(preview.status_code, 200)
        preview_markup = Path(preview.json()["result"]["output_asset_paths"]["preview_side"]).read_text(encoding="utf-8")
        self.assertIn("profile_conf=", preview_markup)
        self.assertIn("frame=", preview_markup)
        self.assertIn("composition=photo_grounded", preview_markup)
        self.assertIn("data:image/png;base64,", preview_markup)
        self.assertIn("texture=source_image", preview_markup)
        self.assertIn("mask-source=person_segmentation", preview_markup)
        self.assertIn("replacement=upper_body_heuristic", preview_markup)
        self.assertIn("anchor_mode=vision_pose_guided", preview_markup)
        self.assertIn(preview.json()["result"]["stylist_commentary"]["confidence_label"], {"medium", "high"})
        preview_front_path = preview.json()["result"]["output_asset_paths"]["preview_front"]
        self.assertTrue(
            preview_front_path.endswith(("front_vton.svg", "catvton_result.png", "remote_tryon_result.png"))
        )
        self.assertIn("vton_metadata", preview.json()["result"]["output_asset_paths"])
        self.assertIn("VTON backend", preview.json()["result"]["notes"][0])

    def test_photo_grounded_bottom_preview_with_segmentation(self) -> None:
        payload = base64.b64encode(PNG_1X1).decode("ascii")
        avatar = self.client.post(
            "/avatars/quick-tryon",
            json={
                "user_id": "bottom_demo",
                "display_name": "Bottom Demo",
                "image_base64": payload,
                "original_filename": "bottom.png",
                "mime_type": "image/png",
                "height_cm": 180,
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_data = avatar.json()
        metadata_dir = Path(avatar_data["assets"]["body_profile_path"]).parent
        mask_path = metadata_dir.parent / "captures" / "front_person_mask.png"
        mask_path.write_bytes(PNG_1X1)
        (metadata_dir / "segmentation_front.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "provider": "apple_vision_person_segmentation",
                    "mask_path": str(mask_path),
                    "bbox_x": 0.24,
                    "bbox_y": 0.08,
                    "bbox_width": 0.52,
                    "bbox_height": 0.84,
                    "coverage_score": 0.48,
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )

        upload = self.client.post(
            "/garments/ingest/image-upload",
            data={"uploaded_by": "bottom_demo", "title": "Tailored Trouser", "category_hint": "bottom", "color": "black"},
            files={"file": ("trouser.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(upload.status_code, 200)

        preview = self.client.post(
            "/tryon/preview",
            json={
                "avatar_id": avatar_data["avatar_id"],
                "garment_id": upload.json()["garment_id"],
                "pose": "neutral",
                "camera_angle": "front",
                "render_mode": "styled_overlay",
            },
        )
        self.assertEqual(preview.status_code, 200)
        preview_markup = Path(preview.json()["result"]["output_asset_paths"]["preview_front"]).read_text(encoding="utf-8")
        self.assertIn("mask-source=person_segmentation", preview_markup)
        self.assertIn("texture=source_image", preview_markup)
        self.assertIn("replacement=lower_body_heuristic", preview_markup)

    def test_quick_tryon_avatar_flow(self) -> None:
        payload = base64.b64encode(PNG_1X1).decode("ascii")
        avatar = self.client.post(
            "/avatars/quick-tryon",
            json={
                "user_id": "quick_demo",
                "display_name": "Quick Demo",
                "image_base64": payload,
                "original_filename": "quick.png",
                "mime_type": "image/png",
                "height_cm": 171,
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_data = avatar.json()
        self.assertEqual(avatar_data["body_profile"]["posture_hint"], "single_photo")
        self.assertLessEqual(avatar_data["body_profile"]["profile_confidence"], 0.52)
        self.assertTrue(Path(avatar_data["assets"]["front_capture_path"]).exists())
        self.assertIsNone(avatar_data["assets"]["side_capture_path"])

    def test_scan_beta_session_bundle_and_build_flow(self) -> None:
        session = self.client.post(
            "/avatars/scan-beta/session",
            json={
                "user_id": "scan_demo",
                "display_name": "Scan Demo",
                "capture_device_model": "iPhone 15 Pro",
                "has_lidar": True,
                "expected_frame_count": 145,
                "expected_depth_frame_count": 92,
                "image_resolution": "1920x1440",
            },
        )
        self.assertEqual(session.status_code, 200)
        session_id = session.json()["scan_session_id"]

        bundle = self.client.post(
            f"/avatars/scan-beta/session/{session_id}/capture-bundle",
            json={
                "upload_mode": "preview_plus_metadata",
                "rgb_frame_count": 145,
                "depth_frame_count": 92,
                "lidar_point_count": 42000,
                "image_resolution": "1920x1440",
                "depth_resolution": "256x192",
                "duration_ms": 8000,
                "coverage_hint": 0.74,
                "preview_image_base64": base64.b64encode(PNG_1X1).decode("ascii"),
                "preview_original_filename": "scan-preview.png",
                "preview_mime_type": "image/png",
            },
        )
        self.assertEqual(bundle.status_code, 200)
        bundle_data = bundle.json()
        self.assertTrue(Path(bundle_data["preview_image_path"]).exists())
        self.assertGreater(bundle_data["coverage_score"], 0.5)

        avatar = self.client.post(
            "/avatars/scan-beta/build",
            json={
                "scan_session_id": session_id,
                "display_name": "Scan Avatar",
                "height_cm": 180,
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_data = avatar.json()
        self.assertEqual(avatar_data["scan_session_id"], session_id)
        self.assertEqual(avatar_data["body_profile"]["posture_hint"], "scan_beta")
        self.assertGreaterEqual(avatar_data["body_profile"]["profile_confidence"], 0.5)

    def test_screenshot_candidate_selection_flow(self) -> None:
        screenshot = self.client.post(
            "/garments/ingest/screenshot",
            json={
                "uploaded_by": "api_demo",
                "image_base64": base64.b64encode(PNG_1X1).decode("ascii"),
                "original_filename": "screen.png",
                "mime_type": "image/png",
                "title": "Ambiguous Look",
            },
        )
        self.assertEqual(screenshot.status_code, 200)
        screenshot_data = screenshot.json()
        self.assertGreaterEqual(len(screenshot_data["candidate_images"]), 2)
        self.assertIsNone(screenshot_data["garment_id"])

        first_candidate = screenshot_data["candidate_images"][0]
        selected = self.client.post(
            "/garments/ingest/select-candidate",
            json={
                "input_id": screenshot_data["input_id"],
                "selected_candidate_id": first_candidate["candidate_id"],
                "selected_source_image_id": first_candidate["source_image_id"],
                "title": "Selected Screenshot Garment",
                "category_hint": "top",
            },
        )
        self.assertEqual(selected.status_code, 200)
        selected_data = selected.json()
        self.assertEqual(selected_data["category"], "top")
        self.assertTrue(Path(selected_data["primary_image_path"]).exists())

    def test_records_reload_after_service_cache_clear(self) -> None:
        scan = self.client.post(
            "/avatars/scan-session",
            json={"user_id": "persist_demo", "source_type": "image-estimated"},
        )
        self.assertEqual(scan.status_code, 200)
        scan_id = scan.json()["scan_session_id"]

        avatar = self.client.post(
            "/avatars",
            json={
                "user_id": "persist_demo",
                "scan_session_id": scan_id,
                "source_type": "image-estimated",
                "display_name": "Persist Demo",
            },
        )
        self.assertEqual(avatar.status_code, 200)
        avatar_id = avatar.json()["avatar_id"]

        upload = self.client.post(
            "/garments/ingest/image-upload",
            data={"uploaded_by": "persist_demo", "title": "Persist Jacket", "category_hint": "outerwear"},
            files={"file": ("persist.png", PNG_1X1, "image/png")},
        )
        self.assertEqual(upload.status_code, 200)
        garment_id = upload.json()["garment_id"]

        preview = self.client.post(
            "/tryon/preview",
            json={
                "avatar_id": avatar_id,
                "garment_id": garment_id,
                "pose": "neutral",
                "camera_angle": "side",
                "render_mode": "styled_overlay",
            },
        )
        self.assertEqual(preview.status_code, 200)
        preview_data = preview.json()
        job_id = preview_data["job_id"]

        assert get_services is not None
        get_services.cache_clear()
        assert create_app is not None
        self.client = TestClient(create_app())

        avatar_reload = self.client.get(f"/avatars/{avatar_id}")
        self.assertEqual(avatar_reload.status_code, 200)

        garment_reload = self.client.get(f"/garments/{garment_id}")
        self.assertEqual(garment_reload.status_code, 200)

        job_reload = self.client.get(f"/tryon/jobs/{job_id}")
        self.assertEqual(job_reload.status_code, 200)
        reloaded_job = job_reload.json()
        preview_path = Path(reloaded_job["result"]["output_asset_paths"]["preview_side"])
        self.assertTrue(preview_path.exists())
        preview_markup = preview_path.read_text(encoding="utf-8")
        self.assertIn("MIRA Stylist Preview", preview_markup)
        self.assertIn("stylist_commentary", reloaded_job["result"])
