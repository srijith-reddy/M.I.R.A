import SwiftUI

// Design tokens. One file so tweaks stay consistent — opening this and
// changing a single color should move the whole HUD, not just one view.
// Dark-mode only by design; the HUD lives on top of the user's desktop
// against vibrancy backing, so a light palette fights the blur.
//
// Palette is a three-stop conic: violet → cyan → magenta. That trio reads
// as "intelligence" (cool cyan = clarity, violet = compute, magenta =
// expression) without landing on any one brand's color. The orb uses the
// full conic; UI chrome uses the cyan midpoint as the accent so we never
// double up with the orb visually.

enum Theme {
    enum Color {
        /// Primary accent — the cool-cyan midpoint of the conic. Used for
        /// state labels, connection dots, focus rings.
        static let accent = SwiftUI.Color(red: 0.35, green: 0.82, blue: 1.00)
        /// Secondary warm accent for reminders, memory recalls.
        static let warm = SwiftUI.Color(red: 1.00, green: 0.78, blue: 0.45)
        /// Danger — error lines, destructive confirmations.
        static let danger = SwiftUI.Color(red: 1.00, green: 0.36, blue: 0.48)

        // Orb conic stops. Stored here (not hard-coded in Orb.swift) so
        // theme tweaks propagate.
        static let orbA = SwiftUI.Color(red: 0.49, green: 0.36, blue: 1.00)  // violet
        static let orbB = SwiftUI.Color(red: 0.14, green: 0.82, blue: 1.00)  // cyan
        static let orbC = SwiftUI.Color(red: 1.00, green: 0.36, blue: 0.78)  // magenta

        static let textPrimary   = SwiftUI.Color.white.opacity(0.96)
        static let textSecondary = SwiftUI.Color.white.opacity(0.70)
        static let textTertiary  = SwiftUI.Color.white.opacity(0.44)

        static let hairline    = SwiftUI.Color.white.opacity(0.08)
        static let cardFill    = SwiftUI.Color.white.opacity(0.06)
        static let cardStroke  = SwiftUI.Color.white.opacity(0.12)

        /// Subtle inner highlight along the top edge of the panel —
        /// mimics the way real glass catches light.
        static let glassHighlight = SwiftUI.Color.white.opacity(0.14)
    }

    enum Metric {
        static let panelWidth: CGFloat = 440
        static let panelMinHeight: CGFloat = 160
        static let panelMaxHeight: CGFloat = 560
        static let cornerRadius: CGFloat = 26
        static let cardRadius: CGFloat = 14
        static let padH: CGFloat = 20
        static let padV: CGFloat = 18
        /// Bumped from 72 → 96. The orb is the focal point; give it room.
        static let orbSize: CGFloat = 96
    }

    enum Font {
        static let state: SwiftUI.Font = .system(size: 11, weight: .bold, design: .rounded)
        static let transcript: SwiftUI.Font = .system(size: 17, weight: .regular, design: .default)
        static let reply: SwiftUI.Font = .system(size: 15, weight: .regular, design: .default)
        static let cardTitle: SwiftUI.Font = .system(size: 11, weight: .bold, design: .rounded)
        static let cardBody: SwiftUI.Font = .system(size: 13, weight: .regular)
        static let footnote: SwiftUI.Font = .system(size: 11, weight: .medium, design: .rounded)
    }

    /// Gradient used for the panel's inner stroke and orb halos. Exposed
    /// so chrome and orb stay chromatically linked.
    static let brandGradient = LinearGradient(
        colors: [Color.orbA, Color.orbB, Color.orbC],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    static let brandConic = AngularGradient(
        gradient: Gradient(colors: [Color.orbA, Color.orbB, Color.orbC, Color.orbA]),
        center: .center
    )
}
