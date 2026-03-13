import ARKit
import Foundation

// MIRA Stylist iOS capture scaffold.
// This is intentionally a thin example, not a full app.

final class MiraStylistCaptureController: NSObject, ARSessionDelegate {
    private let session = ARSession()
    private let backendBaseURL = URL(string: "http://localhost:8000")!
    private var scanSessionId: String?
    private var capturedRGBFrames = 0
    private var capturedDepthFrames = 0

    func startCapture(userId: String) {
        let supportsSceneDepth = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)

        var configuration = ARWorldTrackingConfiguration()
        if supportsSceneDepth {
            configuration.frameSemantics.insert(.sceneDepth)
        }

        session.delegate = self
        session.run(configuration)

        createBackendScanSession(userId: userId, hasLidar: supportsSceneDepth)
    }

    private func createBackendScanSession(userId: String, hasLidar: Bool) {
        let payload: [String: Any] = [
            "user_id": userId,
            "source_type": hasLidar ? "lidar" : "depth",
            "capture_device_model": "iPhone",
            "has_lidar": hasLidar,
            "expected_frame_count": 140,
            "expected_depth_frame_count": 90,
            "image_resolution": "1920x1440"
        ]

        // TODO:
        // - POST payload to /avatars/scan-beta/session
        // - persist returned scan_session_id
        // - upload frame bundles or fused assets against that session with
        //   POST /avatars/scan-beta/session/{scan_session_id}/capture-bundle
        print("Create scan session payload: \(payload)")
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        capturedRGBFrames += 1
        guard let sceneDepth = frame.sceneDepth else {
            return
        }
        capturedDepthFrames += 1

        // TODO:
        // - sample RGB image buffer
        // - sample depth map
        // - capture camera transform and intrinsics
        // - batch uploads or write to local temporary bundle
        // - when capture is complete, register bundle metadata like:
        //   ["rgb_frame_count": capturedRGBFrames,
        //    "depth_frame_count": capturedDepthFrames,
        //    "coverage_hint": 0.75,
        //    "upload_mode": "arkit_frame_bundle"]
        // - finally call POST /avatars/scan-beta/build
        let depthWidth = CVPixelBufferGetWidth(sceneDepth.depthMap)
        let depthHeight = CVPixelBufferGetHeight(sceneDepth.depthMap)
        print("Depth frame: \(depthWidth)x\(depthHeight)")
    }
}
