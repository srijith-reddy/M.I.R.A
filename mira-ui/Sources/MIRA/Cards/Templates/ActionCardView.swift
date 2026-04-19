import SwiftUI

/// Browser / device template — a one-shot action summary. First row is
/// emphasized as the primary action; additional rows are subordinate
/// steps or affected items.
struct ActionCardView: View {
    let rows: [CardRow]

    var body: some View {
        VStack(spacing: 6) {
            if let first = rows.first {
                primaryRow(first)
            }
            if rows.count > 1 {
                ForEach(rows.dropFirst()) { row in
                    subRow(row)
                }
            }
        }
    }

    @ViewBuilder
    private func primaryRow(_ row: CardRow) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "bolt.horizontal.fill")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Palette.accentA)
                .frame(width: 40, height: 40)
                .background(Circle().fill(Palette.accentA.opacity(0.18)))

            VStack(alignment: .leading, spacing: 2) {
                Text(row.title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Palette.text)
                if let sub = row.subtitle {
                    Text(sub)
                        .font(Typography.cardSub)
                        .foregroundStyle(Palette.muted)
                }
            }
            Spacer()
            if let trailing = row.trailing {
                Text(trailing)
                    .font(Typography.cardValue)
                    .foregroundStyle(Palette.accentB)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Palette.accentA.opacity(0.08))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Palette.accentA.opacity(0.25), lineWidth: 1)
        )
    }

    @ViewBuilder
    private func subRow(_ row: CardRow) -> some View {
        CardRowContainer(url: row.url) {
            Image(systemName: "chevron.right")
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(Palette.dim)
                .frame(width: 20)
            Text(row.title)
                .font(Typography.cardRow)
                .foregroundStyle(Palette.text)
                .lineLimit(1)
            Spacer()
            if let trailing = row.trailing {
                Text(trailing)
                    .font(Typography.cardMeta)
                    .foregroundStyle(Palette.muted)
            }
        }
    }
}
