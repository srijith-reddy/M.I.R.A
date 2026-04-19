import Foundation

// Wire types mirroring src/mira/obs/ui_bridge.py. Every frame the bridge
// sends is `{ v, type, ts, data }`; every command we send back is
// `{ type, data }`. Keeping these in one file — the protocol is small
// enough that scattering it across the project would cost more than it
// saves.

enum Protocol {
    static let version = 1
    static let host = "127.0.0.1"
    static let defaultPort = 17651
}

// MARK: - Incoming

/// Every frame from the Python bridge decodes into this envelope. `data`
/// is kept as a raw JSON blob because each event has a different shape —
/// callers pull the fields they care about via the `Event` enum.
struct Frame: Decodable {
    let v: Int
    let type: String
    let ts: Double
    let data: JSON
}

/// Discriminated event union. We only decode the events the UI actually
/// renders; everything else falls through to `.other` so new Python-side
/// events don't crash the HUD.
enum Event {
    case hello(protocol: Int, app: String)
    case uiState(state: VoiceState)
    case wakeTriggered
    case transcript(text: String, partial: Bool)
    case level(Double)
    case supervisorDelegate(agent: String, task: String?)
    case supervisorReply(text: String)
    case agentDispatch(agent: String, summary: String?)
    case toolDispatch(tool: String, args: JSON?)
    case toolResult(tool: String, ok: Bool, summary: String?)
    case llmCall(model: String, latencyMs: Double?, costUsd: Double?)
    case reminderFired(text: String)
    case reminderCreated(text: String, when: String?)
    case memoryRecalled(snippet: String)
    case confirmationRequired(agent: String, prompt: String, id: String?)
    case error(scope: String, message: String)
    case other(type: String, data: JSON)

    static func decode(_ frame: Frame) -> Event {
        let d = frame.data
        switch frame.type {
        case "hello":
            return .hello(
                protocol: d["protocol"]?.intValue ?? 1,
                app: d["app"]?.stringValue ?? "mira"
            )
        case "ui.state":
            let raw = d["state"]?.stringValue ?? "idle"
            return .uiState(state: VoiceState(rawValue: raw) ?? .idle)
        case "wake.triggered":
            return .wakeTriggered
        case "voice.transcript", "voice.followup_transcript":
            return .transcript(
                text: d["text"]?.stringValue ?? "",
                partial: d["partial"]?.boolValue ?? false
            )
        case "voice.level":
            return .level(d["level"]?.doubleValue ?? 0)
        case "supervisor.delegate":
            return .supervisorDelegate(
                agent: d["agent"]?.stringValue ?? "?",
                task: d["task"]?.stringValue
            )
        case "supervisor.reply":
            return .supervisorReply(text: d["text"]?.stringValue ?? "")
        case "agent.dispatch":
            return .agentDispatch(
                agent: d["agent"]?.stringValue ?? "?",
                summary: d["summary"]?.stringValue ?? d["task"]?.stringValue
            )
        case "tool.dispatch":
            return .toolDispatch(
                tool: d["tool"]?.stringValue ?? "?",
                args: d["args"]
            )
        case "tool.result":
            return .toolResult(
                tool: d["tool"]?.stringValue ?? "?",
                ok: d["ok"]?.boolValue ?? true,
                summary: d["summary"]?.stringValue
            )
        case "llm.call":
            return .llmCall(
                model: d["model"]?.stringValue ?? "?",
                latencyMs: d["latency_ms"]?.doubleValue,
                costUsd: d["cost_usd"]?.doubleValue
            )
        case "reminder.fired":
            return .reminderFired(text: d["text"]?.stringValue ?? "")
        case "reminder.created":
            return .reminderCreated(
                text: d["text"]?.stringValue ?? "",
                when: d["when"]?.stringValue
            )
        case "memory.recalled":
            return .memoryRecalled(snippet: d["snippet"]?.stringValue ?? d["text"]?.stringValue ?? "")
        case "browser_agent.confirmation_required",
             "commerce.confirmation_required":
            let agent = frame.type.hasPrefix("browser") ? "browser" : "commerce"
            return .confirmationRequired(
                agent: agent,
                prompt: d["prompt"]?.stringValue ?? d["action"]?.stringValue ?? "Confirm?",
                id: d["id"]?.stringValue
            )
        case "voice.loop_error", "browser.error", "web.search.error":
            return .error(
                scope: frame.type,
                message: d["error"]?.stringValue ?? d["message"]?.stringValue ?? "unknown"
            )
        default:
            return .other(type: frame.type, data: d)
        }
    }
}

enum VoiceState: String {
    case idle, listening, thinking, speaking, setup

    var label: String {
        switch self {
        case .idle: return "Say \"Hey Mira\""
        case .listening: return "Listening…"
        case .thinking: return "Thinking…"
        case .speaking: return "Speaking"
        case .setup: return "Setup required"
        }
    }
}

// MARK: - Outgoing

/// Commands the HUD sends back to Python. Kept flat because the bridge's
/// command surface is intentionally tiny — adding one means thinking about
/// the failure mode if two clients send it at once.
enum Command {
    case stop
    case bargeIn
    case submitText(String)

    func encode() -> Data? {
        var payload: [String: Any] = [:]
        switch self {
        case .stop:
            payload = ["type": "cmd.stop"]
        case .bargeIn:
            payload = ["type": "cmd.barge_in"]
        case .submitText(let text):
            payload = ["type": "cmd.submit_text", "data": ["text": text]]
        }
        return try? JSONSerialization.data(withJSONObject: payload)
    }
}

// MARK: - JSON value

/// Minimal JSON value. Swift's `JSONDecoder` can't decode into an opaque
/// dict-of-Any directly, so we wrap with a recursive enum that keeps
/// common accessors inline. We read more fields than we write, so the
/// ergonomics here matter more than a full-featured JSON library would.
indirect enum JSON: Decodable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case array([JSON])
    case object([String: JSON])
    case null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let b = try? c.decode(Bool.self) { self = .bool(b); return }
        if let i = try? c.decode(Int.self) { self = .int(i); return }
        if let d = try? c.decode(Double.self) { self = .double(d); return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let a = try? c.decode([JSON].self) { self = .array(a); return }
        if let o = try? c.decode([String: JSON].self) { self = .object(o); return }
        self = .null
    }

    subscript(key: String) -> JSON? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }
    var intValue: Int? {
        switch self {
        case .int(let i): return i
        case .double(let d): return Int(d)
        default: return nil
        }
    }
    var doubleValue: Double? {
        switch self {
        case .double(let d): return d
        case .int(let i): return Double(i)
        default: return nil
        }
    }
    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }
}
