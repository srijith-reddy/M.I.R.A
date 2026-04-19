import AppKit
import SwiftUI
import Combine

// The app delegate owns:
//   * The menu-bar NSStatusItem (a small orb glyph that tints with state)
//   * The HUDPanel (shown on click, toggleable via global shortcut)
//   * The Bridge (WebSocket client to the Python daemon)
//
// This is an LSUIElement app — no Dock icon, no app-switcher presence.
// The only affordance is the menubar glyph.

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var panel: HUDPanel!
    private var bridge: Bridge!
    private var vm: HUDViewModel!
    private var bag = Set<AnyCancellable>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        bridge = Bridge()
        vm = HUDViewModel(bridge: bridge)
        bridge.start()

        buildStatusItem()
        buildPanel()

        // Mirror state onto the menubar glyph. Subtle color cue lets the
        // user know MIRA is listening without opening the HUD.
        vm.$state
            .removeDuplicates()
            .sink { [weak self] s in self?.updateStatusGlyph(state: s) }
            .store(in: &bag)

        vm.$connected
            .removeDuplicates()
            .sink { [weak self] _ in self?.updateStatusGlyph(state: self?.vm.state ?? .idle) }
            .store(in: &bag)

        // Auto-show on wake / active states; auto-hide after a grace
        // period of idle. Matches Siri's "pops in when you need it,
        // disappears when you don't" behavior.
        vm.$state
            .removeDuplicates()
            .sink { [weak self] s in self?.autoShowHide(for: s) }
            .store(in: &bag)

        bridge.events
            .receive(on: RunLoop.main)
            .sink { [weak self] ev in
                if case .wakeTriggered = ev { self?.showPanel() }
            }
            .store(in: &bag)
    }

    /// Hide timer. Started when state returns to idle; cancelled the
    /// moment anything else happens.
    private var hideWork: DispatchWorkItem?

    private func autoShowHide(for state: VoiceState) {
        hideWork?.cancel()
        if state == .idle {
            let work = DispatchWorkItem { [weak self] in
                guard let self, self.vm.state == .idle else { return }
                self.panel.orderOut(nil)
            }
            hideWork = work
            // 3.5s idle grace — long enough to read the final reply, short
            // enough that the HUD doesn't linger.
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.5, execute: work)
        } else {
            showPanel()
        }
    }

    private func showPanel() {
        guard let panel = panel else { return }
        if !panel.isVisible {
            panel.positionTopRight()
            panel.orderFrontRegardless()
        }
    }

    // MARK: - Status item

    private func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = siriOrbGlyph(state: .idle, alpha: 1.0)
            button.image?.isTemplate = false
            button.target = self
            button.action = #selector(statusClicked(_:))
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
            button.toolTip = "MIRA"
        }
    }

    @objc private func statusClicked(_ sender: Any?) {
        let event = NSApp.currentEvent
        if event?.type == .rightMouseUp {
            showMenu()
        } else {
            panel.toggle()
        }
    }

    private func showMenu() {
        let menu = NSMenu()
        let connected = vm.connected
        let status = NSMenuItem(
            title: connected ? "Connected" : "Waiting for MIRA…",
            action: nil, keyEquivalent: ""
        )
        status.isEnabled = false
        menu.addItem(status)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(
            title: "Show HUD", action: #selector(togglePanel),
            keyEquivalent: ""
        ))
        menu.addItem(NSMenuItem(
            title: "Open Dashboard",
            action: #selector(openDashboard),
            keyEquivalent: ""
        ))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(
            title: "Quit MIRA HUD", action: #selector(quit),
            keyEquivalent: "q"
        ))
        for item in menu.items { item.target = self }
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }

    @objc private func togglePanel() { panel.toggle() }

    @objc private func openDashboard() {
        // Python dashboard runs on 17650 by default (see settings.py).
        // We open it in the default browser rather than embedding — the
        // HUD is for live state; the dashboard is for post-hoc inspection.
        if let url = URL(string: "http://127.0.0.1:17650/") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func quit() { NSApp.terminate(nil) }

    private func updateStatusGlyph(state: VoiceState) {
        guard let button = statusItem.button else { return }
        // Dim when disconnected; otherwise always full-color so the orb
        // reads as the brand mark rather than a state light.
        let alpha: CGFloat = vm.connected ? 1.0 : 0.55
        let desaturate = !vm.connected || state == .idle ? 0.0 : 0.0
        _ = desaturate
        button.image = siriOrbGlyph(state: state, alpha: alpha)
    }

    /// Siri-style conic-gradient orb glyph. Rendered as a flat NSImage
    /// (menubar icons don't animate well — the system redraws them at
    /// ~1Hz). States tint the whole orb slightly rather than changing
    /// the glyph shape, which keeps the menubar visually stable.
    private func siriOrbGlyph(state: VoiceState, alpha: CGFloat) -> NSImage {
        let size = NSSize(width: 20, height: 20)
        let image = NSImage(size: size, flipped: false) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return true }

            let inset = rect.insetBy(dx: 2, dy: 2)
            let center = CGPoint(x: inset.midX, y: inset.midY)
            let radius = min(inset.width, inset.height) / 2

            // Clip to a circle so the conic gradient stays inside the orb.
            ctx.saveGState()
            ctx.addEllipse(in: inset)
            ctx.clip()

            // Conic gradient: violet → cyan → magenta → violet. Built
            // from many thin wedges — Core Graphics lacks a first-class
            // conic draw on older macOS, this works on all versions.
            let segments = 72
            let stops: [(CGFloat, NSColor)] = [
                (0.00, NSColor(Theme.Color.orbA)),
                (0.33, NSColor(Theme.Color.orbB)),
                (0.66, NSColor(Theme.Color.orbC)),
                (1.00, NSColor(Theme.Color.orbA)),
            ]
            // Inline blend — nested func can't call main-actor methods
            // from the NSImage draw closure (non-isolated context).
            func colorAt(_ t: CGFloat) -> NSColor {
                for i in 0..<(stops.count - 1) {
                    let (a, ca) = stops[i]
                    let (b, cb) = stops[i + 1]
                    if t >= a && t <= b {
                        let k = (t - a) / (b - a)
                        let la = ca.usingColorSpace(.deviceRGB) ?? ca
                        let lb = cb.usingColorSpace(.deviceRGB) ?? cb
                        return NSColor(
                            red:   la.redComponent   + (lb.redComponent   - la.redComponent)   * k,
                            green: la.greenComponent + (lb.greenComponent - la.greenComponent) * k,
                            blue:  la.blueComponent  + (lb.blueComponent  - la.blueComponent)  * k,
                            alpha: 1.0
                        )
                    }
                }
                return stops.last!.1
            }
            for i in 0..<segments {
                let t0 = CGFloat(i) / CGFloat(segments)
                let t1 = CGFloat(i + 1) / CGFloat(segments)
                let a0 = t0 * 2 * .pi - .pi / 2
                let a1 = t1 * 2 * .pi - .pi / 2
                let path = CGMutablePath()
                path.move(to: center)
                path.addArc(center: center, radius: radius + 1,
                            startAngle: a0, endAngle: a1, clockwise: false)
                path.closeSubpath()
                ctx.addPath(path)
                colorAt(t0).withAlphaComponent(alpha).setFill()
                ctx.fillPath()
            }
            ctx.restoreGState()

            // Glossy top-left highlight for depth.
            let glossRect = NSRect(
                x: inset.minX + inset.width * 0.22,
                y: inset.minY + inset.height * 0.52,
                width: inset.width * 0.38,
                height: inset.height * 0.28
            )
            NSColor.white.withAlphaComponent(0.45 * alpha).setFill()
            NSBezierPath(ovalIn: glossRect).fill()

            // State ring — subtle concentric stroke for listening /
            // thinking / speaking. Invisible on idle so the orb reads
            // as its natural self.
            if state != .idle && state != .setup {
                let ringColor: NSColor
                switch state {
                case .listening: ringColor = NSColor(Theme.Color.orbB)
                case .thinking:  ringColor = NSColor(Theme.Color.orbA)
                case .speaking:  ringColor = NSColor(Theme.Color.orbC)
                default:         ringColor = .white
                }
                ringColor.withAlphaComponent(0.85 * alpha).setStroke()
                let ring = NSBezierPath(ovalIn: inset.insetBy(dx: -1.2, dy: -1.2))
                ring.lineWidth = 1.0
                ring.stroke()
            }
            return true
        }
        image.isTemplate = false
        return image
    }


    // MARK: - Panel

    private func buildPanel() {
        let root = HUDView(vm: vm, bridge: bridge)
        panel = HUDPanel(rootView: root)
        panel.positionTopRight()
    }
}
