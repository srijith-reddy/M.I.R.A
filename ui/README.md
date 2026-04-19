# MIRA HUD (SwiftUI)

Premium floating HUD for MIRA. Native macOS app, menubar-resident, connects
to the Python daemon over a local WebSocket (`127.0.0.1:17651`).

## What you get

- Menubar orb glyph that tints with voice state (idle / listening / thinking
  / speaking / setup).
- Floating HUD panel with vibrancy backing — non-activating, so clicking it
  doesn't pull focus from your current app.
- Live voice orb (Canvas + TimelineView, 60fps) driven by voice state.
- Streaming transcript, supervisor reply, and an activity feed of agent
  dispatches, tool calls, LLM timing/cost, reminders, and memory recalls.
- Text composer — type instead of speaking, barge-in, stop.

## Generate & build the Xcode project

The Swift sources live in [MIRA/](MIRA/). The project is defined by
[project.yml](project.yml) (XcodeGen). The generated `MIRA.xcodeproj` is a
build artifact — don't commit it.

```bash
brew install xcodegen           # one-time
cd ui
xcodegen generate               # writes MIRA.xcodeproj
open MIRA.xcodeproj              # or: xcodebuild -scheme MIRA build
```

From Xcode: **⌘R** to run. The menubar orb appears; click it to toggle
the HUD.

For a headless build:

```bash
xcodebuild -project MIRA.xcodeproj -scheme MIRA -configuration Debug build
open ~/Library/Developer/Xcode/DerivedData/MIRA-*/Build/Products/Debug/MIRA.app
```

The app is ad-hoc signed (`CODE_SIGN_IDENTITY = -`) so it runs locally
without a Developer account. Switch to a real team in `project.yml`
before notarizing.

## Wiring

- The HUD expects the Python daemon to expose a WebSocket on
  `127.0.0.1:17651`. This is the default (`MIRA_UI_BRIDGE_PORT`).
- Change the port with `Protocol.defaultPort` in `Models.swift` if you've
  overridden `MIRA_UI_BRIDGE_PORT` on the Python side.
- Protocol version is pinned to `v=1`. Frames are JSON text —
  `{ v, type, ts, data }`. Commands sent back: `cmd.stop`, `cmd.barge_in`,
  `cmd.submit_text`.

## File tour

| File              | What it owns                                           |
|-------------------|--------------------------------------------------------|
| `MIRAApp.swift`   | `@main`; NSApplicationDelegateAdaptor bootstrap.       |
| `AppDelegate.swift` | Menubar NSStatusItem, right-click menu, panel owner. |
| `HUDPanel.swift`  | NSPanel subclass — floating, non-activating, vibrancy. |
| `HUDView.swift`   | Root SwiftUI view + HUDViewModel (event → UI state).   |
| `Orb.swift`       | The voice orb. Canvas + TimelineView(.animation).      |
| `Bridge.swift`    | URLSessionWebSocketTask client, auto-reconnect.        |
| `Models.swift`    | Wire types, `Event` decoder, `Command` encoder, `JSON`.|
| `Theme.swift`     | Colors, metrics, fonts.                                |
| `Info.plist`      | `LSUIElement` + permissions.                           |

## Running end-to-end

1. Start MIRA: `mira --run` (this now starts the UI bridge automatically).
2. Build & run the HUD from Xcode.
3. Menubar orb should tint full white once connected. Say "Hey Mira" —
   the orb pulses and the transcript fills in.
