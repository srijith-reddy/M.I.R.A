import SwiftUI

/// Conic-gradient orb with per-state animation. Drawn with Canvas so we
/// get GPU compositing with no layer-hackery and no image assets.
struct Orb: View {
    let state: AppState.VoiceState
    let level: Double

    @State private var phase: Double = 0
    @State private var pulse: Double = 1

    var body: some View {
        Canvas { ctx, size in
            let rect = CGRect(origin: .zero, size: size).insetBy(dx: 0, dy: 0)

            // Background conic gradient — the orb body.
            let gradient = Gradient(colors: gradientColors)
            let center = CGPoint(x: size.width / 2, y: size.height / 2)

            let path = Path(ellipseIn: rect)
            ctx.fill(
                path,
                with: .conicGradient(gradient, center: center, angle: .degrees(phase))
            )

            // Inner highlight (glass pearl).
            let inset = rect.insetBy(dx: rect.width * 0.18, dy: rect.height * 0.18)
            let highlight = Gradient(colors: [
                Color.white.opacity(0.55),
                Color.white.opacity(0.0)
            ])
            ctx.fill(
                Path(ellipseIn: inset),
                with: .radialGradient(
                    highlight,
                    center: CGPoint(x: inset.midX - inset.width * 0.25,
                                    y: inset.midY - inset.height * 0.25),
                    startRadius: 0,
                    endRadius: inset.width
                )
            )
        }
        .scaleEffect(pulse)
        .shadow(color: glowColor, radius: glowRadius)
        .onAppear { startAnimating() }
        .onChange(of: state) { _, _ in startAnimating() }
    }

    private func startAnimating() {
        switch state {
        case .idle, .setup:
            pulse = 1
            withAnimation(.linear(duration: 8).repeatForever(autoreverses: false)) {
                phase = 360
            }
        case .listening:
            withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                pulse = 1.0 + 0.12 * max(0.3, level)
            }
        case .thinking:
            withAnimation(.linear(duration: 1.1).repeatForever(autoreverses: false)) {
                phase = 720
            }
            withAnimation(.easeInOut(duration: 0.55).repeatForever(autoreverses: true)) {
                pulse = 0.96
            }
        case .speaking:
            withAnimation(.easeInOut(duration: 0.45).repeatForever(autoreverses: true)) {
                pulse = 1.08
            }
        case .error:
            pulse = 1
        }
    }

    private var gradientColors: [Color] {
        switch state {
        case .setup:
            return [Palette.warm, Color(red: 1, green: 0.83, blue: 0.36), Palette.warm]
        case .error:
            return [Palette.danger, Palette.danger.opacity(0.4), Palette.danger]
        default:
            return [Palette.accentA, Palette.accentB, Palette.accentC, Palette.accentA]
        }
    }

    private var glowColor: Color {
        switch state {
        case .listening: return Palette.accentB.opacity(0.6)
        case .thinking:  return Palette.accentA.opacity(0.55)
        case .speaking:  return Palette.accentC.opacity(0.5)
        case .error:     return Palette.danger.opacity(0.6)
        default:         return Palette.accentA.opacity(0.4)
        }
    }

    private var glowRadius: CGFloat {
        switch state {
        case .listening: return 14
        case .thinking:  return 12
        case .speaking:  return 16
        default:         return 10
        }
    }
}
