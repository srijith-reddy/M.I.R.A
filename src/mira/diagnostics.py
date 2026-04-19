from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable

from mira.config.paths import paths
from mira.config.settings import get_settings


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _check_key(name: str, value: str | None) -> Check:
    return Check(
        name=name,
        ok=bool(value),
        detail="set" if value else "missing",
    )


def _check_import(module: str) -> Check:
    try:
        importlib.import_module(module)
        return Check(name=f"import {module}", ok=True, detail="ok")
    except Exception as exc:
        return Check(name=f"import {module}", ok=False, detail=repr(exc))


def _check_paths() -> Check:
    try:
        paths.ensure()
        writable = paths.data_dir.is_dir() and paths.logs_dir.is_dir()
        return Check(
            name="paths",
            ok=writable,
            detail=f"data={paths.data_dir}",
        )
    except Exception as exc:
        return Check(name="paths", ok=False, detail=repr(exc))


def _check_sqlite() -> Check:
    try:
        from mira.runtime.store import connect

        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return Check(name="sqlite", ok=True, detail=str(paths.sqlite_db))
    except Exception as exc:
        return Check(name="sqlite", ok=False, detail=repr(exc))


def _check_audio_devices() -> Check:
    try:
        import sounddevice as sd

        devs = sd.query_devices()
        inputs = [d for d in devs if d.get("max_input_channels", 0) > 0]
        return Check(
            name="audio",
            ok=bool(inputs),
            detail=f"{len(inputs)} input device(s)",
        )
    except Exception as exc:
        return Check(name="audio", ok=False, detail=repr(exc))


def _check_ui_bridge_port() -> Check:
    """The SwiftUI HUD connects on `ui_bridge_port` (loopback). If the port
    is already in use — stale zombie, another MIRA instance, or a user app
    that happened to grab it — `websockets.serve` will OSError at boot and
    the HUD will sit stuck on "Waiting for MIRA…". Catching it in doctor
    gives a clean error message before the user wonders why the orb is
    grey."""
    import socket

    port = get_settings().ui_bridge_port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", port))
        return Check(name="ui_bridge_port", ok=True, detail=f"127.0.0.1:{port} free")
    except OSError as exc:
        return Check(
            name="ui_bridge_port",
            ok=False,
            detail=f"127.0.0.1:{port} in use ({exc.strerror or exc})",
        )
    finally:
        sock.close()


def _check_porcupine_keyword() -> Check:
    """Make sure Porcupine can locate its built-in keyword assets. Boot fails
    late and loud if the wheel is missing these, so catch it here instead."""
    try:
        import pvporcupine

        kws = pvporcupine.KEYWORDS  # set of built-in keyword names
        return Check(
            name="porcupine",
            ok="jarvis" in kws,
            detail=f"keywords={sorted(kws)[:5]}...",
        )
    except Exception as exc:
        return Check(name="porcupine", ok=False, detail=repr(exc))


_CHECKS: list[Callable[[], Check]] = [
    _check_paths,
    _check_sqlite,
    lambda: _check_key("CARTESIA_API_KEY", get_settings().cartesia_api_key),
    lambda: _check_key("CARTESIA_VOICE", get_settings().cartesia_voice),
    lambda: _check_key("OPENAI_API_KEY", get_settings().openai_api_key),
    lambda: _check_key("DEEPSEEK_API_KEY", get_settings().deepseek_api_key),
    lambda: _check_key("DEEPGRAM_API_KEY", get_settings().deepgram_api_key),
    lambda: _check_key("PICOVOICE_ACCESS_KEY", get_settings().picovoice_access_key),
    lambda: _check_key("BRAVE_SEARCH_API_KEY", get_settings().brave_search_api_key),
    lambda: _check_key("ANTHROPIC_API_KEY", get_settings().anthropic_api_key),
    lambda: _check_key("GROQ_API_KEY", get_settings().groq_api_key),
    lambda: _check_import("openai"),
    lambda: _check_import("cartesia"),
    lambda: _check_import("pvporcupine"),
    lambda: _check_import("pvrecorder"),
    lambda: _check_import("sounddevice"),
    lambda: _check_import("webrtcvad"),
    lambda: _check_import("playwright"),
    lambda: _check_import("rumps"),
    _check_audio_devices,
    _check_porcupine_keyword,
    _check_ui_bridge_port,
]


def run_all() -> list[Check]:
    """Run every diagnostic and return the full list. Caller decides how to
    render — the library does not print so tests/UIs can format their way."""
    return [c() for c in _CHECKS]


def format_report(checks: list[Check]) -> str:
    """Render. A check is only blocking if the runtime actually needs it
    at this config. Concretely: we consult `missing_required_keys()` for
    the final word on required API keys — that way doctor never reports a
    blocker that `mira --run` would happily boot past."""
    settings = get_settings()
    runtime_missing = set(settings.missing_required_keys())
    # Translate "OPENAI_API_KEY or DEEPSEEK_API_KEY" back into the individual
    # check names that might be shown as FAIL — if either is set, neither
    # should be blocking.
    planner_ok = bool(settings.openai_api_key or settings.deepseek_api_key)
    stt_ok = bool(settings.deepgram_api_key or settings.openai_api_key)
    # Wakeword backend decides whether Picovoice is required.
    backend = (settings.wakeword_backend or "auto").lower()
    picovoice_required = backend == "porcupine" or (
        backend == "auto" and bool(settings.picovoice_access_key)
    )
    optional_keys = {
        "ANTHROPIC_API_KEY", "GROQ_API_KEY", "BRAVE_SEARCH_API_KEY",
    }

    def _is_blocker(c: Check) -> bool:
        if c.ok:
            return False
        if c.name in optional_keys:
            return False
        if c.name == "OPENAI_API_KEY" and planner_ok and stt_ok:
            return False
        if c.name == "DEEPSEEK_API_KEY" and planner_ok:
            return False
        if c.name == "DEEPGRAM_API_KEY" and stt_ok:
            return False
        if c.name == "PICOVOICE_ACCESS_KEY" and not picovoice_required:
            return False
        return True

    lines: list[str] = []
    for c in checks:
        if c.ok:
            mark = "OK  "
        elif _is_blocker(c):
            mark = "FAIL"
        else:
            mark = "WARN"
        lines.append(f"  [{mark}] {c.name}: {c.detail}")
    blockers = [c for c in checks if _is_blocker(c)]
    lines.append("")
    if blockers:
        lines.append(f"{len(blockers)} blocking issue(s).")
    elif runtime_missing:
        lines.append(f"{len(runtime_missing)} blocking issue(s): {', '.join(runtime_missing)}")
    else:
        lines.append("All blocking checks passed.")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    # Ground truth: do we have what missing_required_keys() actually needs?
    # Import checks and filesystem checks are still blockers on their own.
    settings = get_settings()
    if settings.missing_required_keys():
        return 1
    for c in checks:
        if c.ok:
            continue
        if c.name.endswith("_API_KEY"):
            continue
        return 1
    return 0
