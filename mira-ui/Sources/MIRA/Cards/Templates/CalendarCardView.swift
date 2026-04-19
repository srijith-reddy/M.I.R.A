import SwiftUI

/// Upcoming-events template — timeline-style rows with a left-rail
/// accent that reads as a day schedule. Uses `start_time` / `end_time`
/// if provided; falls back to `trailing` for loosely typed payloads.
struct CalendarCardView: View {
    let rows: [CardRow]

    var body: some View {
        RowsScroll {
            ForEach(rows) { row in
                CardRowContainer(url: row.url) {
                    timeColumn(row)

                    Rectangle()
                        .fill(
                            LinearGradient(colors: [Palette.accentB, Palette.accentA],
                                           startPoint: .top, endPoint: .bottom)
                        )
                        .frame(width: 3, height: 36)
                        .clipShape(Capsule())

                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.title)
                            .font(Typography.cardRow)
                            .foregroundStyle(Palette.text)
                            .lineLimit(1)
                        if let where_ = row.subtitle {
                            HStack(spacing: 4) {
                                Image(systemName: "mappin.circle")
                                    .font(.system(size: 10))
                                Text(where_)
                                    .font(Typography.cardSub)
                            }
                            .foregroundStyle(Palette.muted)
                            .lineLimit(1)
                        }
                    }

                    Spacer(minLength: 0)

                    if let attendees = row.meta {
                        Text(attendees)
                            .font(Typography.cardMeta)
                            .foregroundStyle(Palette.dim)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func timeColumn(_ row: CardRow) -> some View {
        VStack(alignment: .trailing, spacing: 1) {
            Text(row.startTime ?? row.trailing ?? "—")
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(Palette.text)
            Text(row.endTime ?? "")
                .font(.system(size: 10, weight: .regular, design: .monospaced))
                .foregroundStyle(Palette.dim)
        }
        .frame(width: 52, alignment: .trailing)
    }
}
