from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mira.runtime.memory import memory
from mira.runtime.registry import tool


class RememberArgs(BaseModel):
    key: str = Field(
        ...,
        description=(
            "Stable short identifier for the fact (e.g. 'user_name', "
            "'home_city', 'coffee_preference'). Snake-case, no spaces."
        ),
    )
    value: str = Field(..., description="The fact itself, as the user stated it.")


class RecallArgs(BaseModel):
    query: str = Field(..., description="What to look up in prior conversation.")
    k: int = Field(default=5, ge=1, le=20)


class ForgetArgs(BaseModel):
    key: str = Field(..., description="Profile key to delete.")


class ForgetEpisodeArgs(BaseModel):
    id: int = Field(..., ge=1, description="Episode id to delete.")


@tool(
    "memory.remember",
    description=(
        "Store a stable fact about the user (name, preferences, locations). "
        "Overwrites any prior value at this key."
    ),
    params=RememberArgs,
    tags=("memory",),
)
async def memory_remember(args: RememberArgs) -> dict[str, Any]:
    # Profile is key/value, not append — remembering the same fact twice is
    # correct semantics, not an error. We return the pre-existing value so
    # the caller (agent) can decide whether to confirm an overwrite.
    prior = memory().get_profile(args.key)
    memory().set_profile(args.key, args.value.strip())
    return {
        "key": args.key,
        "value": args.value.strip(),
        "prior_value": prior,
        "overwritten": prior is not None and prior != args.value.strip(),
    }


def _summarize_recall(data: Any, *, max_items: int = 5) -> str:
    if not isinstance(data, dict):
        return str(data)
    items = data.get("items") or []
    profile = data.get("profile") or {}
    parts: list[str] = []
    if profile:
        # Key=value pairs on one line — the planner will cherry-pick what matters.
        prof_line = ", ".join(f"{k}={v}" for k, v in list(profile.items())[:10])
        parts.append(f"profile: {prof_line}")
    if not items:
        parts.append("no prior turns matched.")
        return " | ".join(parts)
    parts.append(f"{len(items)} prior turn{'s' if len(items) != 1 else ''}:")
    for ep in items[:max_items]:
        t = (ep.get("transcript") or "").strip().replace("\n", " ")
        r = (ep.get("reply") or "").strip().replace("\n", " ")
        if len(t) > 80:
            t = t[:80] + "..."
        if len(r) > 80:
            r = r[:80] + "..."
        eid = ep.get("id")
        parts.append(f"[{eid}] {t} -> {r}")
    if len(items) > max_items:
        parts.append(f"...+{len(items) - max_items} more")
    return " | ".join(parts)


@tool(
    "memory.recall",
    description=(
        "Search prior conversation turns for a query. Returns top-k matches "
        "with transcript + reply. Read-only."
    ),
    params=RecallArgs,
    tags=("memory",),
    summarizer=_summarize_recall,
)
async def memory_recall(args: RecallArgs) -> dict[str, Any]:
    episodes = memory().recall(args.query, k=args.k)
    items = [
        {
            "id": ep.id,
            "ts": ep.ts,
            "transcript": ep.transcript,
            "reply": ep.reply,
            "via": ep.via,
            "score": ep.score,
        }
        for ep in episodes
    ]
    # Also surface profile facts so a "what do you know about me?" kind of
    # query returns both tiers in one tool call instead of forcing two.
    return {"count": len(items), "items": items, "profile": memory().list_profile()}


@tool(
    "memory.forget",
    description=(
        "Delete a stored profile fact. Destructive — requires confirmation. "
        "Does not affect episode history; use this only for profile keys."
    ),
    params=ForgetArgs,
    requires_confirmation=True,
    tags=("memory",),
    success_phrase="Forgotten.",
)
async def memory_forget(args: ForgetArgs) -> dict[str, Any]:
    prior = memory().get_profile(args.key)
    if prior is None:
        return {"key": args.key, "deleted": False}
    from mira.runtime.store import connect

    with connect() as conn:
        conn.execute("DELETE FROM profile WHERE key = ?", (args.key,))
    return {"key": args.key, "deleted": True, "prior_value": prior}


@tool(
    "memory.forget_episode",
    description=(
        "Delete a single episode (one past turn) by id. Use when recall "
        "surfaced a wrong or embarrassing past turn the user wants erased. "
        "Destructive — requires confirmation."
    ),
    params=ForgetEpisodeArgs,
    requires_confirmation=True,
    tags=("memory",),
)
async def memory_forget_episode(args: ForgetEpisodeArgs) -> dict[str, Any]:
    removed = memory().forget_episode(args.id)
    return {"id": args.id, "deleted": removed}
