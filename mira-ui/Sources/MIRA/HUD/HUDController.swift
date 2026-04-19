import AppKit
import SwiftUI

/// Owns the floating NSPanel that hosts the pill. Separate from
/// CardController — pill and card are independent windows so the card
/// can size to content without dragging the pill around.
@MainActor
final class HUDController {

    private let state: AppState
    private var panel: NSPanel?

    init(state: AppState) {
        self.state = state
        makePanel()
    }

    private func makePanel() {
        let rootView = HUDView()
            .environmentObject(state)
            .frame(width: Metrics.pillWidth, height: Metrics.pillHeight + 16)

        let hosting = NSHostingView(rootView: rootView)
        hosting.translatesAutoresizingMaskIntoConstraints = false

        let panel = BorderlessPanel(
            contentRect: NSRect(x: 0, y: 0,
                                width: Metrics.pillWidth,
                                height: Metrics.pillHeight + 16),
            styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .floating
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        panel.contentView = hosting

        // Full width is mostly transparent — only the pill inside receives
        // clicks. `ignoresMouseEvents` toggles when the pill shows/hides.
        panel.ignoresMouseEvents = true

        self.panel = panel
        position()
        observeVisibility()
        panel.orderFrontRegardless()
    }

    private func observeVisibility() {
        // Keep a KVO-like observer via Combine sink. AppState publishes
        // pillVisible changes on the main actor.
        let obs = state.objectWillChange.sink { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.panel?.ignoresMouseEvents = !(self?.state.pillVisible ?? false)
            }
        }
        sinkBag.append(obs)
    }

    private var sinkBag: [Any] = []

    private func position() {
        guard let panel, let screen = NSScreen.main else { return }
        let visible = screen.visibleFrame
        let x = visible.origin.x + (visible.width - Metrics.pillWidth) / 2
        let y = visible.origin.y + visible.height - (Metrics.pillHeight + 16) - Metrics.hudTopMargin
        panel.setFrame(NSRect(x: x, y: y,
                              width: Metrics.pillWidth,
                              height: Metrics.pillHeight + 16),
                       display: true)
    }
}

/// NSPanel subclass that can become key without stealing activation.
/// Needed so the text input inside the pill gets keystrokes when the user
/// clicks into it, without yanking focus from the frontmost app on show.
final class BorderlessPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
