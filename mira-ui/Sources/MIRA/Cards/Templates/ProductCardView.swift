import SwiftUI

/// Commerce template — product with thumbnail, title, rating, and a big
/// accented price on the right. Optimized for "best X under $Y" replies:
/// scan the rightmost column for prices, skim titles, click to open.
struct ProductCardView: View {
    let rows: [CardRow]

    var body: some View {
        RowsScroll {
            ForEach(rows) { row in
                CardRowContainer(url: row.url) {
                    Thumbnail(urlString: row.thumbnail, size: 48)

                    VStack(alignment: .leading, spacing: 3) {
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

                        HStack(spacing: 8) {
                            if let r = row.rating {
                                StarRating(rating: r)
                            }
                            if let badge = row.badge ?? row.meta {
                                Text(badge)
                                    .font(Typography.cardMeta)
                                    .foregroundStyle(Palette.dim)
                                    .textCase(.uppercase)
                                    .tracking(1)
                            }
                        }
                    }

                    Spacer(minLength: 6)

                    if let price = row.trailing {
                        Text(price)
                            .font(Typography.cardValue)
                            .foregroundStyle(Palette.accentB)
                    }
                }
            }
        }
    }
}
