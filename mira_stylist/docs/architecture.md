# MIRA Stylist Architecture

## Objective

MIRA Stylist is the foundation for "see yourself before you buy." It adds a new feature package that can eventually support:

- mobile LiDAR or depth-guided body scanning
- quick one-photo try-on avatar generation
- persistent user avatar assets and measurements
- image-first garment ingestion from uploads, screenshots, pasted images, and image URLs
- optional product-page metadata enrichment
- virtual try-on preview generation
- future AR-facing phone experiences

## High-Level Architecture

### 1. Scan Ingestion

Mobile clients capture:

- RGB frames
- depth or LiDAR frames when available
- device metadata
- optional calibration and pose snapshots

The backend stores this as a `ScanSession` first. This is intentionally separate from avatar creation so scan quality can be validated before reconstruction.

### 2. Avatar Domain

`UserAvatar` represents a stable user body representation. The scaffold assumes a future system may support:

- parametric body models
- mesh-based avatars
- hybrid mesh plus measurement profiles

The current scaffold stores metadata plus placeholder asset paths, and reloads those records from disk so the MVP survives service restarts.

The current hybrid avatar entry points are:

- `Quick Try-On`: one photo, lowest confidence, fastest setup
- `Guided Photo Capture`: front + side photos, moderate confidence
- `iPhone Scan (Beta)`: scan-session metadata + capture bundle registration for depth/LiDAR devices

The current MVP also supports a first personal-avatar path from front and side photos. This is still heuristic and measurement-driven, but it establishes the capture/storage/API boundary needed before deeper CV or LiDAR work.

### 3. Garment Ingestion

Garment ingestion now starts from a normalized `GarmentInput`.

Primary ingestion types:

- uploaded image
- pasted image
- screenshot
- image URL

Secondary ingestion type:

- product-page URL

The current implementation is intentionally conservative:

- stores the raw input
- normalizes it into source image references
- creates candidate garment images
- supports later manual candidate selection
- only treats product-page parsing as best-effort metadata enrichment

### 4. Try-On Preview

The try-on layer should eventually combine:

- avatar body geometry
- pose normalization
- garment category rules
- garment assets
- rendering mode selection

The current MVP produces deterministic, category-aware SVG preview artifacts and a `PreviewRenderJob` / `TryOnResult` pair so mobile and web clients can integrate against stable contracts without pretending realistic cloth simulation is solved.

Those preview artifacts now also consume a lightweight `body_profile` derived from stored avatar measurements plus photo-capture framing heuristics.

When avatar capture assets are available, the renderer now prefers a `photo-grounded` mode:

- the uploaded self photo is used as the visual base
- garment overlays are anchored to heuristic body zones on that image
- the system falls back to the synthetic silhouette renderer when no usable capture asset exists

This is still not person segmentation or true neural try-on, but it moves the UX closer to real commerce try-on tools.

Each preview result now also carries a structured `stylist_commentary` payload. This is the first step toward MIRA feeling like a stylist instead of a renderer:

- summary
- what works
- watch-outs
- fit caveats
- confidence label and score
- uncertainty-aware notes

On top of that, the MVP now supports a `single-look feedback` layer. Users can ask a follow-up question about one stored look, such as:

- Is this flattering?
- Does this work for dinner?
- Is this too formal?
- Should I buy this?

The backend answers from the stored preview job plus avatar and garment metadata rather than pretending to run an unrestricted conversational model.

The MVP now also supports a `look comparison` layer. Two stored preview jobs can be compared for a specific occasion or style goal, and the system returns:

- winner
- verdict
- decision factors
- strengths of each look
- cautions
- confidence label and score

The next stylist layer now in place is `pairing suggestions`. Given an avatar plus a garment, MIRA can return:

- a short outfit-direction summary
- a lightweight outfit formula
- role-based pairing suggestions such as bottoms, shoes, layers, or accessories
- occasion/style-goal weighting
- explicit notes that the result is generic styling guidance until wardrobe memory exists

On top of that, the MVP now supports `outfit generation`. This takes one anchor garment plus an occasion/style goal and produces:

- a persisted `GeneratedOutfit`
- ordered outfit components with layering metadata
- a composed front/side preview artifact
- one real anchor garment plus generated companion pieces
- a stable API contract for later wardrobe-aware substitution

### 5. API Layer

The FastAPI app is intentionally standalone. It exposes:

- `GET /health`
- `POST /avatars/scan-session`
- `POST /avatars`
- `POST /avatars/quick-tryon`
- `POST /avatars/photo-profile`
- `POST /avatars/scan-beta/session`
- `POST /avatars/scan-beta/session/{scan_session_id}/capture-bundle`
- `POST /avatars/scan-beta/build`
- `GET /avatars/{avatar_id}`
- `POST /garments/ingest/image-upload`
- `POST /garments/ingest/image-url`
- `POST /garments/ingest/pasted-image`
- `POST /garments/ingest/screenshot`
- `POST /garments/ingest/product-page-url`
- `POST /garments/ingest/select-candidate`
- `GET /garments/{garment_id}`
- `POST /tryon/preview`
- `POST /tryon/feedback`
- `POST /tryon/compare`
- `POST /tryon/pairing`
- `GET /tryon/jobs/{job_id}`
- `POST /outfits/generate`
- `GET /outfits/{outfit_id}`

## System Boundaries

Handled now:

- typed contracts
- storage layout
- service boundaries
- deterministic MVP artifact generation
- disk-backed record reload for service recovery
- front/side photo capture persistence
- single-photo quick-avatar path
- lightweight body-profile derivation from photo metadata and measurement hints
- scan-session bundle persistence for future depth/LiDAR ingestion
- preview commentary generation
- single-look follow-up feedback generation
- look-vs-look comparison generation
- multi-garment outfit composition around one anchor garment
- docs and integration guidance
- explicit image-first ingestion and candidate-selection flow

Deferred:

- heavy CV/ML inference
- multi-view reconstruction
- cloth simulation
- neural try-on
- mobile AR rendering
- retailer-specific scraping adapters

## Production Path

1. Add object storage for uploads and generated assets.
2. Add async jobs for avatar build and preview generation.
3. Replace photo-framing heuristics with landmark and silhouette extraction.
4. Add retailer adapters and segmentation workers.
5. Add ARKit/RealityKit preview consumer on iPhone.
