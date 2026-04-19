import SwiftUI

/// Thin vertical meter to the right of the pill text. Only animates while
/// listening — in other states it stays dim to avoid drawing the eye.
struct Waveform: View {
    let level: Double
    let state: AppState.VoiceState

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .bottom) {
                RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                    .fill(Color.white.opacity(0.08))

                RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [Palette.accentA, Palette.accentB],
                            startPoint: .bottom,
                            endPoint: .top
                        )
                    )
                    .frame(height: geo.size.height * visibleLevel)
                    .animation(.easeOut(duration: 0.08), value: visibleLevel)
            }
        }
    }

    private var visibleLevel: CGFloat {
        guard state == .listening || state == .speaking else { return 0.04 }
        return CGFloat(max(0.04, min(1, level)))
    }
}
