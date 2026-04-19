import SwiftUI

/// Root of the card panel. Dispatches to the right template based on
/// `CardKind`. Keeps every template file focused on one shape — the
/// dispatcher is the only place that knows about every renderer.
struct CardHostView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        Group {
            if let card = state.currentCard {
                GlassPanel(radius: Metrics.cardRadius) {
                    VStack(spacing: 0) {
                        header(card: card)
                        Divider()
                            .background(Palette.hairline)
                            .padding(.horizontal, 14)

                        dispatch(card: card)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 8)

                        if let footer = card.footer {
                            Text(footer)
                                .font(Typography.cardMeta)
                                .foregroundStyle(Palette.muted)
                                .textCase(.uppercase)
                                .tracking(1)
                                .padding(.horizontal, 14)
                                .padding(.bottom, 10)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .frame(width: Metrics.cardWidth)
            } else {
                Color.clear
            }
        }
    }

    @ViewBuilder
    private func header(card: CardPayload) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(card.title)
                    .font(Typography.cardTitle)
                    .foregroundStyle(Palette.text)
                    .lineLimit(1)
            }
            Spacer()
            if let subtitle = card.subtitle {
                Text(subtitle)
                    .font(Typography.cardSub)
                    .foregroundStyle(Palette.muted)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 12)
        .padding(.bottom, 10)
    }

    @ViewBuilder
    private func dispatch(card: CardPayload) -> some View {
        switch card.kind {
        case .product:  ProductCardView(rows: card.rows)
        case .source:   SourceCardView(rows: card.rows)
        case .email:    EmailCardView(rows: card.rows)
        case .calendar: CalendarCardView(rows: card.rows)
        case .reminder: ReminderCardView(rows: card.rows)
        case .action:   ActionCardView(rows: card.rows)
        case .list:     GenericListCardView(rows: card.rows)
        }
    }

    private func agentColor(_ kind: CardKind) -> Color {
        switch kind {
        case .product:  return Palette.accentC
        case .source:   return Palette.accentB
        case .email:    return Palette.accentA
        case .calendar: return Palette.accentB
        case .reminder: return Palette.warm
        case .action:   return Palette.accentA
        case .list:     return Palette.muted
        }
    }
}
