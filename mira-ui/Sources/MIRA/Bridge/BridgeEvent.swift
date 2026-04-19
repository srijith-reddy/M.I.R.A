import Foundation

/// Raw decoded frame from the Python ui_bridge. The Python side stringifies
/// everything with `default=str`, so we keep `data` as a loose JSON
/// dictionary and pull fields out with typed accessors. Typed Codable
/// sub-types per event name would be cleaner but explodes in scope — the
/// wire format adds fields all the time and we don't want to rev a Swift
/// type every time a new field appears in `ui.card`.
struct BridgeEvent {
    let type: String
    let ts: Double
    let data: [String: Any]

    static func decode(from raw: Data) -> BridgeEvent? {
        guard
            let obj = try? JSONSerialization.jsonObject(with: raw) as? [String: Any],
            let type = obj["type"] as? String
        else { return nil }
        let ts = (obj["ts"] as? Double) ?? 0
        let data = (obj["data"] as? [String: Any]) ?? [:]
        return BridgeEvent(type: type, ts: ts, data: data)
    }

    // MARK: - Typed accessors

    func string(_ key: String) -> String? {
        if let s = data[key] as? String { return s }
        if let n = data[key] as? NSNumber { return n.stringValue }
        return nil
    }

    func double(_ key: String) -> Double? {
        if let d = data[key] as? Double { return d }
        if let i = data[key] as? Int { return Double(i) }
        if let s = data[key] as? String { return Double(s) }
        return nil
    }

    func int(_ key: String) -> Int? {
        if let i = data[key] as? Int { return i }
        if let d = data[key] as? Double { return Int(d) }
        if let s = data[key] as? String { return Int(s) }
        return nil
    }

    func array(_ key: String) -> [[String: Any]]? {
        data[key] as? [[String: Any]]
    }
}
