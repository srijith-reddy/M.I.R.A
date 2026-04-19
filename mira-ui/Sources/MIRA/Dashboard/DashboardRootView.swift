import SwiftUI

/// Dashboard shell: left sidebar (tabs), right content area. Mirrors the
/// data the Flask dashboard surfaces — stats, recent turns, LLM spend,
/// per-turn trace — but laid out natively so it doesn't feel like a web
/// page stuck in a window.
struct DashboardRootView: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var client: DashboardClient

    enum Tab: String, CaseIterable, Identifiable {
        case overview, turns, spend, trace
        var id: String { rawValue }
        var title: String {
            switch self {
            case .overview: return "Overview"
            case .turns:    return "Turns"
            case .spend:    return "LLM Spend"
            case .trace:    return "Trace"
            }
        }
        var icon: String {
            switch self {
            case .overview: return "square.grid.2x2"
            case .turns:    return "bubble.left.and.bubble.right"
            case .spend:    return "dollarsign.circle"
            case .trace:    return "list.bullet.rectangle"
            }
        }
    }

    @State private var selection: Tab = .overview

    var body: some View {
        HSplitView {
            sidebar
                .frame(minWidth: 200, idealWidth: 220, maxWidth: 260)

            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Color(red: 0.06, green: 0.07, blue: 0.09))
        .preferredColorScheme(.dark)
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Orb(state: state.voiceState, level: state.audioLevel)
                    .frame(width: 28, height: 28)
                VStack(alignment: .leading, spacing: 0) {
                    Text("MIRA")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(Palette.text)
                    Text(state.connected ? "connected" : "offline")
                        .font(Typography.cardMeta)
                        .foregroundStyle(state.connected ? Palette.accentB : Palette.danger)
                        .textCase(.uppercase)
                        .tracking(1)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 18)

            Divider().background(Palette.hairline)

            ScrollView {
                VStack(spacing: 2) {
                    ForEach(Tab.allCases) { tab in
                        SidebarButton(
                            tab: tab,
                            selected: selection == tab,
                            action: { selection = tab }
                        )
                    }
                }
                .padding(.horizontal, 8)
                .padding(.top, 8)
            }

            Spacer()

            Divider().background(Palette.hairline)
            Button {
                Task { await client.refresh() }
            } label: {
                HStack {
                    Image(systemName: "arrow.clockwise")
                    Text("Refresh")
                    Spacer()
                    if client.loading {
                        ProgressView().controlSize(.small)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(Palette.muted)
        }
        .background(Color(red: 0.05, green: 0.06, blue: 0.08))
    }

    @ViewBuilder
    private var content: some View {
        switch selection {
        case .overview: OverviewView()
        case .turns:    TurnsView()
        case .spend:    SpendView()
        case .trace:    TraceView()
        }
    }
}

private struct SidebarButton: View {
    let tab: DashboardRootView.Tab
    let selected: Bool
    let action: () -> Void
    @State private var hovered = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: tab.icon)
                    .font(.system(size: 13, weight: .medium))
                    .frame(width: 18)
                Text(tab.title)
                    .font(.system(size: 13, weight: selected ? .semibold : .regular))
                Spacer()
            }
            .foregroundStyle(selected ? Palette.text : Palette.muted)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(selected ? Palette.accentA.opacity(0.22)
                          : hovered ? Color.white.opacity(0.05) : .clear)
            )
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .onHover { hovered = $0 }
    }
}
