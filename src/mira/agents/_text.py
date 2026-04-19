"""Shared text hygiene helpers for agent replies.

Agents sometimes return markdown (`**bold**`, `__emph__`) in their spoken
reply. The HUD renders plain text and TTS reads the asterisks aloud or
smushes adjacent words — either way it leaks through as noise. Strip
once centrally so every agent benefits without per-agent prompt tuning.
"""

from __future__ import annotations

import re

_MD_EMPH = re.compile(r"(\*\*|__)(.+?)\1")


def strip_markdown(text: str | None) -> str | None:
    """Remove `**bold**` and `__emph__` wrappers. Returns the input
    unchanged if it's None or has no markdown. Does NOT touch backticks,
    headings, or links — those rarely appear in spoken replies and
    stripping them would break legitimate content elsewhere."""
    if text is None:
        return None
    return _MD_EMPH.sub(r"\2", text)
