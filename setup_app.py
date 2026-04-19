"""py2app config for building `MIRA.app`.

Usage:
    python setup_app.py py2app            # release build
    python setup_app.py py2app -A         # alias build (dev — symlinks, fast iteration)

Produces `dist/MIRA.app`. Drag it into /Applications, then either launch
it once from Finder (macOS will prompt for mic permissions on first
wake-word capture) or run `mira install` to register the LaunchAgent
for auto-start at login.

Why py2app over PyInstaller:
  * Menu-bar app: LSUIElement in Info.plist is one line here, PyInstaller
    requires an external plist merge.
  * mic permission strings (NSMicrophoneUsageDescription) go directly into
    Info.plist so macOS shows the right prompt.
  * No cross-platform story needed — MIRA is mac-only today.

Not handled here (by design):
  * Code signing / notarization. Needs a Developer ID and is a separate
    step. Unsigned builds still run; users see a one-time Gatekeeper
    prompt on first launch.
  * DMG packaging. `hdiutil create` after the build if we ever ship.
"""

from __future__ import annotations

from setuptools import setup


APP = ["app_entry.py"]

# Packages py2app should pull in whole — otherwise its static analyzer
# misses dynamically-imported submodules and you get a half-linked app.
INCLUDE_PACKAGES = [
    "mira",
    "rumps",
    "openai",
    "httpx",
    "anyio",
    "pydantic",
    "pydantic_settings",
    "dotenv",
    "numpy",
    "webrtcvad",
    "sounddevice",
    "pvporcupine",
    "pvrecorder",
    "cartesia",
    "deepgram",
    "WebKit",
    "playwright",
    "yt_dlp",
    "trafilatura",
    "selectolax",
    "lxml",
]

OPTIONS = {
    "argv_emulation": False,
    "packages": INCLUDE_PACKAGES,
    "plist": {
        "CFBundleName": "MIRA Daemon",
        "CFBundleDisplayName": "MIRA Daemon",
        "CFBundleIdentifier": "com.mira.agent.daemon",
        "CFBundleVersion": "0.2.0",
        "CFBundleShortVersionString": "0.2.0",
        # LSUIElement=True hides the app from Dock and cmd-tab; it lives
        # only in the menu bar, matching how the user actually experiences
        # it.
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        # macOS will display this string verbatim in the mic permission
        # prompt. Keep it specific — generic text gets denied more often.
        "NSMicrophoneUsageDescription": (
            "MIRA listens for the wake word and transcribes your "
            "voice commands locally and via Deepgram."
        ),
        "NSAppleEventsUsageDescription": (
            "MIRA may script other apps to carry out actions you ask for."
        ),
        "LSMinimumSystemVersion": "12.0",
    },
    # Excluding modules py2app would otherwise bundle but we never import
    # at runtime. Each saves ~10-40MB from the final bundle.
    "excludes": [
        "tkinter",
        "matplotlib",
        "pandas",
        "PyQt5",
        "PyQt6",
        "PySide6",
        "IPython",
        "pytest",
        # playwright ships PyInstaller hook modules that modulegraph can't
        # resolve. Excluding the hook subpackage lets the rest of playwright
        # bundle cleanly; the hooks are PyInstaller-only anyway.
        "playwright._impl.__pyinstaller",
    ],
}


setup(
    app=APP,
    name="MIRA Daemon",
    options={"py2app": OPTIONS},
    # py2app blows up when pyproject.toml's dependencies propagate as
    # install_requires. We've already installed deps into the venv; override
    # to empty so py2app's sanity check passes.
    install_requires=[],
)
