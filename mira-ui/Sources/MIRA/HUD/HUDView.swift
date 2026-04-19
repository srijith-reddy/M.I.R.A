import SwiftUI

struct HUDView: View {
    @EnvironmentObject private var state: AppState
    @State private var expanded: Bool = false
    @FocusState private var inputFocused: Bool
    @State private var inputText: String = ""

    var body: some View {
        ZStack(alignment: .top) {
            Color.clear // click-through outside the pill

            if state.pillVisible {
                pill
                    .transition(.asymmetric(
                        insertion: .opacity.combined(with: .move(edge: .top)),
                        removal: .opacity.combined(with: .scale(scale: 0.98))
                    ))
            }
        }
        .animation(.spring(response: 0.35, dampingFraction: 0.82), value: state.pillVisible)
        .animation(.spring(response: 0.4,  dampingFraction: 0.8),  value: expanded)
    }

    private var pill: some View {
        GlassPanel(radius: Metrics.pillRadius) {
            HStack(spacing: 10) {
                Orb(state: state.voiceState, level: state.audioLevel)
                    .frame(width: 32, height: 32)

                if expanded {
                    expandedInput
                } else {
                    glanceColumn
                    Waveform(level: state.audioLevel, state: state.voiceState)
                        .frame(width: 3, height: 28)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
        }
        .frame(width: expanded ? Metrics.pillWidth - 24 : pillIntrinsicWidth,
               height: Metrics.pillHeight, alignment: .leading)
        .contentShape(RoundedRectangle(cornerRadius: Metrics.pillRadius))
        .onTapGesture { if !expanded { expand() } }
        .padding(.top, 4)
    }

    private var pillIntrinsicWidth: CGFloat {
        // Grow to fit the body text without jumping. Measured values
        // approximate SF Pro kerning at 14pt.
        let base: CGFloat = 120
        let per: CGFloat = 7
        let n = CGFloat(max(displayText.count, 10))
        return min(560 - 24, base + n * per)
    }

    private var glanceColumn: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(Typography.orbLabel)
                .foregroundStyle(labelColor)
                .textCase(.uppercase)
                .tracking(1.2)

            Text(displayText)
                .font(Typography.pillBody)
                .foregroundStyle(Palette.text)
                .lineLimit(2)
                .truncationMode(.tail)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(.easeOut(duration: 0.18), value: displayText)
    }

    private var expandedInput: some View {
        HStack(spacing: 8) {
            TextField("Ask MIRA…", text: $inputText)
                .textFieldStyle(.plain)
                .font(Typography.pillBody)
                .foregroundStyle(Palette.text)
                .focused($inputFocused)
                .onSubmit { submit() }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(Color.white.opacity(0.05))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .strokeBorder(
                            inputFocused ? Palette.accentB.opacity(0.45) : Color.white.opacity(0.08),
                            lineWidth: 1
                        )
                )

            IconButton(system: "gauge") { state.onCommand?(.openDashboard) }
            IconButton(system: "xmark") { collapse() }
        }
    }

    // MARK: - Behavior

    private func expand() {
        expanded = true
        state.showHUD()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            inputFocused = true
        }
    }

    private func collapse() {
        expanded = false
        inputText = ""
        inputFocused = false
    }

    private func submit() {
        let text = inputText.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }
        state.sendText(text)
        inputText = ""
        collapse()
    }

    // MARK: - Derived

    private var label: String {
        switch state.voiceState {
        case .idle:      return "Idle"
        case .listening: return "Listening"
        case .thinking:
            if let a = state.activeAgent { return "Thinking · \(a)" }
            return "Thinking"
        case .speaking:  return "Speaking"
        case .setup:     return "Setup"
        case .error:     return "Error"
        }
    }

    private var labelColor: Color {
        switch state.voiceState {
        case .listening: return Palette.accentB
        case .thinking:  return Palette.accentA
        case .speaking:  return Palette.accentC
        case .error:     return Palette.danger
        default:         return Palette.muted
        }
    }

    private var displayText: String {
        switch state.voiceState {
        case .listening:
            return state.transcript.isEmpty ? "…" : state.transcript
        case .thinking:
            if let t = state.activeTool { return "Using \(t.replacingOccurrences(of: ".", with: " · "))" }
            return state.transcript.isEmpty ? "Working on it…" : state.transcript
        case .speaking:
            return state.reply.isEmpty ? state.transcript : state.reply
        case .error:
            return state.lastError ?? "Something went wrong"
        default:
            return "Click to type · say \u{201C}Hey MIRA\u{201D}"
        }
    }
}

private struct IconButton: View {
    let system: String
    let action: () -> Void
    @State private var hovered = false

    var body: some View {
        Button(action: action) {
            Image(systemName: system)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(hovered ? Palette.text : Palette.muted)
                .frame(width: 30, height: 30)
                .background(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(Color.white.opacity(hovered ? 0.09 : 0.04))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .strokeBorder(Color.white.opacity(hovered ? 0.14 : 0.08), lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .onHover { hovered = $0 }
    }
}
