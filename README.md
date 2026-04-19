# MIRA

MIRA is a voice-first personal assistant for macOS designed for people who want more than a chat window. It lives quietly in the menu bar, listens for a wake word, routes each spoken turn through a fast classifier into a supervisor that coordinates specialists, and replies through streaming speech while showing structured cards on a native SwiftUI HUD.

## What MIRA Does

- listens for a wake word locally and runs a full voice loop with barge-in
- transcribes spoken input with streaming speech-to-text
- routes every turn through a tier-0 fast router before escalating to a supervisor
- delegates to specialist agents for research, web browsing, commerce, communication, device control, and memory
- answers back with streaming text-to-speech so the first word lands in about a second
- renders structured cards alongside the spoken reply with titles, thumbnails, scores, prices, and source links
- controls a real Chromium browser profile for logged-in actions like shopping, booking, and inbox triage
- pulls live sports scores directly from ESPN's public scoreboard API across nine major leagues
- plays music from any YouTube query with automatic ducking under the voice reply
- remembers the user across sessions through semantic recall and a structured profile
- exposes a local dashboard for turn-by-turn traces, per-turn cost, and 24-hour spend

## Product Direction

This repository is built around a calm, native, voice-first experience rather than a chat app dressed up with microphone access.

The current app is:

- headless on the Python side, with a pure Swift menu-bar UI
- multi-agent, typed end-to-end, and metered per call
- confirmation-gated on anything with side effects
- observable out of the box, with a local dashboard that doesn't leave the machine
- provider-agnostic, with one gateway that speaks OpenAI, Anthropic, Groq, and DeepSeek by model-name prefix

MIRA is designed to feel less like a chatbot and more like an assistant that happens to speak — fast to invoke, quiet when idle, responsive when it matters, and honest about what it can and can't do.

## Key Flows

### Wake And Listen

The voice loop is the entry point for almost every turn.

Users can:

- say "Hey MIRA" to invoke the assistant without touching the keyboard
- tune sensitivity to match their environment and voice
- fall back to typing directly into the HUD pill when voice isn't appropriate
- barge in mid-reply to interrupt and re-open the mic
- rely on start and end chimes plus a speech monitor that prevents MIRA from retriggering itself

Under the hood the wake engine picks between Porcupine and openWakeWord depending on which key is configured, and the streaming transcriber prefers Deepgram Nova with OpenAI Whisper as a fallback.

### Route And Plan

Every transcript goes through a two-stage planner so simple turns stay fast and complex turns get real reasoning budget.

The flow is:

- a tier-0 fast router decides in under 200ms whether a turn is smalltalk, a direct specialist call, or supervisor-level coordination
- the supervisor agent runs a ReAct-style loop with a handful of hops, delegating to specialists and composing a final spoken reply
- the supervisor keeps a memory view per turn with user profile, recent turns, and semantically recalled episodes
- failed specialists are blocked from being re-dispatched in the same turn so the model can't loop on a dead end

### Specialists

Each specialist is a single file under the agents package and owns its own tools.

The current roster is:

- research — factual answers, explanations, and a live-data path that plans web queries, searches Brave, fetches with a progressive pipeline, reranks chunks, and streams a synthesized answer straight into TTS
- browser — drives a real logged-in Chromium profile through Playwright for logged-in actions, form fill, and confirmed clicks
- commerce — shopping research and price comparisons with a trust-tiered search mode, plus price-watch reminders
- communication — email drafting and send, calendar events, and reminders, all confirmation-gated
- device — music playback, iMessage, weather, directions, Finder, system settings, and opening or quitting Mac apps
- memory — semantic recall and structured profile facts across past sessions

### Speak And Show

Replies are delivered through two surfaces that fire in parallel.

Users get:

- streaming Cartesia TTS that starts speaking while the model is still generating the rest of the reply
- automatic music ducking so any active track pauses for the duration of the reply and resumes the moment it ends
- a SwiftUI HUD pill that shows live voice state, current transcript, and the reply text
- structured cards rendered in native SwiftUI templates for sources, products, calendar events, emails, reminders, and plain lists
- brand-accurate thumbnails on cards, attached post-hoc by a cheap Haiku extractor that matches rows to the sources the turn already collected

The card layer gracefully degrades: if an agent emits a structured payload the card uses that; otherwise a list auto-parser synthesizes rows from the spoken reply; and if the supervisor had to compose the final answer itself, the Haiku extractor re-runs with the aggregated sources so the fallback card still gets thumbnails.

### Live Sports

Sports score questions bypass the general web pipeline entirely.

When a question matches the sports gate, MIRA:

- fans out in parallel across ESPN's public scoreboards for NBA, WNBA, NFL, MLB, NHL, NCAAF, NCAAM, NCAAW, and MLS
- scores each event against the user's team-name tokens and picks the best match
- formats the spoken reply differently for pre-game, in-progress, and final states
- emits a source-style card with team logos as thumbnails and the current scores as trailing values

This path exists because live score widgets on most sites are JavaScript-rendered and invisible to the general scrape pipeline, while ESPN's unauthenticated JSON scoreboard returns clean data in a few hundred milliseconds.

### Control And Observe

A thin menu-bar surface exposes the rest.

From the menu or the HUD users can:

- toggle the HUD pill on and off with a keyboard shortcut
- open a local dashboard that shows 24-hour spend, per-turn traces, and recent turns
- quit the UI without killing the daemon, or the daemon without killing the UI

## Motion And Voice

