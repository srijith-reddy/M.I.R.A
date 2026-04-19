from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from mira.runtime.registry import tool


def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


class OpenArgs(BaseModel):
    path: str = Field(
        ...,
        description="File or folder path. Supports ~ and common shortcuts like 'Downloads'.",
    )


_SHORTCUTS = {
    "downloads": "~/Downloads", "desktop": "~/Desktop", "documents": "~/Documents",
    "pictures": "~/Pictures", "music": "~/Music", "movies": "~/Movies",
    "home": "~", "applications": "/Applications",
}


def _resolve(path: str) -> str:
    key = path.strip().lower().strip("/")
    if key in _SHORTCUTS:
        return _expand(_SHORTCUTS[key])
    return _expand(path)


@tool(
    "files.open",
    description=(
        "Open a file or folder in Finder / its default app. Accepts full "
        "paths, ~ shortcuts, or common names ('Downloads', 'Desktop')."
    ),
    params=OpenArgs,
    tags=("files",),
)
async def files_open(args: OpenArgs) -> dict[str, Any]:
    resolved = _resolve(args.path)
    if not os.path.exists(resolved):
        return {"ok": False, "error": f"not found: {resolved}"}
    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(["open", resolved], capture_output=True, text=True, timeout=5)
    res = await asyncio.to_thread(_run)
    return {
        "ok": res.returncode == 0,
        "path": resolved,
        "detail": (res.stderr or "").strip(),
    }


@tool(
    "files.reveal",
    description="Reveal a file/folder in Finder (selects it in its parent window).",
    params=OpenArgs,
    tags=("files",),
)
async def files_reveal(args: OpenArgs) -> dict[str, Any]:
    resolved = _resolve(args.path)
    if not os.path.exists(resolved):
        return {"ok": False, "error": f"not found: {resolved}"}
    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(["open", "-R", resolved], capture_output=True, text=True, timeout=5)
    res = await asyncio.to_thread(_run)
    return {"ok": res.returncode == 0, "path": resolved}


class SearchFilesArgs(BaseModel):
    query: str = Field(..., description="Filename fragment or Spotlight query.")
    scope: str = Field(
        default="home",
        description="'home' for ~, or an absolute path to constrain the search.",
    )
    limit: int = Field(default=15, ge=1, le=50)


def _summarize_search(data: Any, *, max_items: int = 8) -> str:
    if not isinstance(data, dict):
        return str(data)
    if not data.get("ok"):
        return f"search error: {data.get('error') or 'unknown'}"
    paths = data.get("paths") or []
    count = data.get("count", len(paths))
    if not paths:
        return "0 files."
    home = os.path.expanduser("~")
    parts: list[str] = [f"{count} file{'s' if count != 1 else ''}:"]
    for p in paths[:max_items]:
        short = p.replace(home, "~", 1) if p.startswith(home) else p
        parts.append(short)
    if len(paths) > max_items:
        parts.append(f"...+{len(paths) - max_items} more")
    return " | ".join(parts)


@tool(
    "files.search",
    description=(
        "Spotlight search for files by name or content. Returns matching "
        "paths with best matches first."
    ),
    params=SearchFilesArgs,
    tags=("files",),
    summarizer=_summarize_search,
)
async def files_search(args: SearchFilesArgs) -> dict[str, Any]:
    scope = _resolve(args.scope)
    cmd = ["mdfind", "-onlyin", scope, args.query]
    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    res = await asyncio.to_thread(_run)
    if res.returncode != 0:
        return {"ok": False, "error": (res.stderr or "mdfind failed").strip()}
    paths = [p for p in res.stdout.splitlines() if p][: args.limit]
    return {"ok": True, "count": len(paths), "paths": paths}
