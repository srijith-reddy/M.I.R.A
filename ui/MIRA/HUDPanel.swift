import AppKit
import SwiftUI

// The floating HUD window. NSPanel (not NSWindow) because panels can be
// non-activating — clicking the HUD doesn't steal focus from the user's
// current app. That distinction matters: MIRA is an assistant that lives
// *beside* your work, not a thing you switch to.
//
// Key properties:
//   * `.floating` level — sits above normal windows but below critical
//     system UI (menu bar dropdowns, spotlight).
//   * `.canJoinAllSpaces` + `.fullScreenAuxiliary` — follows the user
//     across desktops and appears over fullscreen apps.
//   * `titled: false`, `fullSizeContentView: true` — no chrome; the
//     SwiftUI view owns the entire surface.
//   * `isMovableByWindowBackground: true` — the user can drag from any
//     empty pixel, no titlebar needed.
//   * Vibrancy backing via NSVisualEffectView so the panel tints with the
//     desktop behind it instead of looking like a dark rectangle.

final class HUDPanel: NSPanel {
    init<Content: View>(rootView: Content) {
        let style: NSWindow.StyleMask = [
            .borderless,
            .nonactivatingPanel,
            .fullSizeContentView,
        ]
        super.init(
            contentRect: NSRect(x: 0, y: 0,
                                width: Theme.Metric.panelWidth,
                                height: Theme.Metric.panelMinHeight),
            styleMask: style,
            backing: .buffered,
            defer: false
        )

        self.isFloatingPanel = true
        self.level = .floating
        self.collectionBehavior = [
            .canJoinAllSpaces,
            .fullScreenAuxiliary,
            .stationary,
        ]
        self.isMovableByWindowBackground = true
        self.titleVisibility = .hidden
        self.titlebarAppearsTransparent = true
        self.isOpaque = false
        self.backgroundColor = .clear
        self.hasShadow = true
        self.hidesOnDeactivate = false

        // Vibrancy layer behind SwiftUI. Rounded corners applied via the
        // effect view's layer — panels with .borderless + clear bg don't
        // inherit rounded corners from the content view.
        //
        // Layer stack (front → back):
        //   * hosting (SwiftUI) — transparent background, content only
        //   * gradientBorder  — thin violet→cyan→magenta stroke
        //   * topHighlight    — 1px specular strip along the top edge
        //   * tintOverlay     — slight darken so text contrast survives
        //                       over bright desktop wallpapers
        //   * NSVisualEffectView (.hudWindow) — the real vibrancy
        let effect = NSVisualEffectView()
        effect.blendingMode = .behindWindow
        effect.material = .hudWindow
        effect.state = .active
        effect.wantsLayer = true
        effect.layer?.cornerRadius = Theme.Metric.cornerRadius
        effect.layer?.masksToBounds = true

        // Tint overlay — keeps text legible over any wallpaper. Black at
        // ~14% blends with vibrancy without killing transparency.
        let tintOverlay = CALayer()
        tintOverlay.backgroundColor = NSColor.black.withAlphaComponent(0.22).cgColor
        tintOverlay.cornerRadius = Theme.Metric.cornerRadius
        tintOverlay.frame = effect.bounds
        tintOverlay.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        effect.layer?.addSublayer(tintOverlay)

        // Gradient border. CAGradientLayer masked by a hollow ring gives
        // a crisp 1pt stroke that takes on the brand palette rather than
        // a flat white hairline.
        let border = CAGradientLayer()
        border.type = .axial
        border.colors = [
            NSColor(Theme.Color.orbA).withAlphaComponent(0.55).cgColor,
            NSColor(Theme.Color.orbB).withAlphaComponent(0.45).cgColor,
            NSColor(Theme.Color.orbC).withAlphaComponent(0.55).cgColor,
        ]
        border.startPoint = CGPoint(x: 0, y: 0)
        border.endPoint = CGPoint(x: 1, y: 1)
        border.frame = effect.bounds
        border.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]

