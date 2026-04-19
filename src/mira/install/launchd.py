from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mira.config.paths import paths

# Manage a per-user LaunchAgent that boots the MIRA daemon at login.
#
# Design choices:
#   * LaunchAgent (user scope) not LaunchDaemon (root). MIRA speaks through
#     CoreAudio / accesses the user's Keychain / renders a menu-bar item —
#     all of which require the user session, not a privileged daemon.
#   * `RunAtLoad=True` + `KeepAlive=True`: start at login, restart if the
#     process exits for any reason (crash, user quit from the dock rumps
#     entry, etc). A misbehaving build can be stopped via `mira uninstall`
#     so we don't pin the user into a restart loop.
#   * stdout/stderr redirected to `<logs_dir>/daemon.stdout.log|.stderr.log`.
#     launchd ships structured logs elsewhere but the two flat files make
#     grep-based triage trivial.
#
# We intentionally don't install system-wide. Each user signs up via their
# own `mira install` invocation.


LABEL = "com.mira.agent"
_PLIST_BASENAME = f"{LABEL}.plist"


@dataclass(frozen=True)
class LaunchAgentPlan:
    plist_path: Path
    program: list[str]
    stdout_path: Path
    stderr_path: Path
    working_dir: Path


def plan() -> LaunchAgentPlan:
    """Work out where the plist should live and which command it should run.

    If we're running inside a built `MIRA.app` bundle (frozen=True), we point
    at the app's internal `MacOS/MIRA` executable so launchd can route
    app-lifecycle signals properly. Otherwise we fall back to the current
    `python -m mira daemon`, which is the dev-mode path."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / _PLIST_BASENAME

    # getattr to placate type checkers; `sys.frozen` is only set by py2app/
    # PyInstaller at runtime.
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        exe = Path(sys.executable).resolve()
        program = [str(exe), "daemon"]
        cwd = exe.parent
    else:
        python = Path(sys.executable).resolve()
        program = [str(python), "-m", "mira", "daemon"]
        cwd = Path.cwd()

    logs = paths.logs_dir
    return LaunchAgentPlan(
        plist_path=plist_path,
        program=program,
        stdout_path=logs / "daemon.stdout.log",
        stderr_path=logs / "daemon.stderr.log",
        working_dir=cwd,
    )


def _render_plist(p: LaunchAgentPlan) -> bytes:
    # Propagate MIRA_* env vars from the current shell so the installing
    # user's config survives into the agent. launchd otherwise starts with
    # a near-empty env.
    env_keep = {
        k: v for k, v in os.environ.items()
        if k.startswith("MIRA_") or k in ("HOME", "USER", "PATH", "LANG", "LC_ALL")
    }
    body: dict[str, object] = {
        "Label": LABEL,
        "ProgramArguments": p.program,
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(p.working_dir),
        "StandardOutPath": str(p.stdout_path),
        "StandardErrorPath": str(p.stderr_path),
        "ProcessType": "Interactive",  # allows AppKit / mic access
        "EnvironmentVariables": env_keep,
    }
    return plistlib.dumps(body)


def _launchctl(*args: str) -> tuple[int, str]:
    """Run `launchctl` with the given args. Returns (rc, combined-output).

    We swallow non-zero exits here because `launchctl unload` on a plist
    that isn't loaded is a no-op we'd like to treat as success."""
    if shutil.which("launchctl") is None:
        return 127, "launchctl not found (not running on macOS?)"
    proc = subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def install() -> int:
    """Write the plist and load it. Returns exit code."""
    p = plan()
    p.stdout_path.parent.mkdir(parents=True, exist_ok=True)
    p.plist_path.parent.mkdir(parents=True, exist_ok=True)

    # Unload any previous version first so a reinstall picks up edits.
    if p.plist_path.exists():
        _launchctl("unload", str(p.plist_path))

    p.plist_path.write_bytes(_render_plist(p))
    print(f"Wrote {p.plist_path}")

    rc, out = _launchctl("load", "-w", str(p.plist_path))
    if rc != 0:
        print(f"launchctl load failed ({rc}): {out}")
        return rc
    print(f"Loaded {LABEL}. Logs: {p.stdout_path} / {p.stderr_path}")
    return 0


def uninstall() -> int:
    """Unload and delete the plist. No-op if it was never installed."""
    p = plan()
    if p.plist_path.exists():
        _launchctl("unload", str(p.plist_path))
        p.plist_path.unlink()
        print(f"Removed {p.plist_path}")
    else:
        print("LaunchAgent not installed; nothing to do.")
    return 0


def status() -> int:
    """Report whether the LaunchAgent is loaded."""
    p = plan()
    if not p.plist_path.exists():
        print(f"LaunchAgent not installed. Plist would live at: {p.plist_path}")
        return 1
    rc, out = _launchctl("list", LABEL)
    if rc == 0:
        print(f"LaunchAgent {LABEL} is loaded.")
        print(out)
        return 0
    print(f"LaunchAgent {LABEL} plist exists but is not loaded.")
    return 2
