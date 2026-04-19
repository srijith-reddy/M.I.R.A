import SwiftUI
import Combine

// The HUD's root SwiftUI view. Three horizontal bands stacked vertically:
//   * Header: orb + state label + live transcript
//   * Reply: the most recent supervisor reply (when one exists)
//   * Activity stack: agent/tool/reminder cards (most recent first)
//
// Everything streams in from `Bridge.events`. We translate events into a
// `ViewModel` struct rather than binding directly to raw events — the UI
// should re-render on *meaningful* changes, not every log frame.

@MainActor
final class HUDViewModel: ObservableObject {
    @Published var state: VoiceState = .idle
    @Published var transcript: String = ""
    @Published var reply: String = ""
    @Published var cards: [ActivityCard] = []
    @Published var connected: Bool = false
    @Published var errorLine: String? = nil

    /// Latest audio level 0...1 — wired later when voice loop publishes it.
    @Published var level: Double = 0.0

    private var bag = Set<AnyCancellable>()

    init(bridge: Bridge) {
        bridge.$isConnected
            .receive(on: RunLoop.main)
            .assign(to: &$connected)

        bridge.events
            .receive(on: RunLoop.main)
            .sink { [weak self] event in self?.apply(event) }
            .store(in: &bag)
    }

    private func apply(_ event: Event) {
        switch event {
        case .hello:
            // Nothing visible; connection state already flips the header
            // dot. Kept so the switch stays exhaustive-ish.
            break
        case .uiState(let s):
            state = s
            if s == .listening { transcript = "" }
            if s != .speaking { /* leave reply visible */ }
        case .wakeTriggered:
            transcript = ""
            reply = ""
            errorLine = nil
        case .transcript(let text, _):
            transcript = text
        case .level(let v):
            level = max(0, min(1, v))
        case .supervisorDelegate(let agent, let task):
            pushCard(.init(kind: .agent, title: agent, body: task ?? ""))
        case .supervisorReply(let text):
            reply = text
        case .agentDispatch(let agent, let summary):
            pushCard(.init(kind: .agent, title: agent, body: summary ?? ""))
        case .toolDispatch(let tool, _):
            pushCard(.init(kind: .tool, title: tool, body: "running…"))
        case .toolResult(let tool, let ok, let summary):
            // Collapse into the matching dispatch card if it exists —
            // otherwise append. Keeps the activity stack from growing
            // linearly with turn length.
            if let i = cards.firstIndex(where: { $0.kind == .tool && $0.title == tool && $0.body == "running…" }) {
                cards[i].body = summary ?? (ok ? "done" : "failed")
                cards[i].ok = ok
            } else {
                pushCard(.init(kind: .tool, title: tool,
                               body: summary ?? (ok ? "done" : "failed"), ok: ok))
            }
        case .llmCall:
            break
        case .reminderFired(let text):
            pushCard(.init(kind: .reminder, title: "Reminder", body: text))
        case .reminderCreated(let text, let when):
            let body = [text, when].compactMap { $0 }.joined(separator: " — ")
            pushCard(.init(kind: .reminder, title: "Scheduled", body: body))
        case .memoryRecalled(let snippet):
            pushCard(.init(kind: .memory, title: "Memory", body: snippet))
        case .confirmationRequired(let agent, let prompt, _):
            pushCard(.init(kind: .confirm, title: "\(agent) needs OK", body: prompt))
        case .error(let scope, let message):
            errorLine = "\(scope): \(message)"
        case .other:
            break
        }
    }

    private func pushCard(_ card: ActivityCard) {
        cards.insert(card, at: 0)
        // Cap history — older activity rolls off rather than scrolling
        // forever. 12 is empirically enough for a 3-4 step turn.
        if cards.count > 12 {
            cards = Array(cards.prefix(12))
        }
    }
}

struct ActivityCard: Identifiable, Equatable {
    enum Kind { case agent, tool, llm, reminder, memory, confirm }
    let id = UUID()
    let kind: Kind
    var title: String
    var body: String
    var ok: Bool = true
}

// MARK: - View

struct HUDView: View {
    @ObservedObject var vm: HUDViewModel
    let bridge: Bridge

