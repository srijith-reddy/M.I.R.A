import Foundation

/// Thin wrapper around URLSessionWebSocketTask with auto-reconnect.
///
/// Design notes:
///   * Messages fan out via a single `onEvent` closure rather than Combine
///     publishers — AppState is the only subscriber, no reason to pay for
///     Combine plumbing here.
///   * Exponential backoff capped at 4s. The daemon is loopback; either
///     it's up in <1s after a restart or something's wrong and aggressive
///     retry won't help.
///   * `URLSessionWebSocketTask.receive` is one-shot — we re-arm it after
///     each message. Missing a re-arm silently wedges the socket; a guard
///     on `task.state == .running` keeps us from spinning after close.
final class WebSocketClient {

    var onEvent: ((BridgeEvent) -> Void)?
    var onStatus: ((Bool) -> Void)?

    private let url: URL
    private let session: URLSession
    private var task: URLSessionWebSocketTask?
    private var backoff: TimeInterval = 0.5
    private var shouldRun = false

    init(url: URL) {
        self.url = url
        self.session = URLSession(configuration: .ephemeral)
    }

    func connect() {
        shouldRun = true
        openSocket()
    }

    func disconnect() {
        shouldRun = false
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        onStatus?(false)
    }

    func send(command type: String, data: [String: Any]) {
        let frame: [String: Any] = ["type": type, "data": data]
        guard
            let task = task,
            let bytes = try? JSONSerialization.data(withJSONObject: frame),
            let text = String(data: bytes, encoding: .utf8)
        else { return }
        task.send(.string(text)) { _ in }
    }

    // MARK: - Internals

    private func openSocket() {
        let newTask = session.webSocketTask(with: url)
        self.task = newTask
        newTask.resume()
        listen()
    }

    private func listen() {
        guard let task = task else { return }
        task.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                self.onStatus?(true)
                self.backoff = 0.5
                self.handle(message)
                if task.state == .running {
                    self.listen()
                }
            case .failure:
                self.onStatus?(false)
                self.scheduleReconnect()
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        let data: Data?
        switch message {
        case .data(let d): data = d
        case .string(let s): data = s.data(using: .utf8)
        @unknown default: data = nil
        }
        guard let data, let event = BridgeEvent.decode(from: data) else { return }
        onEvent?(event)
    }

    private func scheduleReconnect() {
        guard shouldRun else { return }
        let delay = backoff
        backoff = min(backoff * 1.6, 4.0)
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.shouldRun else { return }
            self.openSocket()
        }
    }
}
