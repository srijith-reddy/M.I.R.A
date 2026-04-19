import SwiftUI

struct TurnsView: View {
    @EnvironmentObject private var client: DashboardClient

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Turns")
                        .font(Typography.dashH1)
                        .foregroundStyle(Palette.text)
                    Text("\(client.turns.count) recent")
                        .font(Typography.cardMeta)
                        .foregroundStyle(Palette.muted)
                        .textCase(.uppercase)
                        .tracking(1)
                }
                Spacer()
            }
            .padding(24)

            Divider().background(Palette.hairline)

            ScrollView {
                LazyVStack(spacing: 1) {
                    ForEach(client.turns) { t in
                        TurnRowView(turn: t)
                            .onTapGesture {
                                Task { await client.selectTurn(t.turn_id) }
                            }
                    }
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 8)
            }
        }
    }
}

struct TurnRowView: View {
    let turn: TurnRow
    @State private var hovered = false

    var body: some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 2) {
                Text(fmtTime(turn.ended_at ?? turn.started_at))
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(Palette.muted)
                StatusPill(status: turn.status, via: turn.via)
            }
            .frame(width: 100, alignment: .leading)

            VStack(alignment: .leading, spacing: 3) {
                if let t = turn.transcript, !t.isEmpty {
                    Text(t)
                        .font(Typography.cardRow)
                        .foregroundStyle(Palette.text)
                        .lineLimit(1)
                }
                if let r = turn.reply, !r.isEmpty {
                    Text(r)
                        .font(Typography.cardSub)
                        .foregroundStyle(Palette.muted)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            VStack(alignment: .trailing, spacing: 2) {
                Text(fmtDur(turn.latency_ms))
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Palette.accentB)
                Text(fmtCost(turn.cost_usd))
                    .font(.system(size: 10, weight: .regular, design: .monospaced))
                    .foregroundStyle(Palette.dim)
            }
            .frame(width: 90, alignment: .trailing)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(hovered ? Color.white.opacity(0.05) : .clear)
        )
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .onHover { hovered = $0 }
    }
}

struct StatusPill: View {
    let status: String?
    let via: String?

    var body: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(color)
                .frame(width: 5, height: 5)
            Text("\(status ?? "—")\(via.map { " · \($0)" } ?? "")")
                .font(Typography.cardMeta)
                .foregroundStyle(color.opacity(0.9))
                .textCase(.uppercase)
                .tracking(1)
        }
    }

    private var color: Color {
        switch (status ?? "").lowercased() {
        case "ok", "done": return Palette.accentB
        case "error":      return Palette.danger
        default:           return Palette.muted
        }
    }
}
