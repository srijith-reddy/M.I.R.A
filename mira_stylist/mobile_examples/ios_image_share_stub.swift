import Foundation

// MIRA Stylist image-share scaffold.
// This intentionally focuses on image-first garment ingestion before full AR scanning.

struct GarmentImageSharePayload: Codable {
    let uploaded_by: String
    let image_base64: String
    let original_filename: String?
    let mime_type: String?
    let referring_page_url: String?
    let notes: String?
}

final class MiraStylistImageShareClient {
    private let backendBaseURL = URL(string: "http://localhost:8000")!

    func ingestScreenshot(
        userId: String,
        imageData: Data,
        referringPageURL: String? = nil
    ) {
        let payload = GarmentImageSharePayload(
            uploaded_by: userId,
            image_base64: imageData.base64EncodedString(),
            original_filename: "shared_screenshot.png",
            mime_type: "image/png",
            referring_page_url: referringPageURL,
            notes: "Shared from iOS image-first flow."
        )

        // TODO:
        // - POST to /garments/ingest/screenshot
        // - surface returned candidate images for user confirmation
        // - call /garments/ingest/select-candidate if review is needed
        print("Would POST screenshot payload to \(backendBaseURL)/garments/ingest/screenshot")
        print(payload)
    }
}
