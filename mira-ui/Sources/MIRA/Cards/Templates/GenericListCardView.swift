import SwiftUI

/// Fallback template when no agent kind matches. Flat rows with title +
/// subtitle + trailing. Kept intentionally minimal so this looks like
/// "a generic result list" rather than "a broken specialized card".
struct GenericListCardView: View {
    let rows: [CardRow]

    var body: some View {
        RowsScroll {
            ForEach(rows) { row in
                CardRowContainer(url: row.url) {
                    if row.thumbnail != nil {
                        Thumbnail(urlString: row.thumbnail, size: 40)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.title)
                            .font(Typography.cardRow)
                            .foregroundStyle(Palette.text)
                            .lineLimit(1)
                        if let sub = row.subtitle {
                            Text(sub)
                                .font(Typography.cardSub)
                                .foregroundStyle(Palette.muted)
                                .lineLimit(1)
                        }
                        if let meta = row.meta {
                            Text(meta)
                                .font(Typography.cardMeta)
                                .foregroundStyle(Palette.dim)
                                .textCase(.uppercase)
                                .tracking(1)
                        }
                    }
                    Spacer(minLength: 6)
                    if let trailing = row.trailing {
                        Text(trailing)
                            .font(Typography.cardValue)
                            .foregroundStyle(Palette.accentB)
                    }
                }
            }
        }
    }
}
