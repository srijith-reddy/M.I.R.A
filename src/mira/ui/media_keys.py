"""Hardware media-key control for MIRA's music player.

Wires Mac play/pause (⏯ key, AirPods squeeze, Control Center, Apple Watch
"Now Playing") to music_pause / music_resume. Uses MPRemoteCommandCenter —
the same API Spotify and Music.app use — so macOS routes the key to
whichever app most recently claimed "now playing." When MIRA starts a
track we update MPNowPlayingInfoCenter, which claims focus.

Why this over a CGEventTap: no Accessibility permission prompt, plays
nicely with AirPods/headphone controls and the Control Center panel, and
the OS mediates focus between MIRA and Spotify for free.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mira.obs.logging import log_event


_registered = False
_loop: asyncio.AbstractEventLoop | None = None
_handler_refs: list[Any] = []  # keep block refs alive across the ObjC boundary


async def _toggle() -> None:
    from mira.tools.music_tools import _player, music_pause, music_resume
    if not _player.is_running():
        return
    if _player.paused:
        await music_resume(None)  # type: ignore[arg-type]
    else:
        await music_pause(None)  # type: ignore[arg-type]


async def _play_action() -> None:
    from mira.tools.music_tools import _player, music_resume
    if _player.is_running() and _player.paused:
        await music_resume(None)  # type: ignore[arg-type]


async def _pause_action() -> None:
    from mira.tools.music_tools import _player, music_pause
    if _player.is_running() and not _player.paused:
        await music_pause(None)  # type: ignore[arg-type]


async def _stop_action() -> None:
    from mira.tools.music_tools import music_stop
    await music_stop(None)  # type: ignore[arg-type]


def install(loop: asyncio.AbstractEventLoop) -> bool:
    """Register play/pause/stop remote command targets. Idempotent — safe
    to call multiple times. Returns True on success."""
    global _registered, _loop
    if _registered:
        return True
    try:
        import objc
        from Foundation import NSBundle, NSObject

        mp_bundle = NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/MediaPlayer.framework"
        )
        if mp_bundle is None or not mp_bundle.load():
            log_event("media_keys.framework_missing")
            return False

        MPRemoteCommandCenter = objc.lookUpClass("MPRemoteCommandCenter")
    except Exception as exc:
        log_event("media_keys.import_failed", error=repr(exc))
        return False

    _loop = loop

    # The action method must declare it returns MPRemoteCommandHandlerStatus
    # (NSInteger = `q` on 64-bit). PyObjC's runtime method registration
    # reads the signature from the class dict, so we attach it there rather
    # than wrapping at call time.
    class _RemoteTarget(NSObject):  # type: ignore[misc]
        def initWithCoro_(self, coro_fn):  # noqa: N802
            self = objc.super(_RemoteTarget, self).init()
            if self is None:
                return None
            self._coro_fn = coro_fn
            return self

        @objc.typedSelector(b"q@:@")
        def handleEvent_(self, _event):  # noqa: N802
            try:
                asyncio.run_coroutine_threadsafe(self._coro_fn(), _loop)
            except Exception as exc:
                log_event("media_keys.dispatch_error", error=repr(exc))
            return 0  # MPRemoteCommandHandlerStatusSuccess

    center = MPRemoteCommandCenter.sharedCommandCenter()
    bindings = [
        (center.togglePlayPauseCommand(), _toggle),
        (center.playCommand(), _play_action),
        (center.pauseCommand(), _pause_action),
        (center.stopCommand(), _stop_action),
    ]
    for cmd, coro in bindings:
        target = _RemoteTarget.alloc().initWithCoro_(coro)
        _handler_refs.append(target)  # retain — cmd holds weak ref
        cmd.setEnabled_(True)
        cmd.addTarget_action_(target, b"handleEvent:")

    _registered = True
    log_event("media_keys.installed")
    return True


def set_now_playing(title: str | None) -> None:
    """Tell macOS MIRA is the current "Now Playing" app (or clear it).

    Setting this claims the media-key focus — next ⏯ press routes to us
    instead of Spotify/Music. Title shows up in Control Center's Now
    Playing panel and on the Apple Watch.
    """
    try:
        import objc
        from Foundation import NSBundle

        mp_bundle = NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/MediaPlayer.framework"
        )
        if mp_bundle is None or not mp_bundle.load():
            return
        MPNowPlayingInfoCenter = objc.lookUpClass("MPNowPlayingInfoCenter")
    except Exception:
        return

    try:
        center = MPNowPlayingInfoCenter.defaultCenter()
        if title is None:
            center.setNowPlayingInfo_(None)
            # 0 = MPNowPlayingPlaybackStateStopped
            center.setPlaybackState_(0)
            return
        info = {
            "MPMediaItemPropertyTitle": title,
            "MPMediaItemPropertyArtist": "MIRA",
        }
        center.setNowPlayingInfo_(info)
        # 1 = MPNowPlayingPlaybackStatePlaying
        center.setPlaybackState_(1)
    except Exception as exc:
        log_event("media_keys.now_playing_error", error=repr(exc))


def set_paused(paused: bool) -> None:
    """Update the playback-state indicator so Control Center shows the
    right ▶/⏸ glyph."""
    try:
        import objc
        from Foundation import NSBundle

        mp_bundle = NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/MediaPlayer.framework"
        )
        if mp_bundle is None or not mp_bundle.load():
            return
        MPNowPlayingInfoCenter = objc.lookUpClass("MPNowPlayingInfoCenter")
    except Exception:
        return

    try:
        center = MPNowPlayingInfoCenter.defaultCenter()
        # 1 = Playing, 2 = Paused.
        center.setPlaybackState_(2 if paused else 1)
    except Exception:
        pass
