from __future__ import annotations

import asyncio
import glob
import os
import shutil
import signal
import subprocess
import tempfile
from typing import Any

from pydantic import BaseModel, Field

from mira.obs.logging import log_event
from mira.runtime.registry import tool


class _Player:
    """Process-level singleton wrapping the yt-dlp → ffplay pipe.

    Why the pipe: previously we called yt-dlp to resolve a direct stream
    URL, waited 2-5s for metadata + format selection, then handed the URL
    to ffplay. That whole window was silent on the user's end. Piping
    yt-dlp stdout straight into ffplay stdin lets ffplay start decoding as
    soon as the first bytes arrive — playback begins ~400-800ms after the
    command, roughly 3-5x faster perceived start time.

    The two processes are tracked together so `stop()` tears both down,
    avoiding a zombie yt-dlp that keeps downloading after the user cancels.
    SIGSTOP/SIGCONT still pauses the pipeline instantly — the kernel
    freezes both ends."""

    def __init__(self) -> None:
        self.ytdlp: subprocess.Popen | None = None
        self.ffplay: subprocess.Popen | None = None
        self.title: str | None = None
        self.query: str | None = None
        self.tmp_path: str | None = None
        self.paused: bool = False  # user-requested pause
        self.ducked: bool = False  # auto-paused under TTS
        # Default to 100 — ffplay's -volume is capped 0-100 and applies on
        # top of the system volume. 80 was pointlessly quiet given most
        # Macs run below half volume.
        self.volume: int = 100

    def is_running(self) -> bool:
        return self.ffplay is not None and self.ffplay.poll() is None

    def _kill(self, proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass

    def stop(self) -> None:
        self._kill(self.ffplay)
        self._kill(self.ytdlp)
        self.ffplay = None
        self.ytdlp = None
        if self.tmp_path:
            for p in glob.glob(self.tmp_path + "*"):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        self.tmp_path = None
        self.title = None
        self.query = None
        self.paused = False


_player = _Player()


def _install_tts_ducking() -> None:
    """Auto-pause music while TTS speaks, auto-resume when TTS ends.

    Problem this solves: ffplay holds the default CoreAudio output device.
    When TTS then tries to open a PortAudio OutputStream on the same
    device, Core Audio returns kAudioHardwareNotRunningError → PortAudio
    surfaces it as error -9986 and TTS falls silent. Ducking here frees
    the device for the duration of the reply, then resumes playback.
    """
    try:
        from mira.runtime.bus import bus

        async def _on_tts_start(_topic: str, _payload: dict) -> None:
            if _player.is_running() and not _player.paused and not _player.ducked:
                if _signal_pipeline(signal.SIGSTOP):
                    _player.ducked = True
                    log_event("music.ducked")

        async def _on_tts_end(_topic: str, _payload: dict) -> None:
            if _player.ducked:
                _signal_pipeline(signal.SIGCONT)
                _player.ducked = False
                log_event("music.unducked")

        bus().subscribe("tts.started", _on_tts_start)
        bus().subscribe("tts.ended", _on_tts_end)
    except Exception as exc:
        log_event("music.ducking_install_error", error=repr(exc))


_install_tts_ducking()


def _start_pipe(query: str, volume: int) -> tuple[str, subprocess.Popen, str]:
    """Download `query` to a temp file via yt-dlp, then play with ffplay.

    Why not stream: we tried two streaming strategies and both failed:
      1. `yt-dlp -o - | ffplay -i pipe:0` — webm/opus and m4a both need
         seekable input to find the moov atom / cluster index. Pipe input
         fails with "Invalid data found when processing input".
      2. `yt-dlp --get-url` + `ffplay URL` — YouTube's signed URLs are
         bound to the client headers yt-dlp used when resolving. ffplay's
         plain HTTP client gets 403 Forbidden on every fetch.

    Download-to-file takes an extra 1-3s but it's the only path that
    actually plays audio. Temp file is tracked on the player and deleted
    in stop()."""
    target = query.strip()
    if not (target.startswith("http://") or target.startswith("https://")):
        target = f"ytsearch1:{target}"

    if shutil.which("yt-dlp") is None:
        raise RuntimeError("yt-dlp not installed (brew install yt-dlp)")
    if shutil.which("ffplay") is None:
        raise RuntimeError("ffplay not installed (brew install ffmpeg)")

    # Download to a predictable base path; yt-dlp appends the real ext.
    # We glob the base later to locate the final file.
    base = tempfile.mktemp(prefix="mira_music_")
    out_tmpl = base + ".%(ext)s"

    # `--print title` alone makes yt-dlp behave like --simulate and skip
    # the download. Adding an `after_move:filepath` print forces it to run
    # the download pipeline, and gives us back the real on-disk path in
    # one step (no globbing for the guessed extension).
    result = subprocess.run(
        [
            "yt-dlp",
            "--no-warnings",
            "-f", "ba[ext=m4a]/bestaudio/best",
            "--no-playlist",
            "--print", "title",
            "--print", "after_move:filepath",
            "-o", out_tmpl,
            target,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        err = (result.stderr or "").strip().splitlines()
        reason = err[-1] if err else "yt-dlp failed"
        raise RuntimeError(reason[:300])

    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"yt-dlp output malformed: {result.stdout[:200]}")
    title = lines[0]
    audio_path = lines[-1]
    if not os.path.exists(audio_path):
        raise RuntimeError("yt-dlp reported path but file missing")

    ffplay = subprocess.Popen(
        [
            "ffplay", "-nodisp", "-autoexit", "-loglevel", "warning",
            "-volume", str(volume),
            "-i", audio_path,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return audio_path, ffplay, title


class PlayArgs(BaseModel):
    query: str = Field(..., description="Song, artist, or YouTube URL.")


@tool(
    "music.play",
    description=(
        "Play music by search query or YouTube URL. Pipes yt-dlp audio into "
        "ffplay so playback starts within ~1s instead of waiting for the "
        "full resolve. Stops any currently-playing track first."
    ),
    params=PlayArgs,
    tags=("music",),
)
async def music_play(args: PlayArgs) -> dict[str, Any]:
    _player.stop()
    log_event("music.play_requested", query=args.query)
    try:
        audio_path, ffplay, title = await asyncio.to_thread(
            _start_pipe, args.query, _player.volume
        )
    except Exception as exc:
        log_event("music.start_error", error=repr(exc), query=args.query)
        return {"ok": False, "error": f"could not start playback: {exc}"}

    await asyncio.sleep(0.8)
    if ffplay.poll() is not None and ffplay.returncode != 0:
        err = b""
        try:
            err = ffplay.stderr.read() if ffplay.stderr else b""
        except Exception:
            pass
        reason = (err.decode("utf-8", errors="ignore").strip().splitlines() or ["unknown"])[-1][:200]
        log_event("music.ffplay_failed", query=args.query, reason=reason)
        try:
            os.unlink(audio_path)
        except OSError:
            pass
        return {"ok": False, "error": f"couldn't play audio: {reason}"}

    _player.ytdlp = None
    _player.ffplay = ffplay
    _player.tmp_path = audio_path
    _player.title = title
    _player.query = args.query
    _player.paused = False
    try:
        from mira.ui.media_keys import set_now_playing
        set_now_playing(title)
    except Exception:
        pass
    log_event("music.play_started", query=args.query, title=title)
    # `silent: True` tells the agent layer to skip the spoken confirmation.
    # The music starting *is* the confirmation — narrating "playing X" just
    # talks over the audio the user asked for.
    return {"ok": True, "title": title, "status": "playing", "silent": True}


class EmptyArgs(BaseModel):
    pass


def _signal_pipeline(sig: int) -> bool:
    """Send `sig` to both pipeline procs. Returns True if either survived
    long enough to accept it. Snapshots the attrs into locals first — the
    procs may be cleared by `stop()` on another task between checks."""
    ff = _player.ffplay
    yt = _player.ytdlp
    delivered = False
    for proc in (ff, yt):
        if proc is None:
            continue
        try:
            if proc.poll() is None:
                os.kill(proc.pid, sig)
                delivered = True
        except (ProcessLookupError, OSError):
            # Child exited between poll() and kill() — harmless; the other
            # half of the pipe will notice and shut down on its own.
            continue
    return delivered


@tool("music.pause", description="Pause the currently-playing track.",
      params=EmptyArgs, tags=("music",))
async def music_pause(_: EmptyArgs) -> dict[str, Any]:
    if not _player.is_running():
        return {"ok": False, "error": "nothing playing"}
    if _player.paused:
        return {"ok": True, "status": "already paused", "silent": True}
    if not _signal_pipeline(signal.SIGSTOP):
        return {"ok": False, "error": "playback ended"}
    _player.paused = True
    try:
        from mira.ui.media_keys import set_paused
        set_paused(True)
    except Exception:
        pass
    return {"ok": True, "status": "paused", "title": _player.title, "silent": True}


@tool("music.resume", description="Resume a paused track.",
      params=EmptyArgs, tags=("music",))
async def music_resume(_: EmptyArgs) -> dict[str, Any]:
    if not _player.is_running():
        return {"ok": False, "error": "nothing playing"}
    if not _player.paused:
        return {"ok": True, "status": "already playing", "silent": True}
    if not _signal_pipeline(signal.SIGCONT):
        return {"ok": False, "error": "playback ended"}
    _player.paused = False
    try:
        from mira.ui.media_keys import set_paused
        set_paused(False)
    except Exception:
        pass
    return {"ok": True, "status": "playing", "title": _player.title, "silent": True}


@tool("music.stop", description="Stop playback entirely.",
      params=EmptyArgs, tags=("music",))
async def music_stop(_: EmptyArgs) -> dict[str, Any]:
    was = _player.title
    _player.stop()
    try:
        from mira.ui.media_keys import set_now_playing
        set_now_playing(None)
    except Exception:
        pass
    return {"ok": True, "stopped": was, "silent": True}


class VolumeArgs(BaseModel):
    level: int = Field(..., ge=0, le=100, description="ffplay volume 0-100.")


@tool(
    "music.volume",
    description=(
        "Set the music player volume (0-100). ffplay's per-instance volume "
        "is fixed at launch, so this takes effect on the NEXT track. For "
        "immediate change, use `system.volume_set` instead."
    ),
    params=VolumeArgs,
    tags=("music",),
)
async def music_volume(args: VolumeArgs) -> dict[str, Any]:
    _player.volume = args.level
    return {"ok": True, "level": args.level, "applies_to": "next track"}


@tool("music.status", description="Report what's playing, if anything.",
      params=EmptyArgs, tags=("music",))
async def music_status(_: EmptyArgs) -> dict[str, Any]:
    if not _player.is_running():
        return {"playing": False}
    return {
        "playing": True,
        "paused": _player.paused,
        "title": _player.title,
        "volume": _player.volume,
    }
