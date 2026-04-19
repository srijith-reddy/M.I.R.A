from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from mira.runtime.registry import tool


def _run(cmd: list[str], timeout: int = 5) -> tuple[bool, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return False, repr(exc)
    if res.returncode != 0:
        return False, (res.stderr or "").strip() or f"exit {res.returncode}"
    return True, (res.stdout or "").strip()


class AppNameArgs(BaseModel):
    name: str = Field(..., description="App name as in Applications ('Safari', 'Spotify').")


@tool(
    "app.open",
    description="Launch a macOS application by name (same as `open -a`).",
    params=AppNameArgs,
    tags=("apps",),
)
async def app_open(args: AppNameArgs) -> dict[str, Any]:
    ok, detail = await asyncio.to_thread(_run, ["open", "-a", args.name])
    return {"ok": ok, "app": args.name, "detail": detail}


@tool(
    "app.activate",
    description="Bring an already-open app to the foreground.",
    params=AppNameArgs,
    tags=("apps",),
)
async def app_activate(args: AppNameArgs) -> dict[str, Any]:
    safe = args.name.replace('"', '\\"')
    ok, detail = await asyncio.to_thread(
        _run, ["osascript", "-e", f'tell application "{safe}" to activate']
    )
    return {"ok": ok, "app": args.name, "detail": detail}


@tool(
    "app.quit",
    description="Quit a running application. Destructive — requires confirmation.",
    params=AppNameArgs,
    requires_confirmation=True,
    tags=("apps",),
)
async def app_quit(args: AppNameArgs) -> dict[str, Any]:
    safe = args.name.replace('"', '\\"')
    ok, detail = await asyncio.to_thread(
        _run, ["osascript", "-e", f'tell application "{safe}" to quit']
    )
    return {"ok": ok, "app": args.name, "detail": detail}


class EmptyArgs(BaseModel):
    pass


@tool(
    "app.frontmost",
    description="Report which app currently has the focus.",
    params=EmptyArgs,
    tags=("apps",),
)
async def app_frontmost(_: EmptyArgs) -> dict[str, Any]:
    ok, detail = await asyncio.to_thread(
        _run,
        ["osascript", "-e",
         'tell application "System Events" to get name of first application process whose frontmost is true'],
    )
    return {"ok": ok, "app": detail if ok else None}
