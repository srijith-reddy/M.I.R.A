import SwiftUI

enum Palette {
    static let accentA = Color(red: 0.486, green: 0.361, blue: 1.0)   // #7c5cff
    static let accentB = Color(red: 0.137, green: 0.820, blue: 1.0)   // #23d1ff
    static let accentC = Color(red: 1.0,   green: 0.361, blue: 0.784) // #ff5cc8
    static let warm    = Color(red: 1.0,   green: 0.620, blue: 0.360) // #ff9e5c
    static let danger  = Color(red: 1.0,   green: 0.361, blue: 0.478) // #ff5c7a

    static let pillBG      = Color.black.opacity(0.45)
    static let pillBorder  = Color.white.opacity(0.10)
    static let text        = Color.white.opacity(0.96)
    static let muted       = Color.white.opacity(0.58)
    static let dim         = Color.white.opacity(0.42)
    static let hairline    = Color.white.opacity(0.06)
    static let rowBG       = Color.white.opacity(0.025)
    static let rowHover    = Color.white.opacity(0.08)
}

enum Typography {
    static let orbLabel   = Font.system(size: 11, weight: .semibold, design: .default)
    static let pillBody   = Font.system(size: 14, weight: .medium, design: .default)
    static let cardTitle  = Font.system(size: 13, weight: .semibold, design: .default)
    static let cardRow    = Font.system(size: 13, weight: .medium, design: .default)
    static let cardSub    = Font.system(size: 11, weight: .regular, design: .default)
    static let cardMeta   = Font.system(size: 10, weight: .medium, design: .default)
    static let cardValue  = Font.system(size: 13, weight: .semibold, design: .monospaced)
    static let dashH1     = Font.system(size: 20, weight: .bold, design: .default)
    static let dashH2     = Font.system(size: 14, weight: .semibold, design: .default)
    static let dashCell   = Font.system(size: 12, weight: .regular, design: .default)
    static let dashStat   = Font.system(size: 22, weight: .bold, design: .rounded)
}

enum Metrics {
    static let pillWidth: CGFloat       = 560
    static let pillHeight: CGFloat      = 56
    static let pillRadius: CGFloat      = 28
    static let cardWidth: CGFloat       = 520
    static let cardRadius: CGFloat      = 18
    static let hudTopMargin: CGFloat    = 8
    static let cardGap: CGFloat         = 10
}