    @State private var draft: String = ""
    @FocusState private var textFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            if !vm.reply.isEmpty {
                replyView
            }
            if !vm.cards.isEmpty {
                Divider().background(Theme.Color.hairline)
                activity
            }
            if let err = vm.errorLine {
                errorBar(err)
            }
            composer
        }
        .padding(.horizontal, Theme.Metric.padH)
        .padding(.vertical, Theme.Metric.padV)
        .frame(width: Theme.Metric.panelWidth, alignment: .leading)
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 14) {
            Orb(state: vm.state, level: vm.level)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Circle()
                        .fill(vm.connected ? Theme.Color.accent : Theme.Color.textTertiary)
                        .frame(width: 6, height: 6)
                    Text(vm.state.label)
                        .font(Theme.Font.state)
                        .foregroundStyle(Theme.Color.textSecondary)
                        .textCase(.uppercase)
                        .tracking(0.8)
                }
                Text(vm.transcript.isEmpty ? "—" : vm.transcript)
                    .font(Theme.Font.transcript)
                    .foregroundStyle(vm.transcript.isEmpty ? Theme.Color.textTertiary : Theme.Color.textPrimary)
                    .lineLimit(3)
                    .animation(.easeOut(duration: 0.15), value: vm.transcript)
            }
            Spacer(minLength: 0)
        }
    }

    private var replyView: some View {
        Text(vm.reply)
            .font(Theme.Font.reply)
            .foregroundStyle(Theme.Color.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
            .lineLimit(8)
            .padding(.top, 2)
    }

    private var activity: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(vm.cards) { card in
                    ActivityRow(card: card)
                        .transition(.asymmetric(
                            insertion: .move(edge: .top).combined(with: .opacity),
                            removal: .opacity
                        ))
                }
            }
            .animation(.easeOut(duration: 0.2), value: vm.cards)
        }
        .frame(maxHeight: 260)
    }

    private func errorBar(_ message: String) -> some View {
        HStack(spacing: 8) {
            Circle().fill(Theme.Color.danger).frame(width: 5, height: 5)
            Text(message)
                .font(Theme.Font.footnote)
                .foregroundStyle(Theme.Color.danger)
                .lineLimit(2)
            Spacer()
            Button(action: { vm.errorLine = nil }) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(Theme.Color.textTertiary)
            }
            .buttonStyle(.plain)
        }
    }

    private var composer: some View {
        HStack(spacing: 10) {
            Image(systemName: "text.cursor")
                .font(.system(size: 12))
                .foregroundStyle(Theme.Color.textTertiary)
            TextField("Type instead of speaking…", text: $draft)
                .textFieldStyle(.plain)
                .font(Theme.Font.reply)
                .foregroundStyle(Theme.Color.textPrimary)
                .focused($textFocused)
                .onSubmit(submit)
            if vm.state == .speaking {
                Button(action: { bridge.send(.bargeIn) }) {
                    Image(systemName: "hand.raised.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.Color.textSecondary)
                }
                .buttonStyle(.plain)
                .help("Interrupt")
            } else if vm.state == .thinking || vm.state == .listening {
                Button(action: { bridge.send(.stop) }) {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.Color.textSecondary)
                }
                .buttonStyle(.plain)
                .help("Stop")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Theme.Color.cardFill)
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .stroke(Theme.Color.cardStroke, lineWidth: 0.5)
                )
        )
    }

    private func submit() {
        let t = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        bridge.send(.submitText(t))
        draft = ""
    }
}

// MARK: - Activity row

struct ActivityRow: View {
    let card: ActivityCard

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: iconName)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 16, height: 16)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 2) {
                Text(card.title.uppercased())
                    .font(Theme.Font.cardTitle)
                    .tracking(0.7)
                    .foregroundStyle(tint)
                Text(card.body)
                    .font(Theme.Font.cardBody)
                    .foregroundStyle(card.ok ? Theme.Color.textPrimary : Theme.Color.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .lineLimit(3)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(
            RoundedRectangle(cornerRadius: Theme.Metric.cardRadius, style: .continuous)
                .fill(Theme.Color.cardFill)
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.Metric.cardRadius, style: .continuous)
                        .stroke(Theme.Color.cardStroke, lineWidth: 0.5)
                )
        )
    }

    private var iconName: String {
        switch card.kind {
        case .agent: return "person.2.fill"
        case .tool: return "wrench.and.screwdriver.fill"
        case .llm: return "sparkles"
        case .reminder: return "bell.fill"
        case .memory: return "brain"
        case .confirm: return "questionmark.circle.fill"
        }
    }

    private var tint: Color {
        switch card.kind {
        case .agent: return Theme.Color.accent
        case .tool: return Theme.Color.textSecondary
        case .llm: return Theme.Color.textTertiary
        case .reminder: return Theme.Color.warm
        case .memory: return Theme.Color.accent.opacity(0.8)
        case .confirm: return Theme.Color.warm
        }
    }
}
