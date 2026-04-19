import SwiftUI

// The voice orb. Canvas + TimelineView for 60fps animation without paying
// SwiftUI's diffing tax on every frame. State ties into `VoiceState` —
// each state has its own motion character:
//
//   * idle      → slow rotating conic, gentle breath
//   * listening → tight pulse synced to `level` (0...1), bright rim
//   * thinking  → fast conic spin + counter-rotating shimmer arc
//   * speaking  → concentric ripples + warm bloom at ~2Hz
//   * setup     → muted amber core, single slow pulse
//
// Rendering order (bottom → top) each frame:
//   1. Outer bloom (radial, very soft, big)
//   2. Conic plate (rotates; provides the color story)
//   3. Glossy core highlight (specular dot, top-left)
//   4. State-specific overlays (ripples, arcs)
//   5. Outer rim stroke (fine gradient line)

struct Orb: View {
    let state: VoiceState
    /// 0...1 — instantaneous audio level. Optional; the orb animates
    /// without it. When we have real RMS from the voice loop, push it
    /// through and the listening pulse locks onto speech cadence.
    var level: Double = 0.0

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60.0)) { context in
            Canvas { ctx, size in
                let t = context.date.timeIntervalSinceReferenceDate
                draw(context: ctx, size: size, t: t)
            }
        }
        .frame(width: Theme.Metric.orbSize, height: Theme.Metric.orbSize)
        .animation(.easeInOut(duration: 0.35), value: state)
    }

    private func draw(context ctx: GraphicsContext, size: CGSize, t: TimeInterval) {
        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        let baseRadius = min(size.width, size.height) / 2 - 8

        // State-specific motion params.
        let spin: Double
        let breath: Double
        let coreScale: CGFloat
        let haloBoost: Double
        switch state {
        case .idle:
            spin = t * 0.35
            breath = 0.5 + 0.5 * sin(t * 1.1)
            coreScale = 0.70 + 0.03 * CGFloat(breath)
            haloBoost = 0.35
        case .listening:
            spin = t * 0.8
            let env = max(level, 0.15 + 0.18 * sin(t * 6.2))
            breath = env
            coreScale = 0.68 + 0.28 * CGFloat(env)
            haloBoost = 0.55 + 0.4 * env
        case .thinking:
            spin = t * 2.4
            breath = 0.5 + 0.08 * sin(t * 3.0)
            coreScale = 0.72
            haloBoost = 0.48
        case .speaking:
            spin = t * 1.1
            breath = 0.5 + 0.5 * sin(t * 2.4)
            coreScale = 0.74 + 0.04 * CGFloat(breath)
            haloBoost = 0.6
        case .setup:
            spin = t * 0.25
            breath = 0.5 + 0.5 * sin(t * 0.9)
            coreScale = 0.70
            haloBoost = 0.28
        }

        // 1) Outer bloom — huge, soft radial. Saturation-matched to state.
        let bloomColors: [Color]
        switch state {
        case .idle:
            bloomColors = [Theme.Color.orbB.opacity(0.35 * haloBoost), .clear]
        case .listening:
            bloomColors = [Theme.Color.orbB.opacity(0.55 * haloBoost), .clear]
        case .thinking:
            bloomColors = [Theme.Color.orbA.opacity(0.45 * haloBoost), .clear]
        case .speaking:
            bloomColors = [Theme.Color.orbC.opacity(0.5 * haloBoost), .clear]
        case .setup:
            bloomColors = [Theme.Color.warm.opacity(0.35 * haloBoost), .clear]
        }
        let bloomR = baseRadius * 1.8
        ctx.fill(
            Path(ellipseIn: CGRect(
                x: center.x - bloomR, y: center.y - bloomR,
                width: bloomR * 2, height: bloomR * 2
            )),
            with: .radialGradient(
                Gradient(colors: bloomColors),
                center: center,
                startRadius: baseRadius * 0.3,
                endRadius: bloomR
            )
        )

        // 2) Conic plate — rotates. Setup is a warm monochrome disc.
        let plateRadius = baseRadius * coreScale
        let plateRect = CGRect(
            x: center.x - plateRadius, y: center.y - plateRadius,
            width: plateRadius * 2, height: plateRadius * 2
        )

        if state == .setup {
            ctx.fill(
                Path(ellipseIn: plateRect),
                with: .radialGradient(
                    Gradient(colors: [
                        Theme.Color.warm.opacity(0.95),
                        Theme.Color.warm.opacity(0.5),
                    ]),
                    center: center,
                    startRadius: 0,
                    endRadius: plateRadius
                )
            )
        } else {
            // Rotate the conic by translating to center, rotating, painting.
            var rotated = ctx
            rotated.translateBy(x: center.x, y: center.y)
            rotated.rotate(by: .radians(spin))
            rotated.translateBy(x: -center.x, y: -center.y)
            rotated.fill(
                Path(ellipseIn: plateRect),
                with: .conicGradient(
                    Gradient(colors: [
                        Theme.Color.orbA,
                        Theme.Color.orbB,
                        Theme.Color.orbC,
                        Theme.Color.orbA,
                    ]),
                    center: center
                )
            )
        }

        // 3) Glossy highlight — small white radial, top-left of plate.
        // Sits on top of the conic to fake a specular reflection.
        let glossCenter = CGPoint(
            x: center.x - plateRadius * 0.32,
            y: center.y - plateRadius * 0.38
        )
        let glossR = plateRadius * 0.55
        ctx.fill(
            Path(ellipseIn: CGRect(
                x: glossCenter.x - glossR, y: glossCenter.y - glossR,
                width: glossR * 2, height: glossR * 2
            )),
            with: .radialGradient(
                Gradient(colors: [
                    .white.opacity(0.55),
                    .white.opacity(0.15),
                    .clear,
                ]),
                center: glossCenter,
                startRadius: 0,
                endRadius: glossR
            )
        )

        // 4) State-specific overlays.
        switch state {
        case .idle, .setup:
            break

        case .listening:
            // Bright rim + soft secondary ring pulsing with level.
            drawRing(ctx: ctx, center: center,
                     radius: plateRadius + 2,
                     width: 1.2,
                     color: Theme.Color.orbB.opacity(0.75))
            let pulse = baseRadius * (0.98 + 0.04 * breath)
            drawRing(ctx: ctx, center: center,
                     radius: pulse,
                     width: 0.7,
                     color: Theme.Color.orbB.opacity(0.35 + 0.35 * breath))

        case .thinking:
            // Two counter-rotating shimmer arcs just outside the plate.
            drawArc(ctx: ctx, center: center,
                    radius: plateRadius + 4,
                    start: t * 1.6, sweep: .pi * 0.55,
                    width: 1.6,
                    color: Theme.Color.orbB.opacity(0.85))
            drawArc(ctx: ctx, center: center,
                    radius: plateRadius + 9,
                    start: -t * 1.2 + .pi, sweep: .pi * 0.35,
                    width: 1.0,
                    color: Theme.Color.orbC.opacity(0.55))

        case .speaking:
            // Three expanding ripples, fading as they grow. Colors walk
            // the palette so successive ripples look distinct.
            let rippleColors = [Theme.Color.orbC, Theme.Color.orbB, Theme.Color.orbA]
            for i in 0..<3 {
                let phase = (t * 1.6 + Double(i) * 0.66).truncatingRemainder(dividingBy: 2.0)
                let progress = phase / 2.0
                let rr = plateRadius + CGFloat(progress) * (baseRadius - plateRadius) * 1.6
                let alpha = (1.0 - progress) * 0.7
                drawRing(ctx: ctx, center: center,
                         radius: rr, width: 1.3,
                         color: rippleColors[i].opacity(alpha))
            }
        }

        // 5) Outer rim — hairline stroke for definition. Skip for speaking
        // (the ripples already define the edge and this would clutter).
        if state != .speaking {
            drawRing(
                ctx: ctx,
                center: center,
                radius: plateRadius + 0.5,
                width: 0.7,
                color: .white.opacity(0.22)
            )
        }
    }

    private func drawRing(ctx: GraphicsContext, center: CGPoint,
                          radius: CGFloat, width: CGFloat, color: Color) {
        let rect = CGRect(
            x: center.x - radius, y: center.y - radius,
            width: radius * 2, height: radius * 2
        )
        ctx.stroke(Path(ellipseIn: rect), with: .color(color), lineWidth: width)
    }

    private func drawArc(ctx: GraphicsContext, center: CGPoint,
                         radius: CGFloat, start: Double, sweep: Double,
                         width: CGFloat, color: Color) {
        var p = Path()
        p.addArc(
            center: center,
            radius: radius,
            startAngle: .radians(start),
            endAngle: .radians(start + sweep),
            clockwise: false
        )
        ctx.stroke(p, with: .color(color),
                   style: StrokeStyle(lineWidth: width, lineCap: .round))
    }
}
