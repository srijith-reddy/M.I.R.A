# MIRA Stylist

MIRA Stylist is a self-contained scaffold for a depth-aware virtual styling system inside the broader MIRA ecosystem.

Current scope:

- avatar domain models for scanned users
- front/side photo-based avatar creation for a first personal-avatar MVP
- single-photo Quick Try-On avatar creation for the lowest-friction path
- iPhone Scan (Beta) session and capture-bundle contracts for LiDAR/depth-capable devices
- scan session tracking for LiDAR/depth/image-driven capture
- image-first garment ingestion contracts for uploads, pasted images, screenshots, and image URLs
- optional product-page URL enrichment as a secondary path
- persisted candidate-image registration and selection
- canonical garment creation from normalized inputs
- lightweight category-aware SVG preview job outputs for workflow validation
- photo-grounded SVG preview mode when the uploaded self image can be rendered in-browser
- structured stylist commentary attached to each preview result
- single-look stylist feedback for follow-up questions about one outfit
- look A vs look B comparison feedback grounded in stored preview jobs
- occasion-aware pairing suggestions for "what should I pair with this?"
- full-look outfit generation around one anchor garment for dinner/work/date/travel flows
- a standalone FastAPI surface for future integration
- mobile and browser-extension design notes

This package does not claim to solve:

- production body reconstruction
- cloth simulation
- universal retailer scraping
- photorealistic rendering
- full AR pose tracking on-device
- exact fit or drape realism from a single garment image

Instead, it establishes clean boundaries so those systems can be added without rewriting the existing codebase.

## Quick Start

Run the standalone API later with a small launcher such as:

```python
from mira_stylist.api import create_app

app = create_app()
```

Or with Uvicorn once FastAPI/Uvicorn are available:

```bash
uvicorn mira_stylist.api.app:app --reload
```

Or with the package entrypoint:

```bash
python -m mira_stylist --reload
```

## Smoke Tests

If `fastapi` and `testclient` are installed, run:

```bash
python -m unittest mira_stylist.tests.test_api_smoke
```

## Demo UI

Once the API is running, open:

```text
http://127.0.0.1:8000/demo
```

The demo walks through:

- hybrid avatar creation with Quick Try-On, Guided Photo Capture, and iPhone Scan (Beta)
- garment image upload
- candidate selection when needed
- preview job generation
- full outfit generation from the current anchor garment

The API also supports direct photo-based avatar creation with:

```text
POST /avatars/quick-tryon
POST /avatars/photo-profile
POST /avatars/scan-beta/session
POST /avatars/scan-beta/session/{scan_session_id}/capture-bundle
POST /avatars/scan-beta/build
```

Those endpoints support:

- one-photo quick avatars for fast preview
- guided front/side capture for better body profile quality
- scan-session plus bundle registration for iPhone LiDAR/depth capture scaffolding

The avatar endpoints return a stored `UserAvatar` with:

- persisted photo capture assets
- inferred or user-supplied body measurements
- a lightweight `body_profile`
- front and side avatar preview artifacts

Preview jobs now also return a `stylist_commentary` object with:

- summary
- what works
- watch-outs
- fit caveats
- confidence score and label
- uncertainty-aware notes

You can also ask about one specific look with:

```text
POST /tryon/feedback
```

That endpoint is grounded in the stored preview job and returns:

- answer
- supporting points
- cautions
- follow-up suggestions
- confidence label and score

And you can compare two looks with:

```text
POST /tryon/compare
```

That endpoint returns:

- winner job id
- verdict
- decision factors
- strengths for each look
- cautions
- confidence score and label

And you can ask MIRA how to build around one piece with:

```text
POST /tryon/pairing
```

That endpoint returns:

- a summary of the outfit direction
- an outfit formula
- occasion-aware pairing recommendations
- confidence score and label
- notes about current MVP limits

And you can generate a full composed look with:

```text
POST /outfits/generate
GET /outfits/{outfit_id}
```

That flow returns:

- an `Outfit` record with ordered components
- one real anchor garment plus generated companion pieces
- front and side composed-look preview artifacts
- an outfit formula, summary, and confidence

Generated records and artifacts are reloaded from disk-backed metadata on service startup, so avatar, garment, ingestion, and preview-job lookups survive process restarts.

## Learned VTON Backend

`mira_stylist` now supports a real learned synthesis backend for front-view preview generation.

Two modes are available:

- `MIRA_STYLIST_VTON_RUNNER`
  - explicit external runner command
  - best when you have a separate official VTON repository checkout or service runner
- built-in IDM-VTON adapter
  - enabled when `MIRA_STYLIST_IDM_VTON_REPO_PATH` or `MIRA_STYLIST_IDM_VTON_SERVER_URL` is set
  - uses [idm_vton_runner.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/tools/idm_vton_runner.py)
  - expects a local IDM-VTON checkout to be running its Gradio demo/API
- built-in local diffusers runner
  - enabled automatically when `MIRA_STYLIST_VTON_MODEL_PATH` is set
  - uses [vton_diffusers_runner.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/tools/vton_diffusers_runner.py)

Example local setup:

```bash
export MIRA_STYLIST_VTON_MODEL_PATH=/absolute/path/to/local/diffusers-inpaint-model
export MIRA_STYLIST_VTON_DEVICE=auto
export MIRA_STYLIST_VTON_STEPS=24
export MIRA_STYLIST_VTON_GUIDANCE_SCALE=6.5
export MIRA_STYLIST_VTON_STRENGTH=0.88
python -m mira_stylist --reload
```

