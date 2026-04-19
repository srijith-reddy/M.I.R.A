"""Card schema + list-reply parser for the HUD visual layer.

A Card is a small structured payload the HUD renders next to the pill.
Two sources populate it:

1. **Agent-emitted**: an agent sets `AgentResponse.ui_payload` directly
   with a known-good card shape (richer — images, specific actions).
2. **Auto-parsed**: for agents that don't populate `ui_payload` yet, we
   try to sniff a list-like reply ("Three options: - X\n- Y\n- Z") and
   synthesize a card from the bullets. Zero agent changes required.

The shape is deliberately flat and JSON-serializable — the bus → UI
bridge stringifies it as-is and the HTML frontend renders from that
dict. No pydantic on the wire. Keep field names stable; the JS side
reads them directly.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


CardType = Literal["list", "compare", "answer", "action"]

# Kind drives the Swift UI's per-domain template selection. The Python
# side can leave it unset (Swift falls back on agent + row-shape inference)
# but setting it explicitly is the robust path — Haiku card extraction
# doesn't always produce rows that match template expectations, and a
# wrong template is worse than a generic one.
CardKind = Literal[
    "product", "source", "email", "calendar", "reminder", "action", "list"
]


@dataclass
class CardRow:
    """One row inside a card.

    Shape is loose on purpose — agents with structured data can populate
    everything; the auto-parser fills only `title`. The HUD renders what
    it finds and skips what's missing.
    """

    title: str
    subtitle: str | None = None
    trailing: str | None = None  # right-aligned value: price, rating, time
    meta: str | None = None      # third line: tiny dim caption
    url: str | None = None       # if set, row becomes clickable
    thumbnail: str | None = None # small image URL, rendered 44x44 on the left
    # Template-specific fields. Templates that don't use them ignore them.
    badge: str | None = None         # product/source: secondary tag (e.g. domain)
    rating: float | None = None      # product: 0–5 stars
    start_time: str | None = None    # calendar: "09:30"
    end_time: str | None = None      # calendar: "10:15"


@dataclass
class Card:
    card_type: CardType
    title: str
    rows: list[CardRow] = field(default_factory=list)
    subtitle: str | None = None  # one-liner under the title
    footer: str | None = None    # tiny caption at the bottom ("source: …")
    ttl_ms: int = 20000          # auto-dismiss after N ms on the HUD
    # Drives Swift template selection when set. Leave None to let the
    # renderer infer from the emitting agent + row shape.
    kind: CardKind | None = None

    def to_dict(self) -> dict[str, Any]:
        # Use asdict so nested rows serialize; filter Nones so the JS
        # doesn't have to branch on missing vs null.
        raw = asdict(self)
        raw["rows"] = [
            {k: v for k, v in r.items() if v is not None} for r in raw["rows"]
        ]
        return {k: v for k, v in raw.items() if v is not None}


# --- List auto-parser -------------------------------------------------------
#
# Matches the common shapes LLMs emit for enumerated replies:
#   - "- Item one"
#   - "* Item one"
#   - "• Item one"
#   - "1. Item one"  /  "1) Item one"
# The whole line after the marker becomes the row. We then run a light
# pattern match to pull out a trailing value (price / rating / time) so
# it renders right-aligned instead of crammed into the title.

_LIST_LINE = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$",
    re.MULTILINE,
)

# "Name — $1,299" / "Name - $1,299" / "Name: $1,299"
_TRAILING_PRICE = re.compile(
    r"^(.+?)\s*[—\-:]\s*(\$?[\d,]+(?:\.\d+)?(?:\s*(?:USD|EUR|GBP))?|\d+(?:\.\d+)?\s*(?:stars?|/10|%))\s*$"
)

# LLMs love dropping `**bold**` / `__emph__` into prose replies. The HUD
# renders plain text, so the asterisks leak through as literal characters.
# Strip them before any field goes into a row — bold has no meaning in a
# voice-assistant card.
_MARKDOWN_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1")


def _strip_markdown(text: str) -> str:
    return _MARKDOWN_EMPHASIS.sub(r"\2", text).strip()

# Opening sentence before a colon-introduced list is usually the title:
# "Here are three laptops: - X\n- Y..."
_INTRO_CAP = re.compile(r"^(.{3,120}?)[:\n]")


def _split_row(line: str) -> CardRow:
    """Turn a bullet line into a CardRow, extracting a trailing value
    when the line obviously has one. This is deliberately conservative
    — false positives here produce visually weird rows, so when in
    doubt we leave the whole thing as the title."""
    line = _strip_markdown(line)
    m = _TRAILING_PRICE.match(line)
    if m:
        return CardRow(
            title=_strip_markdown(m.group(1)),
            trailing=_strip_markdown(m.group(2)),
        )
    # "Name: detail text"
    if ": " in line:
        head, _, tail = line.partition(": ")
        if 1 <= len(head) <= 60 and len(tail) <= 120:
            return CardRow(
                title=_strip_markdown(head),
                subtitle=_strip_markdown(tail),
            )
    return CardRow(title=line)


def parse_list_reply(text: str, *, min_items: int = 2) -> Card | None:
    """Extract a ListCard from a reply that looks like a bulleted list.

    Returns None if the text doesn't have ≥ `min_items` bullets — we
    don't want to cardify "here's one option".
    """
    if not text:
        return None
    matches = _LIST_LINE.findall(text)
    if len(matches) < min_items:
        return None

    # Title = the line(s) before the first bullet, trimmed to one
    # sentence. Falls back to a generic label.
    first_bullet_idx = None
    for m in _LIST_LINE.finditer(text):
        first_bullet_idx = m.start()
        break
    preamble = text[:first_bullet_idx].strip() if first_bullet_idx else ""
    title_match = _INTRO_CAP.match(preamble)
    title = (title_match.group(1).strip() if title_match else preamble.strip()) or "Results"
    title = _strip_markdown(title)
    # Clamp title length so it doesn't eat the card width.
    if len(title) > 80:
        title = title[:77].rstrip(",;: ") + "…"

    rows = [_split_row(line) for line in matches]
    return Card(card_type="list", title=title, rows=rows)


def coerce_payload(payload: Any) -> Card | None:
    """Accept either a Card, a dict matching Card's shape, or None, and
    normalize to a Card. Used when an agent sets `ui_payload` — we don't
    know if they'll construct the dataclass or hand us a raw dict.

    Returns None on anything we can't parse; the caller treats that as
    "no card for this turn"."""
    if payload is None:
        return None
    if isinstance(payload, Card):
        return payload
    if not isinstance(payload, dict):
        return None
    try:
        raw_rows = payload.get("rows") or []
        rows = [
            CardRow(
                title=str(r.get("title", "")),
                subtitle=r.get("subtitle"),
                trailing=r.get("trailing"),
                meta=r.get("meta"),
                url=r.get("url"),
                thumbnail=r.get("thumbnail"),
                badge=r.get("badge"),
                rating=(
                    float(r["rating"]) if isinstance(r.get("rating"), (int, float)) else None
                ),
                start_time=r.get("start_time"),
                end_time=r.get("end_time"),
            )
            for r in raw_rows
            if isinstance(r, dict) and r.get("title")
        ]
        return Card(
            card_type=payload.get("card_type", "list"),
            title=str(payload.get("title", "")),
            subtitle=payload.get("subtitle"),
            footer=payload.get("footer"),
            rows=rows,
            ttl_ms=int(payload.get("ttl_ms", 20000)),
            kind=payload.get("kind"),
        )
    except Exception:
        return None
