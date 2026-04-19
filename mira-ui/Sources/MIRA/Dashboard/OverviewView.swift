import SwiftUI

/// Top-level at-a-glance view. Same four stats as the Flask dashboard
/// (turns, errors, cost, latency) but laid out as first-class cards, plus
/// the 10 most recent turns beneath.
struct OverviewView: View {
    @EnvironmentObject private var client: DashboardClient

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                statsGrid
                Text("Recent activity")
                    .font(Typography.dashH2)
                    .foregroundStyle(Palette.text)
                    .padding(.top, 6)
                turnsPreview
            }
            .padding(24)
        }
    }

    private var header: some View {
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Overview")
                    .font(Typography.dashH1)
                    .foregroundStyle(Palette.text)
                Text("last 24 hours")
                    .font(Typography.cardMeta)
                    .foregroundStyle(Palette.muted)
                    .textCase(.uppercase)
                    .tracking(1)
            }
            Spacer()
        }
    }

    private var statsGrid: some View {
        LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 160, maximum: 260), spacing: 14)],
            spacing: 14
        ) {
            StatCard(title: "Turns",        value: "\(client.stats.turns_24h)",                         accent: Palette.accentA)
            StatCard(title: "Errors",       value: "\(client.stats.errors_24h)",
                     accent: client.stats.errors_24h > 0 ? Palette.danger : Palette.muted)
            StatCard(title: "Cost (24h)",   value: fmtCost(client.stats.cost_usd_24h),                  accent: Palette.accentC)
            StatCard(title: "p50 latency",  value: fmtDur(client.stats.p50_latency_ms),                 accent: Palette.accentB)
            StatCard(title: "p95 latency",  value: fmtDur(client.stats.p95_latency_ms),                 accent: Palette.accentB)
        }
    }

    private var turnsPreview: some View {
        VStack(spacing: 1) {
            ForEach(client.turns.prefix(10)) { t in
                TurnRowView(turn: t)
                    .onTapGesture {
                        Task { await client.selectTurn(t.turn_id) }
                    }
            }
            if client.turns.isEmpty {
                Text("No turns yet — wake MIRA with \u{201C}Hey MIRA\u{201D} to see activity.")
                    .font(Typography.cardSub)
                    .foregroundStyle(Palette.muted)
                    .frame(maxWidth: .infinity)
                    .padding(24)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.white.opacity(0.03))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1)
        )
    }
}

struct StatCard: View {
    let title: String
    let value: String
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(Typography.cardMeta)
                .foregroundStyle(Palette.muted)
                .textCase(.uppercase)
                .tracking(1)
            Text(value)
                .font(Typography.dashStat)
                .foregroundStyle(Palette.text)
            Rectangle()
                .fill(
                    LinearGradient(colors: [accent.opacity(0.8), accent.opacity(0.1)],
                                   startPoint: .leading, endPoint: .trailing)
                )
                .frame(height: 2)
                .padding(.top, 4)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color.white.opacity(0.04))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1)
        )
    }
}

// MARK: - Formatters

func fmtDur(_ ms: Double?) -> String {
    guard let ms else { return "—" }
    if ms >= 1000 { return String(format: "%.2fs", ms / 1000) }
    return "\(Int(ms.rounded()))ms"
}

func fmtCost(_ c: Double?) -> String {
    guard let c, c > 0 else { return "—" }
    return String(format: "$%.4f", c)
}

func fmtTime(_ ts: Double?) -> String {
    guard let ts, ts > 0 else { return "—" }
    let date = Date(timeIntervalSince1970: ts)
    let f = DateFormatter()
    f.dateStyle = .none
    f.timeStyle = .medium
    return f.string(from: date)
}
