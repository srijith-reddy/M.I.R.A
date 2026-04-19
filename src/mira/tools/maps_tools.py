from __future__ import annotations

import asyncio
import subprocess
import urllib.parse
from typing import Any, Literal

from pydantic import BaseModel, Field

from mira.runtime.registry import tool


def _open_url(url: str) -> tuple[bool, str]:
    try:
        res = subprocess.run(["open", url], capture_output=True, text=True, timeout=5)
    except Exception as exc:
        return False, repr(exc)
    if res.returncode != 0:
        return False, (res.stderr or "").strip() or "open failed"
    return True, url


class DirectionsArgs(BaseModel):
    destination: str = Field(..., description="Address, place name, or 'lat,lon'.")
    origin: str | None = Field(
        default=None,
        description="Starting point. If omitted, Maps uses current location.",
    )
    mode: Literal["d", "w", "r"] = Field(
        default="d",
        description="'d' driving, 'w' walking, 'r' transit.",
    )


@tool(
    "maps.directions",
    description=(
        "Open Apple Maps with routing to the destination. Supports driving, "
        "walking, or transit. If origin is omitted, uses current location."
    ),
    params=DirectionsArgs,
    tags=("maps",),
)
async def maps_directions(args: DirectionsArgs) -> dict[str, Any]:
    params = {"daddr": args.destination, "dirflg": args.mode}
    if args.origin:
        params["saddr"] = args.origin
    url = "maps://?" + urllib.parse.urlencode(params)
    ok, detail = await asyncio.to_thread(_open_url, url)
    return {"ok": ok, "opened": url if ok else None, "detail": detail}


class SearchArgs(BaseModel):
    query: str = Field(..., description="Place or business to find.")


@tool(
    "maps.search",
    description="Open Apple Maps showing results for a place or business query.",
    params=SearchArgs,
    tags=("maps",),
)
async def maps_search(args: SearchArgs) -> dict[str, Any]:
    url = "maps://?" + urllib.parse.urlencode({"q": args.query})
    ok, detail = await asyncio.to_thread(_open_url, url)
    return {"ok": ok, "opened": url if ok else None, "detail": detail}
