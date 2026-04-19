import SwiftUI

/// Reminders template — one checkbox-style row per reminder. Trailing
/// slot shows the fire time. Click doesn't complete the reminder (that's
/// the daemon's job) — it just opens the reminder URL if set.
struct ReminderCardView: View {
    let rows: [CardRow]

    var body: some View {
        RowsScroll {
            ForEach(rows) { row in
                CardRowContainer(url: row.url) {
                    Image(systemName: "bell.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Palette.warm)
                        .frame(width: 30, height: 30)
                        .background(
                            Circle().fill(Palette.warm.opacity(0.18))
                        )

                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.title)
                            .font(Typography.cardRow)
                            .foregroundStyle(Palette.text)
                            .lineLimit(2)
                        if let sub = row.subtitle {
                            Text(sub)
                                .font(Typography.cardSub)
                                .foregroundStyle(Palette.muted)
                                .lineLimit(1)
                        }
                    }

                    Spacer(minLength: 6)

                    if let when = row.trailing ?? row.meta {
                        Text(when)
                            .font(Typography.cardMeta)
                            .foregroundStyle(Palette.warm.opacity(0.9))
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(
                                Capsule().fill(Palette.warm.opacity(0.15))
                            )
                    }
                }
            }
        }
    }
}
