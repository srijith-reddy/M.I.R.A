#!/bin/bash
# One-click launcher. Wraps the two-process boot (Python daemon + Swift
# HUD) so the user has a single thing to double-click. Called by the
# Finder-clickable `Launch MIRA.app` on the desktop.

set -e

ROOT="/Users/shrey24/Desktop/M.I.R.A-main"
cd "$ROOT"

# Kill any stale daemon. Idempotent launch is nicer than accidentally
# double-binding port 17651 and leaving the HUD orphaned.
pkill -f "mira daemon" 2>/dev/null || true
pkill -x "MIRA Daemon" 2>/dev/null || true
sleep 0.3

# Launch the bundled daemon (has NSMicrophoneUsageDescription, so macOS
# will prompt for mic on first run instead of silently denying).
open "/Applications/MIRA Daemon.app"

# Give the bridge a moment to bind before the HUD tries to connect. The
# HUD auto-reconnects so this isn't strictly needed, but it avoids the
# "Waiting for MIRA…" flash on cold boot.
sleep 1

open ~/Library/Developer/Xcode/DerivedData/MIRA-*/Build/Products/Debug/MIRA.app
