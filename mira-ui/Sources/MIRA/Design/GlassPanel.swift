import SwiftUI

/// Reusable glass container. Uses `.ultraThinMaterial` which is the real
/// AppKit vibrancy effect — gives you native saturation + blur that
/// respects the wallpaper behind the window. CSS `backdrop-filter` only
/// blurs the app's own content; material blurs through transparent
/// NSPanels too, which is why this already looks different from the
/// WKWebView version.
struct GlassPanel<Content: View>: View {
    var radius: CGFloat
    @ViewBuilder var content: () -> Content

    init(radius: CGFloat = Metrics.pillRadius, @ViewBuilder content: @escaping () -> Content) {
        self.radius = radius
        self.content = content
    }

    var body: some View {
        content()
            .background {
                ZStack {
                    RoundedRectangle(cornerRadius: radius, style: .continuous)
                        .fill(.ultraThinMaterial)
                    RoundedRectangle(cornerRadius: radius, style: .continuous)
                        .fill(Color.black.opacity(0.18))
                        .blendMode(.plusDarker)
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(Palette.pillBorder, lineWidth: 0.5)
            }
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(Color.white.opacity(0.06), lineWidth: 1)
                    .blendMode(.plusLighter)
                    .mask(
                        LinearGradient(
                            colors: [.white, .clear],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
            }
            .shadow(color: .black.opacity(0.45), radius: 22, x: 0, y: 10)
            .shadow(color: .black.opacity(0.3),  radius: 4,  x: 0, y: 2)
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
    }
}
