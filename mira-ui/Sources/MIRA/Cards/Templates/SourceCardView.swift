import SwiftUI

/// Research template — rows are web sources cited in the spoken answer.
/// Shows a small favicon-like thumb + title + domain badge + snippet.
/// Click opens the source in the default browser, so the user can verify.
struct SourceCardView: View {
    let rows: [CardRow]

    var body: some View {
        RowsScroll {
            ForEach(Array(rows.enumerated()), id: \.offset) { idx, row in
                CardRowContainer(url: row.url) {
                    // Logos (sports teams, CDN-hosted images) override the
                    // numbered pill. Citations without a reliable image
                    // keep the numbered pill — half of favicons fail and
                    // a broken image looks worse than a clean number.
                    if let thumb = row.thumbnail, !thumb.isEmpty {
                        Thumbnail(urlString: thumb, size: 28)
                    } else {
                        Text("\(idx + 1)")
                            .font(.system(size: 12, weight: .bold, design: .rounded))
                            .frame(width: 24, height: 24)
                            .foregroundStyle(Palette.text)
                            .background(
                                Circle().fill(Palette.accentB.opacity(0.22))
                            )
                            .overlay(
                                Circle().strokeBorder(Palette.accentB.opacity(0.4), lineWidth: 1)
                            )
                    }

                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.title)
                            .font(Typography.cardRow)
                            .foregroundStyle(Palette.text)
                            .lineLimit(1)
                        if let snippet = row.subtitle {
                            Text(snippet)
                                .font(Typography.cardSub)
                                .foregroundStyle(Palette.muted)
                                .lineLimit(2)
                        }
                        if let domain = row.meta ?? row.badge {
                            Text(domain)
                                .font(Typography.cardMeta)
                                .foregroundStyle(Palette.accentB)
                                .textCase(.lowercase)
                        }
                    }

                    Spacer(minLength: 6)

                    // Trailing value for sources that carry one — ESPN
                    // score cards put the current points here, for
                    // example. Plain citations skip this and show the
                    // external-link glyph alone.
                    if let trailing = row.trailing, !trailing.isEmpty {
                        Text(trailing)
                            .font(.system(size: 14, weight: .semibold, design: .rounded))
                            .foregroundStyle(Palette.text)
                            .padding(.trailing, 4)
                    }

                    Image(systemName: "arrow.up.right.square")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Palette.dim)
                }
            }
        }
    }
}
