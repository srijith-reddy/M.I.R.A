import SwiftUI

/// Communication template — inbox items. `title` = sender, `subtitle` =
/// subject, `meta` = short timestamp like "2h ago". Trailing slot renders
/// an unread-dot treatment when meta contains the word "unread".
struct EmailCardView: View {
    let rows: [CardRow]

    var body: some View {
        RowsScroll {
            ForEach(rows) { row in
                CardRowContainer(url: row.url) {
                    ZStack {
                        Circle()
                            .fill(Palette.accentA.opacity(0.22))
                            .frame(width: 32, height: 32)
                        Text(initials(row.title))
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(Palette.text)
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(row.title)
                                .font(Typography.cardRow)
                                .foregroundStyle(Palette.text)
                                .lineLimit(1)
                            if unread(row) {
                                Circle()
                                    .fill(Palette.accentB)
                                    .frame(width: 6, height: 6)
                            }
                        }
                        if let subject = row.subtitle {
                            Text(subject)
                                .font(Typography.cardSub)
                                .foregroundStyle(Palette.muted)
                                .lineLimit(1)
                        }
                    }

                    Spacer(minLength: 6)

                    if let ts = row.trailing ?? row.meta {
                        Text(ts)
                            .font(Typography.cardMeta)
                            .foregroundStyle(Palette.dim)
                            .textCase(.uppercase)
                            .tracking(1)
                    }
                }
            }
        }
    }

    private func initials(_ name: String) -> String {
        let parts = name.split(separator: " ").prefix(2)
        return parts.compactMap { $0.first.map(String.init) }.joined().uppercased()
    }

    private func unread(_ row: CardRow) -> Bool {
        (row.meta ?? "").lowercased().contains("unread")
    }
}
