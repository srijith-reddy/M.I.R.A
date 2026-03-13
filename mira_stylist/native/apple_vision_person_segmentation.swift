import AppKit
import CoreImage
import CoreVideo
import Foundation
import Vision

struct SegmentationPayload: Codable {
    let status: String
    let provider: String
    let image_width: Int?
    let image_height: Int?
    let mask_path: String?
    let bbox_x: Double?
    let bbox_y: Double?
    let bbox_width: Double?
    let bbox_height: Double?
    let coverage_score: Double
    let notes: [String]
}

func emit(_ payload: SegmentationPayload) {
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

func saveMask(_ pixelBuffer: CVPixelBuffer, to outputPath: String) -> Bool {
    let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
    let context = CIContext(options: nil)
    guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else {
        return false
    }
    let rep = NSBitmapImageRep(cgImage: cgImage)
    guard let data = rep.representation(using: .png, properties: [:]) else {
        return false
    }
    do {
        try data.write(to: URL(fileURLWithPath: outputPath))
        return true
    } catch {
        return false
    }
}

func analyzeMask(_ pixelBuffer: CVPixelBuffer) -> (Int, Int, Int, Int, Double)? {
    CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
    let width = CVPixelBufferGetWidth(pixelBuffer)
    let height = CVPixelBufferGetHeight(pixelBuffer)
    let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
    guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer) else {
        return nil
    }
    let buffer = baseAddress.assumingMemoryBound(to: UInt8.self)
    var minX = width
    var minY = height
    var maxX = -1
    var maxY = -1
    var foreground = 0
    for y in 0..<height {
        let row = buffer.advanced(by: y * bytesPerRow)
        for x in 0..<width {
            let value = row[x]
            if value > 24 {
                foreground += 1
                if x < minX { minX = x }
                if y < minY { minY = y }
                if x > maxX { maxX = x }
                if y > maxY { maxY = y }
            }
        }
    }
    if foreground == 0 || maxX < minX || maxY < minY {
        return nil
    }
    let coverage = Double(foreground) / Double(width * height)
    return (minX, minY, maxX, maxY, coverage)
}

let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    emit(
        SegmentationPayload(
            status: "invalid_input",
            provider: "apple_vision_person_segmentation",
            image_width: nil,
            image_height: nil,
            mask_path: nil,
            bbox_x: nil,
            bbox_y: nil,
            bbox_width: nil,
            bbox_height: nil,
            coverage_score: 0.0,
            notes: ["Expected an image path and an output mask path."]
        )
    )
    exit(1)
}

let imagePath = arguments[1]
let outputMaskPath = arguments[2]
guard let (cgImage, width, height) = loadCGImage(at: imagePath) else {
    emit(
        SegmentationPayload(
            status: "unreadable_image",
            provider: "apple_vision_person_segmentation",
            image_width: nil,
            image_height: nil,
            mask_path: nil,
            bbox_x: nil,
            bbox_y: nil,
            bbox_width: nil,
            bbox_height: nil,
            coverage_score: 0.0,
            notes: ["The input image could not be decoded into a CGImage."]
        )
    )
    exit(0)
}

let request = VNGeneratePersonSegmentationRequest()
request.qualityLevel = .balanced
request.outputPixelFormat = kCVPixelFormatType_OneComponent8
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

do {
    try handler.perform([request])
} catch {
    emit(
        SegmentationPayload(
            status: "vision_error",
            provider: "apple_vision_person_segmentation",
            image_width: width,
            image_height: height,
            mask_path: nil,
            bbox_x: nil,
            bbox_y: nil,
            bbox_width: nil,
            bbox_height: nil,
            coverage_score: 0.0,
            notes: ["Vision segmentation request failed: \(error.localizedDescription)"]
        )
    )
    exit(0)
}

guard let observation = request.results?.first else {
    emit(
        SegmentationPayload(
            status: "no_person",
            provider: "apple_vision_person_segmentation",
            image_width: width,
            image_height: height,
            mask_path: nil,
            bbox_x: nil,
            bbox_y: nil,
            bbox_width: nil,
            bbox_height: nil,
            coverage_score: 0.0,
            notes: ["No person segmentation mask was produced."]
        )
    )
    exit(0)
}

let pixelBuffer = observation.pixelBuffer
let saved = saveMask(pixelBuffer, to: outputMaskPath)
let bounds = analyzeMask(pixelBuffer)

emit(
    SegmentationPayload(
        status: saved && bounds != nil ? "ok" : (saved ? "low_signal" : "mask_write_failed"),
        provider: "apple_vision_person_segmentation",
        image_width: width,
        image_height: height,
        mask_path: saved ? outputMaskPath : nil,
        bbox_x: bounds != nil ? Double(bounds!.0) / Double(CVPixelBufferGetWidth(pixelBuffer)) : nil,
        bbox_y: bounds != nil ? Double(bounds!.1) / Double(CVPixelBufferGetHeight(pixelBuffer)) : nil,
        bbox_width: bounds != nil ? Double(bounds!.2 - bounds!.0 + 1) / Double(CVPixelBufferGetWidth(pixelBuffer)) : nil,
        bbox_height: bounds != nil ? Double(bounds!.3 - bounds!.1 + 1) / Double(CVPixelBufferGetHeight(pixelBuffer)) : nil,
        coverage_score: bounds?.4 ?? 0.0,
        notes: saved ? [] : ["The mask image could not be written to disk."]
    )
)
