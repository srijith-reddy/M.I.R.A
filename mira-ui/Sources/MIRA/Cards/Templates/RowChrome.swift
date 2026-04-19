import SwiftUI

/// Pieces shared across card templates. Every template composes these
/// rather than duplicating padding / hover logic, so the card rhythm is
/// identical whether you're looking at a product card or an email card.

struct CardRowContainer<Content: View>: View {
    let url: String?
    @ViewBuilder var content: () -> Content
    @State private var hovered = false
    @EnvironmentObject private var state: AppState

    var body: some View {
        HStack(spacing: 10, content: content)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(hovered ? Palette.rowHover : Palette.rowBG)
            )
            .contentShape(RoundedRectangle(cornerRadius: 10))
            .onHover { hovered = $0 }
            .onTapGesture {
                if let u = url { state.openURL(u) }
            }
            .animation(.easeOut(duration: 0.12), value: hovered)
    }
}

/// 44x44 remote thumbnail with a graceful fallback. Uses AsyncImage —
/// falls back to a tinted placeholder if the URL fails so a dead CDN
/// doesn't leave a broken-image glyph in the card.
struct Thumbnail: View {
    let urlString: String?
    var size: CGFloat = 44

    var body: some View {
        Group {
            if let s = urlString, let url = URL(string: s) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .empty:
                        placeholder
                    case .success(let image):
                        image.resizable().scaledToFill()
                    case .failure:
                        placeholder
                    @unknown default:
                        placeholder
                    }
                }
            } else {
                placeholder
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
        )
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(
                colors: [Palette.accentA.opacity(0.25), Palette.accentB.opacity(0.15)],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )
            Image(systemName: "photo")
                .font(.system(size: size * 0.32))
                .foregroundStyle(Palette.muted)
        }
    }
}

/// Compact star rating (0–5). We round to the nearest half and draw
/// filled / half / empty glyphs. Kept to 5 glyphs max so product cards
/// don't eat horizontal space.
struct StarRating: View {
    let rating: Double

    var body: some View {
        HStack(spacing: 1) {
            ForEach(0..<5) { i in
                let f = Double(i)
                let filled = rating >= f + 1
                let half = !filled && rating >= f + 0.5
                Image(systemName: filled ? "star.fill" : (half ? "star.leadinghalf.filled" : "star"))
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(filled || half ? Palette.warm : Palette.dim)
            }
            Text(String(format: "%.1f", rating))
                .font(Typography.cardMeta)
                .foregroundStyle(Palette.muted)
                .padding(.leading, 3)
        }
    }
}

struct RowsScroll<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        ScrollView {
            VStack(spacing: 2) {
                content()
            }
        }
        .frame(maxHeight: 340)
    }
}
