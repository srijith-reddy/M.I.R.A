# MIRA Stylist CatVTON Audit

## Subsystem Status

### Pose Estimation
- Status: partial
- Files:
  - [apple_vision_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/apple_vision_service.py)
  - [pose_estimation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/pose_estimation.py)
  - [apple_vision_body_pose.swift](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/native/apple_vision_body_pose.swift)
- Assessment:
  - Apple Vision body pose is usable for lightweight anchoring on macOS.
  - It is not dense pose and is weaker than DWpose/OpenPose for learned try-on alignment.
  - The new engine keeps Apple Vision as the default local backend and adds pluggable `dwpose` / `openpose` runner hooks.

### Garment Segmentation
- Status: partial
- Files:
  - [garment_ingestion.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/pipelines/garment_ingestion.py)
  - [garment_segmentation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/garment_segmentation.py)
- Assessment:
  - Existing ingestion handled asset persistence and candidate registration but not real segmentation.
  - The new segmentation module adds a clean interface for `GroundingDINO + SAM` and a working `simple_alpha` fallback for local testing.

### Garment Ingestion
- Status: solved for MVP, partial for production
- Files:
  - [garment_input_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/garment_input_service.py)
  - [ingestion_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/ingestion_service.py)
  - [garment_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/garment_service.py)
- Assessment:
  - Image-first ingestion, candidate selection, and canonical garment creation are already in place.
  - Product understanding still depends on heuristics and optional user overrides.

### Body Model / Avatar
- Status: partial
- Files:
  - [avatar_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/avatar_service.py)
  - [avatar_building.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/pipelines/avatar_building.py)
  - [human_segmentation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/human_segmentation.py)
- Assessment:
  - Avatar metadata, body profile heuristics, photo-grounded capture persistence, and Apple Vision segmentation exist.
  - This is not yet a true 3D body reconstruction path.

### Fit Realism
- Status: missing
- Files:
  - [preview_generation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/pipelines/preview_generation.py)
  - [vton_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/vton_service.py)
- Assessment:
  - Existing fit notes are heuristic.
  - Real fit realism depends on the learned try-on backend and better size/garment metadata than the repository currently has.

### Multi-View Consistency
- Status: missing
- Files:
  - [preview_generation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/pipelines/preview_generation.py)
  - [tryon_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/tryon_service.py)
- Assessment:
  - Front and side artifacts are generated, but they are not jointly constrained by one learned multi-view representation.

### Virtual Try-On Model
- Status: partial
- Files:
  - [vton_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/vton_service.py)
  - [catvton_engine.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vton/catvton_engine.py)
  - [catvton_local_runner.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/tools/catvton_local_runner.py)
  - [remote_vton_client.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vton/remote_vton_client.py)
- Assessment:
  - CatVTON is now the default learned-engine path for `top`, `outerwear`, and `dress`.
  - The service preserves fallback behavior through the older explicit-runner / IDM / diffusers paths.
  - Real output quality still depends on CatVTON runtime availability and clean human/garment masks.

## Target Architecture

```text
user photo
  -> pose estimation
  -> human segmentation
garment image
  -> garment segmentation
pose + masks + images
  -> CatVTON inference
  -> try-on image
```

### Implemented Mapping
- User photo -> [pose_estimation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/pose_estimation.py)
- User photo -> [human_segmentation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/human_segmentation.py)
- Garment image -> [garment_segmentation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/garment_segmentation.py)
- Images + masks + pose -> [catvton_engine.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vton/catvton_engine.py)
- Fallback / orchestration -> [vton_service.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/services/vton_service.py)

## Local Mac Run Notes

### Local MPS
```bash
export MIRA_STYLIST_CATVTON_MODE=local_mps
export MIRA_STYLIST_CATVTON_REPO_PATH=/absolute/path/to/third_party/CatVTON
export MIRA_STYLIST_CATVTON_PYTHON_BIN=$(which python)
python scripts/test_vton_pipeline.py --user test_user.jpg --garment shirt.png
```

### Local CPU
```bash
export MIRA_STYLIST_CATVTON_MODE=local_cpu
export MIRA_STYLIST_CATVTON_REPO_PATH=/absolute/path/to/third_party/CatVTON
python scripts/test_vton_pipeline.py --user test_user.jpg --garment shirt.png
```

## Remote GPU Run Notes

```bash
export MIRA_STYLIST_CATVTON_MODE=remote_gpu_api
export MIRA_STYLIST_REMOTE_VTON_URL=http://gpu-host:9000
python scripts/test_vton_pipeline.py --user test_user.jpg --garment shirt.png
```

The remote endpoint is expected to accept:

```json
{
  "user_image": "<base64>",
  "garment_image": "<base64>",
  "pose": { "...": "..." },
  "human_mask": "<base64>",
  "garment_mask": "<base64>",
  "garment_category": "top"
}
```
