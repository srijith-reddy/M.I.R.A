import Foundation
import Combine

// WebSocket client for src/mira/obs/ui_bridge.py. Connects to
// 127.0.0.1:17651, publishes decoded events via the `events` subject, and
// auto-reconnects with exponential backoff so the HUD survives a MIRA
// daemon restart without the user noticing.
//
// Design notes:
//   * One connection per process. The Python side broadcasts to every
//     connected client, but there's only ever one HUD running on the
//     machine, so we don't model fan-out here.
//   * The Python bridge may not be up when the HUD launches (user starts
//     MIRA after the app, or the port is briefly contended). We keep
//     reconnecting — silently — rather than surfacing "not connected" UI.
//     `@Published isConnected` lets views that care dim themselves.
//   * URLSessionWebSocketTask is the stdlib option. It's fine. Don't pull
//     in Starscream unless you need permessage-deflate.

@MainActor
final class Bridge: ObservableObject {
    @Published private(set) var isConnected: Bool = false

    /// Stream of decoded events. SwiftUI views subscribe via `.onReceive`.
    /// We use PassthroughSubject (not @Published) because events are a
    /// firehose — republishing each one would force every dependent view
    /// to re-evaluate whether the event is relevant.
    let events = PassthroughSubject<Event, Never>()

    private var task: URLSessionWebSocketTask?
    private var session: URLSession
    private let url: URL
    private var retryDelay: TimeInterval = 0.5
    private var isStopped: Bool = false

    init(port: Int = Protocol.defaultPort) {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        // Loopback only — no proxy, no cookies, no cache. Keeping the
        // session minimal avoids surprising system-wide settings.
        config.httpCookieStorage = nil
        config.urlCache = nil
        config.connectionProxyDictionary = [:]
        self.session = URLSession(configuration: config)
        self.url = URL(string: "ws://\(Protocol.host):\(port)")!
    }

    func start() {
        isStopped = false
        connect()
    }

    func stop() {
        isStopped = true
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        isConnected = false
    }

    func send(_ command: Command) {
        guard let data = command.encode(), let task else { return }
        guard let s = String(data: data, encoding: .utf8) else { return }
        task.send(.string(s)) { _ in
            // Failure here means the socket died mid-send; the read loop
            // will notice, flip `isConnected`, and kick a reconnect. No
            // reason to handle it twice.
        }
    }

    // MARK: - Internal

    private func connect() {
        guard !isStopped else { return }
        task?.cancel(with: .goingAway, reason: nil)
        let t = session.webSocketTask(with: url)
        task = t
        t.resume()
        receive()
    }

    private func receive() {
        guard let task else { return }
        task.receive { [weak self] result in
            guard let self else { return }
            Task { @MainActor in
                switch result {
                case .success(let message):
                    // First successful receive implicitly confirms the
                    // handshake. Reset backoff so the next disconnect
                    // reconnects quickly.
                    self.isConnected = true
                    self.retryDelay = 0.5
                    self.handle(message)
                    self.receive()
                case .failure:
                    self.isConnected = false
                    self.scheduleReconnect()
                }
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        let data: Data
        switch message {
        case .data(let d): data = d
        case .string(let s): data = Data(s.utf8)
        @unknown default: return
        }
        guard let frame = try? JSONDecoder().decode(Frame.self, from: data) else {
            return
        }
        events.send(Event.decode(frame))
    }

    private func scheduleReconnect() {
        guard !isStopped else { return }
        let delay = retryDelay
        // Cap at 10s — beyond that the user notices the HUD is dead but
        // shorter retries waste battery when the daemon is intentionally
        // off.
        retryDelay = min(retryDelay * 1.7, 10.0)
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            self.connect()
        }
    }
}