Example IDM-VTON prototype setup:

```bash
export MIRA_STYLIST_IDM_VTON_REPO_PATH=/absolute/path/to/IDM-VTON
export MIRA_STYLIST_IDM_VTON_PYTHON_BIN=/absolute/path/to/idm-vton-env/bin/python
export MIRA_STYLIST_IDM_VTON_SERVER_URL=http://127.0.0.1:7860
python -m mira_stylist --reload
```

Current limits of the built-in learned backend:

- it is a learned diffusion refinement path, not a full retailer-grade proprietary VTON stack
- it works best for `top`, `outerwear`, and `dress`
- it still depends on the quality of the segmentation/pose preprocessing and the local checkpoint you provide
- no local model weights are bundled in this repository

For the IDM-VTON-specific local checkout layout and startup contract, see [learned_vton_backend.md](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/docs/learned_vton_backend.md).

## CatVTON Pipeline

`mira_stylist` now includes a CatVTON-first pipeline layer for local Mac testing and remote GPU inference.

- CatVTON engine: [catvton_engine.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vton/catvton_engine.py)
- Local runner: [catvton_local_runner.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/tools/catvton_local_runner.py)
- Pose selection: [pose_estimation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/pose_estimation.py)
- Human segmentation: [human_segmentation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/human_segmentation.py)
- Garment segmentation: [garment_segmentation.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/vision/garment_segmentation.py)
- End-to-end script: [test_vton_pipeline.py](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/scripts/test_vton_pipeline.py)

Supported CatVTON modes:

- `local_mps`
  - prefers Apple Silicon MPS for local testing
- `local_cpu`
  - slower but useful for compatibility checks
- `remote_gpu_api`
  - sends images, masks, and pose metadata to a remote GPU-backed `/tryon` service

Example local CatVTON setup:

```bash
export MIRA_STYLIST_CATVTON_MODE=local_mps
export MIRA_STYLIST_CATVTON_REPO_PATH=/absolute/path/to/third_party/CatVTON
python scripts/test_vton_pipeline.py --user test_user.jpg --garment shirt.png
```

For the audit report, architecture diagram, and detailed runbook, see [catvton_pipeline_audit.md](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/docs/catvton_pipeline_audit.md).

Stylist-specific env defaults are listed in [mira_stylist/.env.example](/Users/shrey24/Desktop/GenAI_projects/M.I.R.A/mira_stylist/.env.example). `mira_stylist/config.py` now loads `.env` directly when `python-dotenv` is available, so Stylist no longer depends on `mira/core/config.py` being imported first.

## Storage Layout

The module writes metadata and lightweight MVP artifacts under:

```text
output/mira_stylist/
  avatars/
    {user_id}/
      {avatar_id}/
        captures/
        mesh/
        textures/
        previews/
        metadata/
  scan_sessions/
    {user_id}/
      {scan_session_id}/
        uploads/
        metadata/
  garment_inputs/
    {input_id}/
      raw/
      normalized/
      metadata/
  garments/
    {garment_id}/
      raw/
      candidates/
      segmented/
      mesh/
      textures/
      metadata/
  tryon/
    {job_id}/
      previews/
      metadata/
  outfits/
    {outfit_id}/
      previews/
      metadata/
```

## Main Modules

- `models/`: typed domain and API contracts
- `services/`: service layer and persistence helpers
- `pipelines/`: input normalization, garment creation, avatar build, and preview boundaries
- `api/`: FastAPI routes and service container
- `docs/`: architecture and roadmap notes
- `mobile_examples/`: iPhone LiDAR capture example stub
- `extension/`: future browser extension scaffold

## Ingestion Philosophy

Primary path:

- uploaded image files
- pasted product images
- screenshots
- direct image URLs

Secondary path:

- product-page URLs for best-effort metadata enrichment

Everything is normalized through a shared `GarmentInput` contract before garment creation.

## Principles

- isolated from existing `mira/` modules
- backward compatible by default
- explicit TODO markers around difficult CV/AR work
- realistic about web parsing and garment reconstruction limits

## MVP Behavior Implemented

- binary image inputs are sanitized, hashed, and stored
- basic PNG/JPEG/GIF metadata is extracted without heavy dependencies
- preview rendering uses the uploaded self photo as the visual base when capture assets are available and browser-friendly
- candidate preview SVG artifacts are generated for review flows
- unambiguous single-candidate inputs are auto-finalized into canonical garments
- garment category and color are inferred heuristically from titles and filenames, with demo-side override support when inference is weak
- ingestion requests and results are persisted so candidate selection can resume after restart
- avatars, garments, scan sessions, and preview jobs are reloaded from stored metadata on startup
- front and side photo captures can create a lightweight personal body profile without adding heavy CV dependencies
- composed outfit generation can build a multi-piece look from one anchor garment plus occasion-aware generated companions
- Quick Try-On duplicates none of the scan complexity and is tuned for speed over certainty
- iPhone Scan (Beta) stores capture coverage metadata and optional preview assets without claiming fused reconstruction is implemented
- try-on preview jobs create deterministic category-aware SVG renders plus manifest files
- every try-on preview now includes heuristic MIRA commentary so the system can explain what it thinks, not just render an image
- single-look follow-up questions can now be answered from the stored preview result without rerunning the renderer
- two looks can now be compared directly using the stored commentary and preview jobs
