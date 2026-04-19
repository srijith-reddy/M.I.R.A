import AppKit
import SwiftUI

/// Manages a single NSWindow hosting the dashboard. Created lazily the
/// first time the user requests it; subsequent "open" calls reuse the
/// same window. Close just hides; next open brings it forward.
@MainActor
final class DashboardController: NSObject, NSWindowDelegate {

    private let state: AppState
    private var window: NSWindow?

    init(state: AppState) {
        self.state = state
    }

    func showWindow() {
        if let window {
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
            state.dashboard.beginPolling()
            return
        }

        let root = DashboardRootView()
            .environmentObject(state)
            .environmentObject(state.dashboard)

        let hosting = NSHostingController(rootView: root)
        let w = NSWindow(contentViewController: hosting)
        w.title = "MIRA"
        w.setContentSize(NSSize(width: 1100, height: 720))
        w.minSize = NSSize(width: 900, height: 600)
        w.styleMask = [.titled, .closable, .resizable, .miniaturizable, .fullSizeContentView]
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.isMovableByWindowBackground = true
        w.toolbarStyle = .unified
        w.backgroundColor = NSColor(red: 0.06, green: 0.07, blue: 0.09, alpha: 1.0)
        w.appearance = NSAppearance(named: .darkAqua)
        w.center()
        w.delegate = self
        self.window = w

        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
        state.dashboard.beginPolling()
    }

    func windowWillClose(_ notification: Notification) {
        state.dashboard.endPolling()
        window = nil
    }
}
