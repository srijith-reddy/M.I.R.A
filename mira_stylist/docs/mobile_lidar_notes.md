# Mobile LiDAR Notes

## Goal

Allow an iPhone or depth-aware phone to provide enough structured capture data for avatar creation and later try-on, while also supporting simple image sharing for garment ingestion.

## Image-First Mobile Flow

For MVP, the mobile app can be useful before any LiDAR body scanning is perfect.

Suggested garment flow:

1. user shares or uploads an image into MIRA Stylist
2. app chooses the correct endpoint:
   - uploaded image
   - pasted image
   - screenshot
   - image URL
3. backend returns candidate images and asks for confirmation if needed
4. app submits candidate selection
5. app requests preview generation for the saved avatar

## Interim MVP Before LiDAR

Until a real depth capture path is implemented, mobile clients can still create a personal avatar profile by sending:

- a front full-body image
- a side full-body image
- optional self-reported height or other measurements

That flow now lands at `POST /avatars/photo-profile` and is intended as the first practical body-profile capture step before LiDAR and landmark estimation are added.

## Hybrid Avatar Modes

The current backend now supports three avatar entry points:

- `POST /avatars/quick-tryon`
  For one-photo, low-friction previews.
- `POST /avatars/photo-profile`
  For front + side guided capture.
- `POST /avatars/scan-beta/session` + `POST /avatars/scan-beta/session/{scan_session_id}/capture-bundle` + `POST /avatars/scan-beta/build`
  For iPhone depth/LiDAR capture scaffolding.

## Suggested On-Device Responsibilities

- drive `ARSession` or `ARKit` body/depth capture
- collect device metadata and calibration state
- capture preview photos for UX confirmation
- handle share-sheet image ingestion
- guide the user through scan coverage
- upload frames or fused outputs to the backend

## Suggested Backend Responsibilities

- store scan sessions and uploaded frame references
- validate scan completeness and depth quality
- reconstruct or estimate a stable body representation
- persist avatar assets and measurements
- generate preview outputs for try-on

## Practical Capture Flow

1. User starts a scan session from the phone.
2. Client calls `POST /avatars/scan-session`.
3. Client captures:
   - RGB frames
   - depth maps
   - LiDAR data if supported
   - device pose metadata
4. Client uploads references or bundles to backend/object storage.
5. Backend creates avatar artifacts asynchronously in a future revision.

Current beta implementation:

1. client creates a scan-beta session
2. client uploads a metadata bundle with RGB/depth counts, coverage hint, and optional preview image
3. backend derives a lightweight body profile from coverage heuristics
4. backend returns a `UserAvatar` without claiming fused geometry reconstruction

## Important Constraints

- LiDAR quality differs across devices.
- Front-facing versus rear-facing capture has different tradeoffs.
- Apparel fit preview is sensitive to pose, posture, and loose clothing during scan.
- A stable avatar may require combining direct measurements with reconstruction outputs.

## Future ARKit Integration Points

- `ARBodyTrackingConfiguration` for landmark hints
- `ARWorldTrackingConfiguration` plus scene depth on supported devices
- RealityKit preview surface for on-phone try-on playback
- body anchor alignment for future live AR overlay mode
