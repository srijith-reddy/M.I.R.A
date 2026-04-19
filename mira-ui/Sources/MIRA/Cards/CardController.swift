import AppKit
import SwiftUI
import Combine

/// Second NSPanel that hosts the active card, positioned below the pill.
/// Keeping it in its own window means the card can grow to fit content
/// without dragging the pill around, and the click surface outside the
/// card is completely transparent to mouse events.
@MainActor
final class CardController {

    private let state: AppState
    private var panel: NSPanel?
    private var hosting: NSView?
    private var sinkBag: [AnyCancellable] = []

    init(state: AppState) {
        self.state = state
        makePanel()
        observe()
    }

    private func makePanel() {
        let root = CardHostView()
            .environmentObject(state)

        let hosting = NSHostingView(rootView: root)
        self.hosting = hosting

        let panel = BorderlessPanel(
            contentRect: NSRect(x: 0, y: 0, width: Metrics.cardWidth, height: 200),
            styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .floating
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        panel.contentView = hosting
        panel.ignoresMouseEvents = true
        panel.alphaValue = 0

        self.panel = panel
    }

    private func observe() {
        state.$currentCard
            .receive(on: RunLoop.main)
            .sink { [weak self] card in
                self?.apply(card: card)
            }
            .store(in: &sinkBag)
    }

    private func apply(card: CardPayload?) {
        guard panel != nil else { return }
        if card == nil {
            fadeOut()
            return
        }
        // Measure content height after SwiftUI renders one pass. We size
        // the panel to the hosting view's fitting size so the card hugs
        // its content, just like SwiftUI windows do natively.
        DispatchQueue.main.async { [weak self] in
            guard let self, let hosting = self.hosting else { return }
            hosting.layoutSubtreeIfNeeded()
            let fit = hosting.fittingSize
            let height = max(120, min(520, fit.height))
            self.resize(to: height)
            self.fadeIn()
        }
    }

    private func resize(to height: CGFloat) {
        guard let panel, let screen = NSScreen.main else { return }
        let visible = screen.visibleFrame
        let x = visible.origin.x + (visible.width - Metrics.cardWidth) / 2
        let pillTop = visible.origin.y + visible.height - Metrics.hudTopMargin
        let pillBottom = pillTop - (Metrics.pillHeight + 16)
        let y = pillBottom - Metrics.cardGap - height
        panel.setFrame(
            NSRect(x: x, y: y, width: Metrics.cardWidth, height: height),
            display: true, animate: false
        )
    }

    private func fadeIn() {
        guard let panel else { return }
        panel.orderFrontRegardless()
        panel.ignoresMouseEvents = false
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.22
            ctx.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 0.9, 0.2, 1.0)
            panel.animator().alphaValue = 1.0
        }
    }

    private func fadeOut() {
        guard let panel else { return }
        panel.ignoresMouseEvents = true
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.2
            panel.animator().alphaValue = 0.0
        }, completionHandler: {
            panel.orderOut(nil)
        })
    }
}
