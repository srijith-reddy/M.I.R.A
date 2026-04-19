import SwiftUI
import AppKit

// @main entry. We don't use SwiftUI's WindowGroup / MenuBarExtra because
// the HUD needs NSPanel-level control (non-activating, custom level,
// vibrancy). Delegating to AppKit via NSApplicationDelegateAdaptor keeps
// the door open for finer-grained windowing later without fighting the
// scene graph.

@main
struct MIRAApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        // Empty settings scene — required to satisfy the App protocol.
        // The real UI is the NSPanel spun up by AppDelegate.
        Settings { EmptyView() }
    }
}
