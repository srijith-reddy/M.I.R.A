# MIRA · Swift UI

Native SwiftUI frontend for the MIRA voice assistant daemon.

Replaces the WKWebView + HTML HUD and the Flask dashboard with a single
menubar app. The Python daemon is unchanged — this app connects over the
existing WebSocket (`ws://127.0.0.1:17651`) and HTTP dashboard
(`http://127.0.0.1:17650`) surfaces.

## What it gives you

- **Glass HUD pill** — conic orb, live transcript, state label, audio meter
- **Per-domain cards** — separate floating panel with six templates:
  `product`, `source`, `email`, `calendar`, `reminder`, `action`, plus a
  generic fallback
- **Dashboard window** — overview stats, recent turns, LLM spend, per-turn trace
- **Zero backend changes required** — drops in next to the existing HTML HUD

## Requirements

- macOS 14 or later (uses SwiftUI `MenuBarExtra`, `.ultraThinMaterial`)
- Xcode 15 or later (or the Swift 5.9 toolchain via `swift build`)

## Build and run

**From Xcode (recommended while developing):**

```bash
open Package.swift
```

Xcode will resolve the package, let you pick the `MIRA` scheme, and run
with full SwiftUI Previews support.

**From the command line:**

```bash
cd mira-ui
swift run -c release MIRA
```

The app starts in the menubar (no Dock icon). Click the menubar glyph to
see status, open the dashboard, or quit.

## First-time checklist

1. Start the Python daemon as you do today (`python app_entry.py daemon`
   or via `launch.sh`). The WebSocket bridge on `17651` and the HTTP
   dashboard on `17650` must be up.
2. Launch the Swift app. The menubar should read `MIRA · connected`
   within one second.
3. Say "Hey MIRA" — the pill fades in, the orb animates through
   listening → thinking → speaking.
4. Ask something that produces a card ("top laptops under $1000") — the
   card panel appears under the pill, sized to content.

## Packaging as a signed `.app`

Swift packages produce a bare executable. To ship a proper `.app`
bundle:

```bash
swift build -c release
# Creates .build/release/MIRA

# Wrap it in a bundle with Info.plist (LSUIElement=true for menubar-only)
# and codesign for distribution. See Apple's "Distributing a Command-Line
# Tool from a .app Bundle" docs, or wrap with XcodeGen / tuist.
```

For local use `swift run` is enough.

## Project layout

```
Sources/MIRA/
├── MIRAApp.swift              @main + AppDelegate + MenuBarExtra
├── AppState.swift             Observable state, routes bridge events into views
├── MenuContent.swift
├── Bridge/
│   ├── BridgeEvent.swift      Decoded log_event frames
│   ├── WebSocketClient.swift  Auto-reconnecting WS client
│   └── DashboardClient.swift  HTTP client + Decodable response types
├── HUD/
│   ├── HUDController.swift    NSPanel host for the pill
│   ├── HUDView.swift          Pill composition + expand-to-input
│   ├── Orb.swift              Canvas-drawn conic orb per state
│   └── Waveform.swift         Mic-level meter
├── Cards/
│   ├── CardController.swift   Separate NSPanel, content-sized
│   ├── CardHostView.swift     Glass shell + per-kind dispatcher
│   ├── CardPayload.swift      Decoded ui.card event
│   └── Templates/
│       ├── RowChrome.swift        Shared row + thumb + stars
│       ├── ProductCardView.swift  Commerce
│       ├── SourceCardView.swift   Research citations
│       ├── EmailCardView.swift    Inbox
│       ├── CalendarCardView.swift Schedule
│       ├── ReminderCardView.swift Reminders
│       ├── ActionCardView.swift   Browser / device actions
│       └── GenericListCardView.swift
├── Dashboard/
│   ├── DashboardController.swift  NSWindow host
│   ├── DashboardRootView.swift    Sidebar + content split
│   ├── OverviewView.swift         Stats + recent activity
│   ├── TurnsView.swift            All recent turns
│   ├── SpendView.swift            Per-model LLM spend
│   └── TraceView.swift            Per-turn event trace
└── Design/
    ├── Tokens.swift           Palette + Typography + Metrics
    └── GlassPanel.swift       Reusable blurred container
```

## Contract with the Python daemon

### Events consumed (via WebSocket)

| Event | Purpose |
|-------|---------|
| `ui.state` | Drives orb/pill state (idle/listening/thinking/speaking/setup/error) |
| `wake.triggered` | Wake word fired — reset HUD for a fresh turn |
| `voice.transcript`, `voice.followup_transcript` | User speech, live |
| `voice.level` | Mic RMS 0–1, drives waveform |
| `supervisor.reply` | Agent's spoken reply text |
| `supervisor.delegate` | Which agent is running this turn |
| `tool.dispatch`, `tool.result` | Shows "Using <tool>" in the pill |
| `reminder.fired` | Surfaces as a pill line |
| `ui.card` | Renders a card in the second panel |
| `voice.loop_error`, `browser.error`, `web.search.error` | Error chrome |

### Commands sent back

| Command | Payload | Purpose |
|---------|---------|---------|
| `cmd.submit_text` | `{text}` | User typed into the pill input |
| `cmd.stop` | `{}` | Interrupt current turn |
| `cmd.barge_in` | `{}` | Talk over TTS |

### Card payload (`ui.card` event)

```json
{
  "kind": "product",
  "title": "Top laptops under $1,000",
  "subtitle": null,
  "footer": "3 sources · 3 read",
  "ttl_ms": 20000,
  "rows": [
    {
      "title": "Acer Swift Go 14",
      "subtitle": "14\" OLED · 16GB RAM",
      "trailing": "$899",
      "meta": "amazon.com",
      "rating": 4.5,
      "thumbnail": "https://…",
      "url": "https://…"
    }
  ]
}
```

`kind` is optional — when omitted, the Swift side infers from the
`agent` field + row shape. Explicit kind is always safer.

## Design decisions

- **Two NSPanels, not one.** Pill and card are independent windows so
  the card can be content-sized without dragging the pill around. The
  click-through rect outside each panel stays tight.
- **`.ultraThinMaterial`, not CSS backdrop-filter.** Real AppKit
  vibrancy means the blur respects the wallpaper behind the HUD, not
  just the app's own content. This is the single biggest visual
  upgrade over the WKWebView version.
- **Per-domain templates.** One `View` per card kind beats one generic
  list with 8 optional fields. Easier to polish, easier to reason
  about, and each template can add affordances (star ratings for
  products, time blocks for calendar) without the others paying.
- **AppState as single source of truth.** Every view reads from one
  `@MainActor` observable object. No prop drilling, no subscribers
  buried in individual views.
