import SwiftUI
import Combine

/// Single source of truth for the UI. The WebSocket bridge publishes
/// decoded events into these properties; views observe them via
/// @EnvironmentObject. Keeping all UI state in one place means any screen
/// (HUD, card, dashboard) sees a consistent snapshot without threading
/// subscribers through every view.
@MainActor
final class AppState: ObservableObject {

    enum VoiceState: String {
        case idle, listening, thinking, speaking, setup, error
    }

    enum UICommand {
        case openDashboard
        case quit
    }

    // --- Live voice state (drives the pill + orb) ---
    @Published var voiceState: VoiceState = .idle
    @Published var transcript: String = ""
    @Published var reply: String = ""
    @Published var audioLevel: Double = 0
    @Published var pillVisible: Bool = false
    @Published var activeAgent: String? = nil
    @Published var activeTool: String? = nil

    // --- Cards ---
    @Published var currentCard: CardPayload? = nil

    // --- Connection ---
    @Published var connected: Bool = false
    @Published var lastError: String? = nil

    // --- Bridges ---
    let bridge: WebSocketClient
    let dashboard: DashboardClient

    /// Dispatched from menu + HUD actions. Owned by AppDelegate.
    var onCommand: ((UICommand) -> Void)?

    private var hideHUDWorkItem: DispatchWorkItem?
    private var hideCardWorkItem: DispatchWorkItem?

    init() {
        self.bridge = WebSocketClient(url: URL(string: "ws://127.0.0.1:17651")!)
        self.dashboard = DashboardClient(baseURL: URL(string: "http://127.0.0.1:17650")!)
        self.bridge.onEvent = { [weak self] event in
            Task { @MainActor in self?.handle(event) }
        }
        self.bridge.onStatus = { [weak self] connected in
            Task { @MainActor in self?.connected = connected }
        }
    }

    // MARK: - Event routing

    private func handle(_ event: BridgeEvent) {
        switch event.type {
        case "ui.state":
            if let raw = event.string("state"),
               let vs = VoiceState(rawValue: raw) {
                voiceState = vs
                if vs != .idle { showHUD() } else { scheduleHide(after: 2.8) }
            }

        case "wake.triggered":
            currentCard = nil
            transcript = ""
            reply = ""
            voiceState = .listening
            showHUD()

        case "voice.transcript", "voice.followup_transcript":
            if let t = event.string("transcript") ?? event.string("text"), !t.isEmpty {
                transcript = t
                showHUD()
            }

        case "voice.level":
            if let v = event.double("level") ?? event.double("rms") {
                audioLevel = max(0, min(1, v))
            }

        case "supervisor.reply":
            if let r = event.string("reply") ?? event.string("text"), !r.isEmpty {
                reply = r
                showHUD()
            }

        case "supervisor.delegate":
            activeAgent = event.string("agent")

        case "tool.dispatch":
            activeTool = event.string("tool")

        case "tool.result":
            activeTool = nil

        case "reminder.fired":
            if let t = event.string("text") {
                reply = "⏰ " + t
                showHUD()
            }

        case "ui.card":
            if let card = CardPayload.from(event: event) {
                currentCard = card
                scheduleCardHide(after: Double(card.ttlMs) / 1000.0)
            }

        case "ui.show_pill":
            showHUD()

        case "ui.hide_pill":
            pillVisible = false

        case "voice.loop_error", "browser.error", "web.search.error":
            voiceState = .error
            lastError = event.string("error") ?? event.string("message")
            scheduleHide(after: 4.0)

        default:
            break
        }
    }

    // MARK: - HUD visibility

    func showHUD() {
        pillVisible = true
        hideHUDWorkItem?.cancel()
    }

    func hideHUD() {
        hideHUDWorkItem?.cancel()
        pillVisible = false
    }

    func toggleHUD() {
        if pillVisible { hideHUD() } else { showHUD() }
    }

    private func scheduleHide(after seconds: TimeInterval) {
        hideHUDWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            if self.voiceState == .idle && self.currentCard == nil {
                self.pillVisible = false
            }
        }
        hideHUDWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    private func scheduleCardHide(after seconds: TimeInterval) {
        hideCardWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in
            self?.currentCard = nil
        }
        hideCardWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    // MARK: - Commands back to daemon

    func sendText(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        bridge.send(command: "cmd.submit_text", data: ["text": trimmed])
    }

    func openURL(_ urlString: String) {
        guard let url = URL(string: urlString) else { return }
        NSWorkspace.shared.open(url)
    }

    func stop() {
        bridge.send(command: "cmd.stop", data: [:])
    }

    func bargeIn() {
        bridge.send(command: "cmd.barge_in", data: [:])
    }
}
