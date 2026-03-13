import AppKit
import Foundation
import Vision

struct PointPayload: Codable {
    let x: Double
    let y: Double
    let confidence: Double
}

struct AnalysisPayload: Codable {
    let status: String
    let provider: String
    let image_width: Int?
    let image_height: Int?
    let points: [String: PointPayload]
    let notes: [String]
}

func emit(_ payload: AnalysisPayload) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    let data = try! encoder.encode(payload)
    FileHandle.standardOutput.write(data)
}

func loadCGImage(at path: String) -> (CGImage, Int, Int)? {
    let url = URL(fileURLWithPath: path)
    guard let image = NSImage(contentsOf: url) else {
        return nil
    }
    var rect = CGRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        return nil
    }
    return (cgImage, cgImage.width, cgImage.height)
}

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    emit(
        AnalysisPayload(
            status: "invalid_input",
            provider: "apple_vision_body_pose",
            image_width: nil,
            image_height: nil,
            points: [:],
            notes: ["Expected an image path argument."]
        )
    )
    exit(1)
}

let imagePath = arguments[1]
guard let (cgImage, width, height) = loadCGImage(at: imagePath) else {
    emit(
        AnalysisPayload(
            status: "unreadable_image",
            provider: "apple_vision_body_pose",
            image_width: nil,
            image_height: nil,
            points: [:],
            notes: ["The input image could not be decoded into a CGImage."]
        )
    )
    exit(0)
}

let request = VNDetectHumanBodyPoseRequest()
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

do {
    try handler.perform([request])
} catch {
    emit(
        AnalysisPayload(
            status: "vision_error",
            provider: "apple_vision_body_pose",
            image_width: width,
            image_height: height,
            points: [:],
            notes: ["Vision body-pose request failed: \(error.localizedDescription)"]
        )
    )
    exit(0)
}

guard let observation = request.results?.first else {
    emit(
        AnalysisPayload(
            status: "no_pose",
            provider: "apple_vision_body_pose",
            image_width: width,
            image_height: height,
            points: [:],
            notes: ["No human body pose was detected in the image."]
        )
    )
    exit(0)
}

let jointNames: [VNHumanBodyPoseObservation.JointName] = [
    .nose,
    .neck,
    .root,
    .leftShoulder,
    .rightShoulder,
    .leftElbow,
    .rightElbow,
    .leftWrist,
    .rightWrist,
    .leftHip,
    .rightHip,
    .leftKnee,
    .rightKnee,
    .leftAnkle,
    .rightAnkle,
]

var points: [String: PointPayload] = [:]
var notes: [String] = []

func jointKey(_ joint: VNHumanBodyPoseObservation.JointName) -> String {
    switch joint {
    case .nose: return "nose"
    case .neck: return "neck"
    case .root: return "root"
    case .leftShoulder: return "leftShoulder"
    case .rightShoulder: return "rightShoulder"
    case .leftElbow: return "leftElbow"
    case .rightElbow: return "rightElbow"
    case .leftWrist: return "leftWrist"
    case .rightWrist: return "rightWrist"
    case .leftHip: return "leftHip"
    case .rightHip: return "rightHip"
    case .leftKnee: return "leftKnee"
    case .rightKnee: return "rightKnee"
    case .leftAnkle: return "leftAnkle"
    case .rightAnkle: return "rightAnkle"
    default: return String(describing: joint)
    }
}

for joint in jointNames {
    if let point = try? observation.recognizedPoint(joint), point.confidence >= 0.1 {
        points[jointKey(joint)] = PointPayload(
            x: Double(point.location.x),
            y: Double(point.location.y),
            confidence: Double(point.confidence)
        )
    }
}

if points["leftShoulder"] == nil || points["rightShoulder"] == nil {
    notes.append("Shoulder keypoints were not both detected confidently.")
}
if points["leftHip"] == nil || points["rightHip"] == nil {
    notes.append("Hip keypoints were not both detected confidently.")
}

emit(
    AnalysisPayload(
        status: points.isEmpty ? "low_signal" : "ok",
        provider: "apple_vision_body_pose",
        image_width: width,
        image_height: height,
        points: points,
        notes: notes
    )
)
