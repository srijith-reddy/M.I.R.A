import Foundation

/// Decoded form of a `ui.card` event. Mirrors the Python `Card` dataclass
/// in src/mira/ui/cards.py. The Python side may or may not set `kind` —
/// when absent we fall back to heuristics over the rows.
struct CardPayload: Identifiable {
    let id = UUID()
    let kind: CardKind
    let title: String
    let subtitle: String?
    let footer: String?
    let rows: [CardRow]
    let ttlMs: Int
    let agent: String?

    static func from(event: BridgeEvent) -> CardPayload? {
        let title = event.string("title") ?? "Results"
        guard let rawRows = event.array("rows"), !rawRows.isEmpty else { return nil }
        let rows = rawRows.compactMap(CardRow.from(dict:))
        guard !rows.isEmpty else { return nil }

        let kindStr = event.string("kind")
            ?? event.string("card_type")
            ?? "list"
        let agent = event.string("agent")
        let kind = CardKind.infer(
            explicit: kindStr,
            agent: agent,
            rows: rows
        )

        return CardPayload(
            kind: kind,
            title: title,
            subtitle: event.string("subtitle"),
            footer: event.string("footer"),
            rows: rows,
            ttlMs: event.int("ttl_ms") ?? 20000,
            agent: agent
        )
    }
}

struct CardRow: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String?
    let trailing: String?
    let meta: String?
    let url: String?
    let thumbnail: String?
    let badge: String?        // optional badge text (e.g. "amazon.com")
    let rating: Double?       // 0–5 float, rendered as stars when present
    let startTime: String?    // calendar: "09:30"
    let endTime: String?      // calendar: "10:15"

    static func from(dict d: [String: Any]) -> CardRow? {
        guard let title = d["title"] as? String, !title.isEmpty else { return nil }
        return CardRow(
            title: title,
            subtitle: d["subtitle"] as? String,
            trailing: d["trailing"] as? String,
            meta: d["meta"] as? String,
            url: d["url"] as? String,
            thumbnail: d["thumbnail"] as? String,
            badge: d["badge"] as? String,
            rating: d["rating"] as? Double,
            startTime: d["start_time"] as? String,
            endTime: d["end_time"] as? String
        )
    }
}

/// The renderer picks a template by `CardKind`. Kept narrow so every
/// agent has exactly one home; "generic list" is the fallback.
enum CardKind: String {
    case product     // commerce: price comparisons, shopping
    case source      // research: cited web sources
    case email       // communication: inbox items
    case calendar    // communication: upcoming events
    case reminder    // reminders
    case action      // browser / device: one-shot action summary
    case list        // generic fallback

    static func infer(explicit: String, agent: String?, rows: [CardRow]) -> CardKind {
        if let k = CardKind(rawValue: explicit.lowercased()) { return k }
        // Explicit card_type wasn't useful (e.g. "list"); try the agent.
        switch agent {
        case "commerce":      return .product
        case "research":      return .source
        case "communication":
            // Mixed inbox — if any row has start_time it's probably calendar.
            if rows.contains(where: { $0.startTime != nil }) { return .calendar }
            return .email
        case "browser", "device": return .action
        default: break
        }
        // Final fallback: rating present → product, url present → source
        if rows.contains(where: { $0.rating != nil || $0.trailing?.contains("$") == true }) {
            return .product
        }
        if rows.contains(where: { $0.url != nil }) {
            return .source
        }
        return .list
    }
}
