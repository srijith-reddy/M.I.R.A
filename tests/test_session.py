from __future__ import annotations

import time

import pytest

from mira.runtime.schemas import ToolCall
from mira.runtime.session import (
    PENDING_TTL_SECONDS,
    PendingConfirmation,
    classify_confirmation,
    clear_pending,
    load_pending,
    recent_turns,
    record_turn,
    set_pending,
)


@pytest.mark.parametrize(
    "utterance, expected",
    [
        ("yes", "yes"),
        ("Yeah.", "yes"),
        ("yep do it", "yes"),
        ("go ahead please", "yes"),
        ("Confirmed!", "yes"),
        ("no", "no"),
        ("Nah", "no"),
        ("cancel", "no"),
        ("wait, hold on", "no"),
        ("", "unclear"),
        ("maybe later", "unclear"),
        ("tell me the weather", "unclear"),
    ],
)
def test_classify_confirmation(utterance: str, expected: str) -> None:
    assert classify_confirmation(utterance) == expected


def _sample_pending(tool: str = "reminder.delete") -> PendingConfirmation:
    return PendingConfirmation(
        original_turn_id="turn-1",
        agent="communication",
        tool_call=ToolCall(tool=tool, args={"id": 1}, requires_confirmation=True),
        prompt="Delete reminder 1?",
    )


def test_pending_roundtrip() -> None:
    p = _sample_pending()
    set_pending(p, user_id="alice")
    loaded = load_pending("alice")
    assert loaded is not None
    assert loaded.tool_call.tool == "reminder.delete"
    assert loaded.agent == "communication"
    assert loaded.original_turn_id == "turn-1"


def test_pending_isolated_per_user() -> None:
    set_pending(_sample_pending(tool="reminder.delete"), user_id="alice")
    assert load_pending("bob") is None
    assert load_pending("alice") is not None


def test_pending_clear() -> None:
    set_pending(_sample_pending(), user_id="alice")
    clear_pending("alice")
    assert load_pending("alice") is None


def test_pending_expiry_clears_on_read() -> None:
    p = _sample_pending()
    # Stamp the creation time into the past so load_pending sees it as expired.
    p.created_at = time.time() - (PENDING_TTL_SECONDS + 1)
    set_pending(p, user_id="alice")
    assert load_pending("alice") is None
    # load_pending also wipes the row on expiry — second read should still be None.
    assert load_pending("alice") is None


def test_record_turn_caps_at_max() -> None:
    for i in range(25):
        record_turn(
            turn_id=f"t{i}",
            transcript=f"msg {i}",
            reply=f"reply {i}",
            status="done",
            via="direct:research",
            user_id="alice",
        )
    recent = recent_turns("alice", limit=50)
    assert len(recent) == 20
    # FIFO trim: oldest five dropped, newest twenty retained.
    assert recent[0].turn_id == "t5"
    assert recent[-1].turn_id == "t24"
