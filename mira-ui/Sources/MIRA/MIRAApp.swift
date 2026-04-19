import SwiftUI
import AppKit

@main
struct MIRAApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        MenuBarExtra {
            MenuContent()
                .environmentObject(appDelegate.state)
        } label: {
            Image(systemName: "circle.hexagongrid.circle.fill")
                .renderingMode(.template)
        }
        .menuBarExtraStyle(.menu)
    }
}

/// Owns lifecycle for the HUD panel, card panel, dashboard window, and the
/// bridge connection to the Python daemon. We use a delegate rather than a
/// WindowGroup scene because the HUD is a borderless NSPanel — SwiftUI's
/// scene system can't express that (floating, non-activating, per-space).
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let state = AppState()
    private var hud: HUDController?
    private var card: CardController?
    private var dashboard: DashboardController?

    nonisolated override init() { super.init() }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Agent / accessory style: menubar icon only, no Dock tile.
        NSApp.setActivationPolicy(.accessory)

        hud = HUDController(state: state)
        card = CardController(state: state)
        dashboard = DashboardController(state: state)

        state.onCommand = { [weak self] cmd in
            self?.handleCommand(cmd)
        }

        state.bridge.connect()
        state.dashboard.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        state.bridge.disconnect()
        state.dashboard.stop()
    }

    private func handleCommand(_ cmd: AppState.UICommand) {
        switch cmd {
        case .openDashboard:
            dashboard?.showWindow()
        case .quit:
            NSApp.terminate(nil)
        }
    }
}
