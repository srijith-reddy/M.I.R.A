import Foundation
import Combine

/// HTTP client for the dashboard REST API (`/api/stats`, `/api/turns`,
/// `/api/llm_spend`, `/api/events?turn_id=…`). Polls every 3 seconds while
/// the dashboard window is visible, stops otherwise — the 3s cadence
/// matches the legacy web dashboard and keeps background CPU near zero.
@MainActor
final class DashboardClient: ObservableObject {

    @Published var stats: DashboardStats = .empty
    @Published var turns: [TurnRow] = []
    @Published var spend: [SpendRow] = []
    @Published var trace: [TraceEvent] = []
    @Published var loading: Bool = false
    @Published var selectedTurnID: String?

    private let baseURL: URL
    private let session: URLSession
    private var pollTimer: Timer?

    init(baseURL: URL) {
        self.baseURL = baseURL
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 4
        self.session = URLSession(configuration: cfg)
    }

    // MARK: - Lifecycle

    func start() {
        // idle; refresh is explicit.
    }

    func stop() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    func beginPolling() {
        stop()
        Task { await refresh() }
        let timer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in await self?.refresh() }
        }
        RunLoop.main.add(timer, forMode: .common)
        pollTimer = timer
    }

    func endPolling() {
        stop()
    }

    // MARK: - Fetch

    func refresh() async {
        loading = true
        defer { loading = false }
        async let s: DashboardStats? = fetch("/api/stats")
        async let t: TurnsResponse? = fetch("/api/turns?limit=100")
        async let sp: SpendResponse? = fetch("/api/llm_spend")
        if let v = await s { stats = v }
        if let v = await t { turns = v.items }
        if let v = await sp { spend = v.items }
        if let id = selectedTurnID {
            trace = (await fetch("/api/events?turn_id=\(id)") as EventsResponse?)?.items ?? []
        }
    }

    func selectTurn(_ id: String) async {
        selectedTurnID = id
        trace = (await fetch("/api/events?turn_id=\(id)") as EventsResponse?)?.items ?? []
    }

    // MARK: - Core

    private func fetch<T: Decodable>(_ path: String) async -> T? {
        guard let url = URL(string: path, relativeTo: baseURL) else { return nil }
        do {
            let (data, _) = try await session.data(from: url)
            return try JSONDecoder.dashboard.decode(T.self, from: data)
        } catch {
            return nil
        }
    }
}

// MARK: - Response models

struct DashboardStats: Decodable {
    let turns_24h: Int
    let errors_24h: Int
    let cost_usd_24h: Double
    let p50_latency_ms: Double?
    let p95_latency_ms: Double?

    static let empty = DashboardStats(
        turns_24h: 0, errors_24h: 0, cost_usd_24h: 0,
        p50_latency_ms: nil, p95_latency_ms: nil
    )
}

struct TurnsResponse: Decodable { let items: [TurnRow] }

struct TurnRow: Decodable, Identifiable {
    let turn_id: String
    let user_id: String?
    let transcript: String?
    let reply: String?
    let status: String?
    let via: String?
    let started_at: Double?
    let ended_at: Double?
    let latency_ms: Double?
    let cost_usd: Double?
    var id: String { turn_id }
}

struct SpendResponse: Decodable { let items: [SpendRow] }

struct SpendRow: Decodable, Identifiable {
    let model: String
    let provider: String
    let calls: Int
    let cost_usd: Double
    let prompt_tokens: Int
    let completion_tokens: Int
    var id: String { "\(provider)/\(model)" }
}

struct EventsResponse: Decodable { let items: [TraceEvent] }

struct TraceEvent: Decodable, Identifiable {
    let ts: Double
    let turn_id: String?
    let span_id: String?
    let parent_span_id: String?
    let event: String
    let fields: [String: AnyCodable]?

    var id: String { "\(ts)-\(event)" }
}

extension JSONDecoder {
    static let dashboard: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .useDefaultKeys
        return d
    }()
}

/// Minimal AnyCodable so the `fields` dict on a trace event decodes. The
/// dashboard only reads it with JSON.stringify semantics, so we don't need
/// full fidelity — just enough to stringify for display.
struct AnyCodable: Decodable {
    let value: Any

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let v = try? c.decode(String.self)  { self.value = v; return }
        if let v = try? c.decode(Int.self)     { self.value = v; return }
        if let v = try? c.decode(Double.self)  { self.value = v; return }
        if let v = try? c.decode(Bool.self)    { self.value = v; return }
        if let v = try? c.decode([AnyCodable].self) { self.value = v.map(\.value); return }
        if let v = try? c.decode([String: AnyCodable].self) {
            self.value = v.mapValues(\.value); return
        }
        self.value = NSNull()
    }
}
