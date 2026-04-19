from __future__ import annotations

import asyncio
import subprocess
import time
from typing import Any

from pydantic import BaseModel, Field

from mira.runtime.registry import tool

# Fuzzy name matching — the reason this file exists. Voice transcripts
# drop consonants ("Shri" → "Shrey"), swap similar sounds ("Tam" → "Sam"),
# and mangle foreign names. A strict substring match against Contacts.app
# is too brittle for voice. We pull every contact once, cache in-process,
# and score each query with rapidfuzz.WRatio — which handles short-name,
# transposition, and partial-token cases in one scorer.

# Long TTL because the full-fetch AppleScript walks every Contacts.app
# entry and can take 5-30s for a big address book. Users rarely add
# contacts mid-session; we'd rather eat a restart than pay that cost on
# every voice turn.
_ALL_TTL_S = 600.0
_all_cache: tuple[float, list[dict[str, Any]]] | None = None
_all_lock = asyncio.Lock()

# Scores below this are almost certainly garbage. 65 keeps "Tam"→"Sam"
# (68) and "Shri"→"Shrey" (77) in, but drops the WRatio floor-noise
# where unrelated short names all score exactly 60.
_MIN_SCORE = 65.0


_ALL_SCRIPT = r'''
tell application "Contacts"
    set outList to {}
    repeat with p in every person
        set pname to name of p as string
        set phs to {}
        repeat with ph in phones of p
            set end of phs to (value of ph as string)
        end repeat
        set ems to {}
        repeat with em in emails of p
            set end of ems to (value of em as string)
        end repeat
        set joinedPhones to my joinList(phs)
        set joinedEmails to my joinList(ems)
        set end of outList to pname & "||" & joinedPhones & "||" & joinedEmails
    end repeat
end tell
set AppleScript's text item delimiters to linefeed
return outList as string

on joinList(lst)
    set AppleScript's text item delimiters to ","
    set s to lst as string
    set AppleScript's text item delimiters to ""
    return s
end joinList
'''


def _run_applescript(script: str) -> tuple[bool, str]:
    try:
        # 60s ceiling — big address books are slow. Anything past that is
        # a permissions / hang situation.
        res = subprocess.run(
            ["osascript", "-"],
            input=script, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "osascript timed out"
    if res.returncode != 0:
        return False, (res.stderr or "").strip() or "osascript failed"
    return True, (res.stdout or "").strip()


def _parse_all(stdout: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in stdout.splitlines() if stdout else []:
        parts = line.split("||")
        if len(parts) < 3:
            continue
        name, phones, emails = parts[0], parts[1], parts[2]
        if not name.strip():
            continue
        items.append({
            "name": name,
            "phones": [p for p in phones.split(",") if p],
            "emails": [e for e in emails.split(",") if e],
        })
    return items


async def _load_all(force: bool = False) -> list[dict[str, Any]] | None:
    global _all_cache
    now = time.time()
    if not force and _all_cache is not None and now - _all_cache[0] < _ALL_TTL_S:
        return _all_cache[1]
    async with _all_lock:
        if not force and _all_cache is not None and now - _all_cache[0] < _ALL_TTL_S:
            return _all_cache[1]
        ok, out = await asyncio.to_thread(_run_applescript, _ALL_SCRIPT)
        if not ok:
            return None
        items = _parse_all(out)
        _all_cache = (now, items)
        return items


class LookupArgs(BaseModel):
    name: str = Field(..., description="Name or partial name to search for.")


def _summarize_lookup(data: Any, *, max_items: int = 5) -> str:
    if not isinstance(data, dict):
        return str(data)
    if not data.get("ok"):
        return f"contacts error: {data.get('error') or 'unknown'}"
    items = data.get("matches") or []
    count = data.get("count", len(items))
    if not items:
        return "no matches."
    parts: list[str] = [f"{count} match{'es' if count != 1 else ''}:"]
    for m in items[:max_items]:
        name = (m.get("name") or "").strip() or "(unnamed)"
        phones = m.get("phones") or []
        emails = m.get("emails") or []
        tail: list[str] = []
        if phones:
            tail.append(phones[0])
        if emails:
            tail.append(emails[0])
        score = m.get("score")
        score_tag = f" [{int(score)}]" if isinstance(score, (int, float)) else ""
        line = (name + score_tag) if not tail else f"{name}{score_tag} — {', '.join(tail)}"
        parts.append(line)
    if len(items) > max_items:
        parts.append(f"...+{len(items) - max_items} more")
    return " | ".join(parts)


def _rank(query: str, contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score every contact against the query and return the best. Uses
    WRatio because it handles both partial and transposed matches; we
    then boost exact-substring hits so 'Sam' still picks Sam over Samuel."""
    from rapidfuzz import fuzz

    q = query.strip()
    q_lower = q.lower()
    scored: list[tuple[float, dict[str, Any]]] = []
    for c in contacts:
        name = c.get("name") or ""
        if not name:
            continue
        score = fuzz.WRatio(q, name)
        # Substring boost: ensures exact containment beats fuzzy near-misses
        # without wiping them out (we still want "Shri" → "Shrey" to show
        # up when there's no exact "Shri" in the book).
        if q_lower in name.lower():
            score = min(100.0, score + 10.0)
        if score >= _MIN_SCORE:
            scored.append((score, {**c, "score": round(score, 1)}))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _, c in scored[:10]]


@tool(
    "contacts.lookup",
    description=(
        "Resolve a person's name (or a voice-transcribed approximation) "
        "to their phone numbers and email addresses from macOS Contacts.app. "
        "Uses fuzzy matching — 'Tam' still finds 'Sam', 'Shri' finds 'Shrey'. "
        "Returns the top candidates with match scores; the caller decides "
        "which one to use. If multiple strong matches exist, ask the user."
    ),
    params=LookupArgs,
    tags=("contacts",),
    summarizer=_summarize_lookup,
)
async def contacts_lookup(args: LookupArgs) -> dict[str, Any]:
    contacts = await _load_all()
    if contacts is None:
        return {
            "ok": False,
            "error": "no access — grant Contacts permission in System Settings",
        }
    matches = _rank(args.name, contacts)
    return {"ok": True, "count": len(matches), "matches": matches}
