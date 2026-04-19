import SwiftUI

struct MenuContent: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        Text(state.connected ? "MIRA · connected" : "MIRA · offline")
            .font(.system(size: 11, weight: .medium))
        Divider()
        Button(state.pillVisible ? "Hide HUD" : "Show HUD") { state.toggleHUD() }
            .keyboardShortcut("h")
        Button("Open Dashboard")  { state.onCommand?(.openDashboard) }
        Divider()
        Button("Quit MIRA UI")    { state.onCommand?(.quit) }
            .keyboardShortcut("q")
    }
}
