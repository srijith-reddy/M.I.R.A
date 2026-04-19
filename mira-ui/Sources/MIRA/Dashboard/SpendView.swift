import SwiftUI

struct SpendView: View {
    @EnvironmentObject private var client: DashboardClient

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("LLM Spend")
                        .font(Typography.dashH1)
                        .foregroundStyle(Palette.text)
                    Text("last 24 hours · per model")
                        .font(Typography.cardMeta)
                        .foregroundStyle(Palette.muted)
                        .textCase(.uppercase)
                        .tracking(1)
                }
                Spacer()
                Text(fmtCost(client.spend.reduce(0) { $0 + $1.cost_usd }))
                    .font(Typography.dashH1)
                    .foregroundStyle(Palette.accentC)
            }
            .padding(24)

            Divider().background(Palette.hairline)

            if client.spend.isEmpty {
                Spacer()
                Text("No LLM calls recorded in the last 24 hours.")
                    .font(Typography.cardSub)
                    .foregroundStyle(Palette.muted)
                    .frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    VStack(spacing: 1) {
                        columnHeader
                        ForEach(client.spend) { row in
                            SpendRowView(row: row, maxCost: maxCost)
                        }
                    }
                    .padding(.horizontal, 24)
                    .padding(.vertical, 8)
                }
            }
        }
    }

    private var columnHeader: some View {
        HStack {
            Text("MODEL")
                .frame(width: 220, alignment: .leading)
            Text("PROVIDER")
                .frame(width: 100, alignment: .leading)
            Text("CALLS")
                .frame(width: 70, alignment: .trailing)
            Text("PROMPT")
                .frame(width: 90, alignment: .trailing)
            Text("COMPLETION")
                .frame(width: 110, alignment: .trailing)
            Spacer()
            Text("COST")
                .frame(width: 90, alignment: .trailing)
        }
        .font(Typography.cardMeta)
        .foregroundStyle(Palette.muted)
        .tracking(1)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private var maxCost: Double {
        client.spend.map(\.cost_usd).max() ?? 1
    }
}

struct SpendRowView: View {
    let row: SpendRow
    let maxCost: Double

    var body: some View {
        ZStack(alignment: .leading) {
            // Bar background — visualizes relative cost.
            GeometryReader { geo in
                RoundedRectangle(cornerRadius: 8)
                    .fill(
                        LinearGradient(colors: [Palette.accentC.opacity(0.14), .clear],
                                       startPoint: .leading, endPoint: .trailing)
                    )
                    .frame(width: geo.size.width * CGFloat(row.cost_usd / max(0.00001, maxCost)))
            }

            HStack {
                Text(row.model)
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                    .foregroundStyle(Palette.text)
                    .frame(width: 220, alignment: .leading)
                Text(row.provider)
                    .font(Typography.cardMeta)
                    .foregroundStyle(Palette.accentA)
                    .textCase(.uppercase)
                    .frame(width: 100, alignment: .leading)
                Text("\(row.calls)")
                    .font(Typography.dashCell)
                    .frame(width: 70, alignment: .trailing)
                    .foregroundStyle(Palette.muted)
                Text("\(row.prompt_tokens)")
                    .font(.system(size: 12, design: .monospaced))
                    .frame(width: 90, alignment: .trailing)
                    .foregroundStyle(Palette.muted)
                Text("\(row.completion_tokens)")
                    .font(.system(size: 12, design: .monospaced))
                    .frame(width: 110, alignment: .trailing)
                    .foregroundStyle(Palette.muted)
                Spacer()
                Text(fmtCost(row.cost_usd))
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Palette.accentC)
                    .frame(width: 90, alignment: .trailing)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
    }
}
