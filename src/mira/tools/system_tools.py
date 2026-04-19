from __future__ import annotations

import asyncio
import subprocess
from typing import Any, Literal

from pydantic import BaseModel, Field

from mira.runtime.registry import tool


def _osascript(script: str) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return False, "timed out"
    if res.returncode != 0:
        return False, (res.stderr or "osascript failed").strip()
    return True, (res.stdout or "").strip()


class VolumeArgs(BaseModel):
    level: int = Field(..., ge=0, le=100, description="Output volume 0-100.")


@tool(
    "system.volume_set",
    description="Set the system output volume (0-100).",
    params=VolumeArgs,
    tags=("system",),
)
async def system_volume_set(args: VolumeArgs) -> dict[str, Any]:
    ok, out = await asyncio.to_thread(
        _osascript, f"set volume output volume {args.level}"
    )
    return {"ok": ok, "level": args.level, "detail": out}


class EmptyArgs(BaseModel):
    pass


@tool(
    "system.volume_get",
    description="Read the current system output volume (0-100).",
    params=EmptyArgs,
    tags=("system",),
)
async def system_volume_get(_: EmptyArgs) -> dict[str, Any]:
    ok, out = await asyncio.to_thread(
        _osascript, "output volume of (get volume settings)"
    )
    if not ok:
        return {"ok": False, "error": out}
    try:
        return {"ok": True, "level": int(out)}
    except ValueError:
        return {"ok": False, "error": f"unexpected output: {out}"}


class MuteArgs(BaseModel):
    muted: bool = Field(..., description="True to mute, False to unmute.")


@tool(
    "system.mute",
    description="Mute or unmute the system output.",
    params=MuteArgs,
    tags=("system",),
)
async def system_mute(args: MuteArgs) -> dict[str, Any]:
    val = "true" if args.muted else "false"
    ok, out = await asyncio.to_thread(
        _osascript, f"set volume output muted {val}"
    )
    return {"ok": ok, "muted": args.muted, "detail": out}


class BrightnessArgs(BaseModel):
    direction: Literal["up", "down"] = Field(
        ...,
        description="'up' or 'down'. macOS has no AppleScript brightness setter, "
                    "so we step with dedicated brightness key codes.",
    )
    steps: int = Field(default=4, ge=1, le=16)


@tool(
    "system.brightness",
    description=(
        "Nudge display brightness up or down. Each step is one key press "
        "(~6% change). Use `steps` to move farther in one call."
    ),
    params=BrightnessArgs,
    tags=("system",),
)
async def system_brightness(args: BrightnessArgs) -> dict[str, Any]:
    # macOS brightness key codes: 144 = up, 145 = down.
    key = 144 if args.direction == "up" else 145
    script = " ".join(
        f'tell application "System Events" to key code {key}' for _ in range(args.steps)
    )
    ok, out = await asyncio.to_thread(_osascript, script)
    return {"ok": ok, "direction": args.direction, "steps": args.steps, "detail": out}


@tool(
    "system.sleep_display",
    description="Put the display to sleep immediately (screen off).",
    params=EmptyArgs,
    tags=("system",),
)
async def system_sleep_display(_: EmptyArgs) -> dict[str, Any]:
    try:
        res = await asyncio.to_thread(
            subprocess.run, ["pmset", "displaysleepnow"],
            capture_output=True, text=True, timeout=3,
        )
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}
    return {"ok": res.returncode == 0}