MIRA's voice layer is built to feel immediate and uninterrupted rather than polite and slow.

That means it:

- streams TTS chunks to the speaker as the model generates them
- caches TTS bytes for common phrases so repeated responses play from disk
- claims the macOS "Now Playing" focus so hardware play/pause keys, AirPods squeezes, Control Center, and the Apple Watch all route to MIRA when music is playing
- auto-pauses music under TTS and auto-resumes it afterwards
- uses a speech monitor to suppress wake-word detection while MIRA's own voice is on the output device

What it does not do:

- run in a browser or a chat tab
- replace the voice APIs it's built on during provider outages
- work on non-macOS platforms today

## Tech Stack

- Python 3.11
- AsyncIO, Pydantic, httpx, FastAPI-style HTTP dashboard
- SwiftUI, AppKit, MenuBarExtra, MPRemoteCommandCenter, MPNowPlayingInfoCenter
- Playwright + Chromium for the browser agent
- Cartesia Sonic-2 for streaming TTS
- Deepgram Nova with OpenAI Whisper fallback for STT
- Porcupine or openWakeWord for wake detection
- Brave Search, trafilatura, Crawl4AI, and Playwright for the web retrieval pipeline
- yt-dlp and ffplay for music playback
- OpenAI, Anthropic, Groq, and DeepSeek behind one LLM gateway
- SQLite for session state, reminders, memory, and event persistence

## Repository Structure

```text
src/mira/
  __main__.py          mira CLI entry point
  agents/              router, supervisor, specialists, card extractor
  browser/             Playwright runtime with a shared Chromium profile
  config/              pydantic settings and path resolution
  diagnostics.py       mira doctor checks
  evals/               offline test harness
  install/             LaunchAgent install and setup wizard
  integrations/        Gmail, Calendar, contacts, external services
  obs/                 logging, event bus, dashboard, Swift UI WebSocket bridge
  runtime/             LLM gateway, tool registry, orchestrator, scheduler, store
  safety/              domain trust tiering for search
  tools/               browser, web, calendar, email, reminders, music, maps, weather, memory
  ui/                  headless macOS daemon, media key wiring
  voice/               wakeword, recorder, STT, TTS, chimes, speech monitor
  web/                 planner, retrieval, chunking, rerank, synthesis

mira-ui/
  Package.swift        SwiftPM app manifest
  Sources/MIRA/        SwiftUI app, HUD, cards, dashboard window, WebSocket client

tests/                 pytest suite
scripts/               one-off maintenance and evaluation scripts
models/                custom wake-word .onnx files (gitignored)
```

## Local Development

### Daemon

```bash
cp .env.example .env
pip install '.[providers,wakeword-oss,dev]'
playwright install chromium
mira doctor
mira daemon
```

### Swift UI

```bash
cd mira-ui
swift build -c release
./.build/release/MIRA
```

The Swift app connects to the Python daemon over a local WebSocket on port 17651 and a JSON HTTP dashboard on port 17650. Either side can be restarted independently without breaking the other.

### One-Shot Text Turn

```bash
mira text "what's the score of the Rockets-Lakers game"
```

Useful for debugging without involving the microphone.

## Environment

The backend reads from a `.env` file in the repository root, falling back to `~/Library/Application Support/mira/.env`.

Core keys:

- `OPENAI_API_KEY`
- `CARTESIA_API_KEY`
- `CARTESIA_VOICE`

Wake word keys (choose one):

- `PICOVOICE_ACCESS_KEY` for Porcupine
- or `WAKEWORD_BACKEND=openwakeword` with the `wakeword-oss` extra for a fully local backend

Optional keys:

- `ANTHROPIC_API_KEY`
- `GROQ_API_KEY`
- `DEEPSEEK_API_KEY`
- `DEEPGRAM_API_KEY`
- `BRAVE_SEARCH_API_KEY`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` for Gmail and Calendar
- `OPENWEATHER_API_KEY` for weather

Planner routing is by model-name prefix rather than provider env var, so mixing providers is a matter of changing one string. Prices are tracked in a per-model cost table inside the LLM gateway and surface in the dashboard per turn and per day.

## Current Status

What is real today:

- headless Python daemon with signal-based shutdown and a single-instance lock
- native SwiftUI menu-bar app with HUD pill, card templates, and dashboard window
- streaming voice loop with wake word, STT, TTS, and barge-in
- tier-0 fast router plus multi-hop supervisor with memory recall
- five specialists wired end-to-end with typed tools
- confirmation gates on every side-effecting tool
- live sports path with parallel ESPN fan-out
- commerce and research card paths with brand-accurate thumbnails
- supervisor fallback card extractor for turns that would otherwise land as plain bullets
- music playback with ducking under TTS and full hardware media-key control
- local dashboard on loopback with 24-hour stats, recent turns, and per-turn event traces
- SQLite-backed session store, reminder scheduler, and semantic memory

What still depends on external providers or future hardening:

- STT, TTS, and planner reliability across provider outages
- Playwright-driven browser flows on sites that fight automation
- broader wake-word tuning across languages and accents
- production packaging and signed distribution of the Swift app
- deeper test coverage on the long-tail specialists

## Why This Repo Exists

Voice assistants are usually either too shallow to be useful or too cloud-heavy to be trusted.

MIRA exists to make a personal assistant that feels like it belongs on the machine it runs on — local daemon, native UI, streaming speech, typed tools, real confirmations, honest costs — without giving up the multi-agent reasoning and web reach that make a modern assistant worth talking to.