        let ringMask = CAShapeLayer()
        ringMask.fillRule = .evenOdd
        ringMask.frame = effect.bounds
        ringMask.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        let updateRingMask: (CGRect) -> Void = { r in
            let outer = CGPath(roundedRect: r,
                               cornerWidth: Theme.Metric.cornerRadius,
                               cornerHeight: Theme.Metric.cornerRadius,
                               transform: nil)
            let inset = r.insetBy(dx: 0.8, dy: 0.8)
            let inner = CGPath(roundedRect: inset,
                               cornerWidth: Theme.Metric.cornerRadius - 0.8,
                               cornerHeight: Theme.Metric.cornerRadius - 0.8,
                               transform: nil)
            let combined = CGMutablePath()
            combined.addPath(outer)
            combined.addPath(inner)
            ringMask.path = combined
        }
        updateRingMask(effect.bounds)
        border.mask = ringMask
        effect.layer?.addSublayer(border)

        // Top-edge glass highlight. A soft white → clear gradient fading
        // over the first ~24pt, clipped to the rounded shape. Sells the
        // "real glass" effect more than a subtle border does.
        let highlight = CAGradientLayer()
        highlight.type = .axial
        highlight.colors = [
            NSColor.white.withAlphaComponent(0.18).cgColor,
            NSColor.white.withAlphaComponent(0.0).cgColor,
        ]
        highlight.startPoint = CGPoint(x: 0.5, y: 0)
        highlight.endPoint = CGPoint(x: 0.5, y: 0.28)
        highlight.frame = effect.bounds
        highlight.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        let highlightMask = CAShapeLayer()
        highlightMask.frame = effect.bounds
        highlightMask.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        highlightMask.path = CGPath(
            roundedRect: effect.bounds,
            cornerWidth: Theme.Metric.cornerRadius,
            cornerHeight: Theme.Metric.cornerRadius,
            transform: nil
        )
        highlight.mask = highlightMask
        effect.layer?.addSublayer(highlight)

        let hosting = NSHostingView(rootView: rootView)
        hosting.translatesAutoresizingMaskIntoConstraints = false
        effect.addSubview(hosting)
        NSLayoutConstraint.activate([
            hosting.topAnchor.constraint(equalTo: effect.topAnchor),
            hosting.bottomAnchor.constraint(equalTo: effect.bottomAnchor),
            hosting.leadingAnchor.constraint(equalTo: effect.leadingAnchor),
            hosting.trailingAnchor.constraint(equalTo: effect.trailingAnchor),
        ])

        // Deeper drop shadow than the default. NSPanel's hasShadow gives
        // a conservative system shadow — we want the HUD to clearly float.
        if let winLayer = self.contentView?.layer {
            winLayer.shadowColor = NSColor.black.cgColor
            winLayer.shadowOpacity = 0.55
            winLayer.shadowOffset = CGSize(width: 0, height: -8)
            winLayer.shadowRadius = 24
        }

        self.contentView = effect
    }

    // NSPanel defaults to canBecomeKey=false for non-activating; override
    // so the embedded text field can actually receive keystrokes when the
    // user clicks into it. Without this, typing goes to the underlying
    // app.
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    /// Position near the top-right of the active screen — close to the
    /// menubar glyph so the eye travels naturally from the status item to
    /// the HUD when it pops.
    func positionTopRight() {
        guard let screen = NSScreen.main else { return }
        let visible = screen.visibleFrame
        let size = self.frame.size
        let origin = NSPoint(
            x: visible.maxX - size.width - 16,
            y: visible.maxY - size.height - 12
        )
        self.setFrameOrigin(origin)
    }

    /// Top-center placement (Siri-style). Use when the HUD is the primary
    /// focus — auto-show on wake wants the user's eye drawn straight up.
    func positionTopCenter() {
        guard let screen = NSScreen.main else { return }
        let visible = screen.visibleFrame
        let size = self.frame.size
        let origin = NSPoint(
            x: visible.midX - size.width / 2,
            y: visible.maxY - size.height - 12
        )
        self.setFrameOrigin(origin)
    }

    func toggle() {
        if isVisible {
            orderOut(nil)
        } else {
            positionTopRight()
            // orderFrontRegardless → shown without stealing activation
            // from the frontmost app. `makeKey` only when the user clicks
            // into the text field.
            orderFrontRegardless()
        }
    }
}
