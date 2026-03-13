from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import time
from urllib.parse import parse_qs, urlparse

from PIL import Image

from mira_stylist.config import get_settings
from mira_stylist.models import (
    AvatarAssetManifest,
    AvatarStatus,
    BodyMeasurements,
    GarmentAssetManifest,
    GarmentCategory,
    GarmentItem,
    GarmentProcessingStatus,
    ProductSource,
    RemoteTryOnAccepted,
    RemoteTryOnStatus,
    SourceType,
    TryOnRequest,
    UserAvatar,
    VTONInputPayload,
    VTONRunResult,
)
from mira_stylist.services.artifact_manifest_service import ArtifactManifestService
from mira_stylist.services.artifact_url_service import ArtifactURLService
from mira_stylist.services.object_store_service import ObjectStoreService
from mira_stylist.services.storage_service import AssetStorageService
from mira_stylist.services.stylist_job_service import StylistJobService
from mira_stylist.services.tryon_pipeline_orchestrator import TryOnPipelineOrchestrator
from mira_stylist.services.tryon_service import TryOnPreviewService
from mira_stylist.vton.providers.remote_gpu import RemoteGPUVTONProvider


class PipelineFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mira_stylist_foundation_"))
        self.settings = replace(get_settings(), storage_root=self.temp_dir)
        self.storage = AssetStorageService(settings=self.settings)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_artifact_manifest_service_persists_manifest(self) -> None:
        avatar_image = self.temp_dir / "avatar.jpg"
        garment_image = self.temp_dir / "garment.jpg"
        pose_json = self.temp_dir / "pose.json"
        human_json = self.temp_dir / "human.json"
        human_mask = self.temp_dir / "human.png"
        garment_mask = self.temp_dir / "garment_mask.png"
        garment_seg_meta = self.temp_dir / "garment_segmentation.json"
        garment_alpha = self.temp_dir / "garment_alpha.png"

        Image.new("RGB", (120, 180), (255, 255, 255)).save(avatar_image)
        Image.new("RGB", (80, 120), (120, 80, 40)).save(garment_image)
        Image.new("L", (120, 180), 255).save(human_mask)
        Image.new("L", (80, 120), 255).save(garment_mask)
        Image.new("RGBA", (80, 120), (120, 80, 40, 255)).save(garment_alpha)
        pose_json.write_text("{}", encoding="utf-8")
        human_json.write_text("{}", encoding="utf-8")
        garment_seg_meta.write_text("{}", encoding="utf-8")

        avatar = UserAvatar(
            user_id="user_1",
            avatar_id="avatar_1",
            status=AvatarStatus.READY,
            source_type=SourceType.IMAGE_ESTIMATED,
            measurements=BodyMeasurements(height_cm=180),
            assets=AvatarAssetManifest(front_capture_path=str(avatar_image)),
        )
        garment = GarmentItem(
            garment_id="garment_1",
            raw_input_id="input_1",
            source=ProductSource(),
            title="Brown Shirt",
            category=GarmentCategory.TOP,
            primary_image_path=str(garment_image),
            assets=GarmentAssetManifest(primary_image_path=str(garment_image)),
            extraction_status=GarmentProcessingStatus.READY,
        )
        payload = VTONInputPayload(
            request_id="req_1",
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            pose="neutral",
            camera_angle="front",
            avatar_image_path=str(avatar_image),
            garment_image_path=str(garment_image),
            person_segmentation_path=str(human_mask),
            person_segmentation_metadata_path=str(human_json),
            pose_metadata_path=str(pose_json),
            garment_mask_path=str(garment_mask),
            garment_category="top",
            output_dir=str(self.temp_dir / "out"),
            notes=["Human mask prepared with backend=apple_vision_agnostic_mask and mask_type=upper."],
        )

        service = ArtifactManifestService(storage=self.storage)
        paths = self.storage.ensure_tryon_paths("tryon_1")
        manifest = service.create_manifest(
            job_id="tryon_1",
            request=TryOnRequest(avatar_id=avatar.avatar_id, garment_id=garment.garment_id),
            avatar=avatar,
            garment=garment,
            payload=payload,
            storage_paths=paths,
        )

        manifest_path = paths.metadata_dir / "artifact_manifest.json"
        self.assertTrue(manifest_path.exists())
        self.assertEqual(manifest.human_parsing.mask_type, "upper")
        self.assertEqual(manifest.garment_image.path, str(garment_image))

    def test_artifact_url_service_builds_and_verifies_signed_url(self) -> None:
        artifact = self.temp_dir / "avatars" / "u" / "a" / "captures" / "photo.jpg"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"img")
        settings = replace(
            self.settings,
            artifact_base_url="https://stylist.example/artifacts",
            artifact_signing_secret="secret",
            artifact_url_ttl_seconds=300,
        )
        service = ArtifactURLService(settings=settings)
        expires = int(time.time()) + 123
        url = service.build_signed_url(artifact, expires=expires)
        self.assertIn("https://stylist.example/artifacts/", url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        relative = service._relative_storage_path(artifact)
        sig = query["sig"][0]
        self.assertTrue(service.verify(relative, expires=int(query["expires"][0]), signature=sig))

    def test_object_store_service_external_base_mode(self) -> None:
        artifact = self.temp_dir / "tryon" / "job1" / "metadata" / "artifact_manifest.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}", encoding="utf-8")
        settings = replace(
            self.settings,
            object_store_mode="external_base",
            object_store_base_url="https://cdn.example/mira",
        )
        store = ObjectStoreService(settings=settings, artifact_urls=ArtifactURLService(settings=settings))
        url = store.publish_artifact(artifact)
        self.assertEqual(url, "https://cdn.example/mira/tryon/job1/metadata/artifact_manifest.json")

    def test_orchestrator_persists_stylist_job(self) -> None:
        avatar_image = self.temp_dir / "avatar.jpg"
        garment_image = self.temp_dir / "garment.jpg"
        person_mask = self.temp_dir / "person.png"
        garment_mask = self.temp_dir / "garment_mask.png"
        pose_json = self.temp_dir / "pose.json"

        Image.new("RGB", (120, 180), (255, 255, 255)).save(avatar_image)
        Image.new("RGB", (90, 120), (140, 90, 50)).save(garment_image)
        Image.new("L", (120, 180), 255).save(person_mask)
        Image.new("L", (90, 120), 255).save(garment_mask)
        pose_json.write_text("{}", encoding="utf-8")

        avatar = UserAvatar(
            user_id="user_1",
            avatar_id="avatar_1",
            status=AvatarStatus.READY,
            source_type=SourceType.IMAGE_ESTIMATED,
            assets=AvatarAssetManifest(front_capture_path=str(avatar_image)),
        )
        garment = GarmentItem(
            garment_id="garment_1",
            raw_input_id="input_1",
            source=ProductSource(),
            title="Brown Shirt",
            category=GarmentCategory.TOP,
            primary_image_path=str(garment_image),
            assets=GarmentAssetManifest(primary_image_path=str(garment_image)),
            extraction_status=GarmentProcessingStatus.READY,
        )
        payload = VTONInputPayload(
            request_id="tryon_1",
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            pose="neutral",
            camera_angle="front",
            avatar_image_path=str(avatar_image),
            garment_image_path=str(garment_image),
            person_segmentation_path=str(person_mask),
            pose_metadata_path=str(pose_json),
            garment_mask_path=str(garment_mask),
            garment_category="top",
            output_dir=str(self.temp_dir / "out"),
            notes=[],
        )

        class StubVTON:
            def build_payload(self, **kwargs):
                return payload

            def generate_preview(self, **kwargs):
                preview_path = Path(kwargs["storage_paths"].previews_dir) / "remote.png"
                preview_path.write_bytes(b"png")
                return VTONRunResult(
                    status="ok",
                    backend="remote_gpu_api",
                    generated_preview_path=str(preview_path),
                )

        paths = self.storage.ensure_tryon_paths("tryon_1")
        jobs = StylistJobService(storage=self.storage)
        manifests = ArtifactManifestService(storage=self.storage)
        class StubPreprocessing:
            def build_vton_payload(self, **kwargs):
                return payload
        orchestrator = TryOnPipelineOrchestrator(
            jobs=jobs,
            manifests=manifests,
            preprocessing=StubPreprocessing(),
            vton=StubVTON(),
        )

        run = orchestrator.run_sync(
            preview_job_id="tryon_1",
            request=TryOnRequest(avatar_id=avatar.avatar_id, garment_id=garment.garment_id),
            avatar=avatar,
            garment=garment,
            storage_paths=paths,
        )

        self.assertEqual(run.stylist_job.status.value, "completed")
        self.assertTrue((paths.metadata_dir / "artifact_manifest.json").exists())
        self.assertTrue((paths.jobs_dir / f"{run.stylist_job.stylist_job_id}.json").exists())

    def test_async_submission_and_callback_update_preview_job(self) -> None:
        avatar_image = self.temp_dir / "avatar.jpg"
        garment_image = self.temp_dir / "garment.jpg"
        person_mask = self.temp_dir / "person.png"
        garment_mask = self.temp_dir / "garment_mask.png"
        pose_json = self.temp_dir / "pose.json"

        Image.new("RGB", (120, 180), (255, 255, 255)).save(avatar_image)
        Image.new("RGB", (90, 120), (140, 90, 50)).save(garment_image)
        Image.new("L", (120, 180), 255).save(person_mask)
        Image.new("L", (90, 120), 255).save(garment_mask)
        pose_json.write_text("{}", encoding="utf-8")

        avatar = UserAvatar(
            user_id="user_1",
            avatar_id="avatar_1",
            status=AvatarStatus.READY,
            source_type=SourceType.IMAGE_ESTIMATED,
            assets=AvatarAssetManifest(front_capture_path=str(avatar_image)),
        )
        garment = GarmentItem(
            garment_id="garment_1",
            raw_input_id="input_1",
            source=ProductSource(),
            title="Brown Shirt",
            category=GarmentCategory.TOP,
            primary_image_path=str(garment_image),
            assets=GarmentAssetManifest(primary_image_path=str(garment_image)),
            extraction_status=GarmentProcessingStatus.READY,
        )
        payload = VTONInputPayload(
            request_id="tryon_1",
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            pose="neutral",
            camera_angle="front",
            avatar_image_path=str(avatar_image),
            garment_image_path=str(garment_image),
            person_segmentation_path=str(person_mask),
            pose_metadata_path=str(pose_json),
            garment_mask_path=str(garment_mask),
            garment_category="top",
            output_dir=str(self.temp_dir / "out"),
            notes=[],
        )

        class StubVTON:
            def build_payload(self, **kwargs):
                return payload

            def generate_preview(self, **kwargs):
                return None

        class StubRemoteProvider:
            provider_name = "remote_gpu_api"

            def build_remote_request(self, **kwargs):
                from mira_stylist.models import RemoteTryOnRequest

                job = kwargs["stylist_job"]
                return RemoteTryOnRequest(
                    stylist_job_id=job.stylist_job_id,
                    preview_job_id=job.preview_job_id,
                    render_mode="styled_overlay",
                    camera_angle="front",
                )

            def submit(self, **kwargs):
                return RemoteTryOnAccepted(
                    status="accepted",
                    backend="remote_gpu_api",
                    provider_job_id="gpu_job_1",
                )

            def poll(self, **kwargs):
                return RemoteTryOnStatus(
                    status="completed",
                    backend="remote_gpu_api",
                    provider_job_id="gpu_job_1",
                    result_image_url="https://gpu/result.png",
                    notes=["remote completion"],
                )

        paths = self.storage.ensure_tryon_paths("tryon_async")
        jobs = StylistJobService(storage=self.storage)
        manifests = ArtifactManifestService(storage=self.storage)
        class StubPreprocessing:
            def build_vton_payload(self, **kwargs):
                return payload
        orchestrator = TryOnPipelineOrchestrator(
            jobs=jobs,
            manifests=manifests,
            preprocessing=StubPreprocessing(),
            vton=StubVTON(),
            remote_provider=StubRemoteProvider(),
        )
        service = TryOnPreviewService(
            storage=self.storage,
            vton=StubVTON(),
            jobs=jobs,
            manifests=manifests,
            orchestrator=orchestrator,
        )

        from mira_stylist.models.api import AsyncTryOnPreviewRequest

        stylist_job = service.create_preview_job_async(
            AsyncTryOnPreviewRequest(avatar_id=avatar.avatar_id, garment_id=garment.garment_id),
            avatar=avatar,
            garment=garment,
        )
        self.assertEqual(stylist_job.status.value, "gpu_queued")
        updated = service.poll_pipeline_job(stylist_job.stylist_job_id)
        self.assertIsNotNone(updated)
        preview_job = service.get_job(stylist_job.preview_job_id)
        self.assertEqual(preview_job.preview_status.value, "completed")
        self.assertEqual(preview_job.result.output_asset_paths["preview_front"], "https://gpu/result.png")

    def test_remote_provider_builds_versioned_request_with_public_artifacts(self) -> None:
        artifact = self.temp_dir / "tryon" / "job1" / "preprocessing" / "user.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), (255, 255, 255)).save(artifact)

        settings = replace(
            self.settings,
            object_store_mode="external_base",
            object_store_base_url="https://cdn.example/mira",
        )
        object_store = ObjectStoreService(settings=settings, artifact_urls=ArtifactURLService(settings=settings))
        provider = RemoteGPUVTONProvider(object_store=object_store)
        stylist_job = StylistJobService(storage=self.storage).create_job(
            preview_job_id="tryon_1",
            avatar_id="avatar_1",
            garment_id="garment_1",
            storage_paths=self.storage.ensure_tryon_paths("tryon_1"),
        )
        from mira_stylist.models import ImageArtifact, TryOnArtifactManifest

        manifest = TryOnArtifactManifest(
            manifest_id="manifest_1",
            job_id="tryon_1",
            avatar_id="avatar_1",
            garment_id="garment_1",
            user_image=ImageArtifact(role="user_image", path=str(artifact), mime_type="image/png"),
            garment_image=ImageArtifact(role="garment_image", path=str(artifact), mime_type="image/png"),
        )
        request = provider.build_remote_request(
            stylist_job=stylist_job,
            artifact_manifest=manifest,
            payload=None,
        )
        self.assertEqual(request.schema_version, "runpod.v1")
        self.assertEqual(request.provider_version, "v1")
        self.assertEqual(request.artifacts[0].public_url, "https://cdn.example/mira/tryon/job1/preprocessing/user.png")


if __name__ == "__main__":
    unittest.main()
