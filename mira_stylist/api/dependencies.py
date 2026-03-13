from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from mira_stylist.config import StylistSettings, get_settings
from mira_stylist.services import (
    AssetStorageService,
    ArtifactManifestService,
    ArtifactURLService,
    AvatarService,
    CandidateSelectionService,
    GarmentInputService,
    GarmentIngestionService,
    GarmentService,
    ObjectStoreService,
    OutfitGenerationService,
    PreprocessingService,
    RemoteJobReconciler,
    ScanSessionService,
    StylistJobService,
    TryOnPipelineOrchestrator,
    TryOnPreviewService,
    VTONService,
)
from mira_stylist.vton.providers import RemoteGPUVTONProvider


@dataclass
class StylistServiceContainer:
    settings: StylistSettings
    storage: AssetStorageService
    artifact_urls: ArtifactURLService
    object_store: ObjectStoreService
    scan_sessions: ScanSessionService
    avatars: AvatarService
    garment_inputs: GarmentInputService
    garment_ingestion: GarmentIngestionService
    garments: GarmentService
    candidate_selection: CandidateSelectionService
    stylist_jobs: StylistJobService
    artifact_manifests: ArtifactManifestService
    preprocessing: PreprocessingService
    orchestrator: TryOnPipelineOrchestrator
    reconciler: RemoteJobReconciler
    tryon: TryOnPreviewService
    outfits: OutfitGenerationService


@lru_cache(maxsize=1)
def get_services() -> StylistServiceContainer:
    settings = get_settings()
    storage = AssetStorageService(settings=settings)
    artifact_urls = ArtifactURLService(settings=settings)
    object_store = ObjectStoreService(settings=settings, artifact_urls=artifact_urls)
    scan_sessions = ScanSessionService(storage=storage)
    avatars = AvatarService(storage=storage, scan_sessions=scan_sessions)
    garment_inputs = GarmentInputService(storage=storage)
    garment_ingestion = GarmentIngestionService(storage=storage, inputs=garment_inputs)
    garments = GarmentService(storage=storage)
    candidate_selection = CandidateSelectionService(
        ingestion=garment_ingestion,
        garments=garments,
    )
    preprocessing = PreprocessingService()
    vton = VTONService(settings=settings)
    remote_provider = RemoteGPUVTONProvider(object_store=object_store)
    stylist_jobs = StylistJobService(storage=storage)
    artifact_manifests = ArtifactManifestService(storage=storage)
    orchestrator = TryOnPipelineOrchestrator(
        jobs=stylist_jobs,
        manifests=artifact_manifests,
        preprocessing=preprocessing,
        vton=vton,
        remote_provider=remote_provider,
    )
    tryon = TryOnPreviewService(
        storage=storage,
        vton=vton,
        preprocessing=preprocessing,
        jobs=stylist_jobs,
        manifests=artifact_manifests,
        orchestrator=orchestrator,
    )
    reconciler = RemoteJobReconciler(jobs=stylist_jobs, tryon=tryon)
    reconciler.reconcile_pending_jobs()
    outfits = OutfitGenerationService(storage=storage)
    return StylistServiceContainer(
        settings=settings,
        storage=storage,
        artifact_urls=artifact_urls,
        object_store=object_store,
        scan_sessions=scan_sessions,
        avatars=avatars,
        garment_inputs=garment_inputs,
        garment_ingestion=garment_ingestion,
        garments=garments,
        candidate_selection=candidate_selection,
        stylist_jobs=stylist_jobs,
        artifact_manifests=artifact_manifests,
        preprocessing=preprocessing,
        orchestrator=orchestrator,
        reconciler=reconciler,
        tryon=tryon,
        outfits=outfits,
    )
