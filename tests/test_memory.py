from __future__ import annotations

from typing import Iterator

import numpy as np
import pytest

from mira.runtime import memory as memory_mod
from mira.runtime.memory import MemoryStore, memory
from mira.runtime.store import connect


@pytest.fixture(autouse=True)
def _reset_memory_state() -> Iterator[None]:
    """Episodes table leaks across tests the same way session_state does.
    Wipe it on entry/exit so recall ordering is predictable."""
    with connect() as conn:
        conn.execute("DELETE FROM episodes")
        conn.execute("DELETE FROM profile")
    yield
    with connect() as conn:
        conn.execute("DELETE FROM episodes")
        conn.execute("DELETE FROM profile")


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the OpenAI embedding call with a deterministic fake so recall
    tests can assert ranking without a network dependency. The fake is
    content-addressed so two identical inputs get identical vectors."""
    def _fake_embed(self: MemoryStore, text: str) -> np.ndarray | None:
        if not text.strip():
            return None
        # Hash the text deterministically into a 1536-d vector. Similar
        # strings share prefix content → higher cosine sim.
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        vec = rng.standard_normal(1536).astype(np.float32)
        vec /= np.linalg.norm(vec)
        return vec

    monkeypatch.setattr(MemoryStore, "embed", _fake_embed)


def test_profile_roundtrip() -> None:
    m = memory()
    m.set_profile("user_name", "Shrey")
    assert m.get_profile("user_name") == "Shrey"
    m.set_profile("user_name", "Shreyas")  # update replaces
    assert m.get_profile("user_name") == "Shreyas"
    assert m.list_profile() == {"user_name": "Shreyas"}


def test_profile_missing_key_returns_none() -> None:
    assert memory().get_profile("nonexistent") is None


def test_record_episode_persists_row() -> None:
    m = memory()
    eid = m.record_episode(
        turn_id="t1",
        transcript="what's the weather",
        reply="Clear and 58.",
        status="done",
        via="direct:research",
    )
    assert eid > 0
    recent = m.recent_episodes(limit=5)
    assert len(recent) == 1
    assert recent[0].transcript == "what's the weather"
    assert recent[0].reply == "Clear and 58."
    assert recent[0].via == "direct:research"


def test_recall_returns_exact_match_first() -> None:
    # With the deterministic hash-based fake embedding, identical text
    # produces identical vectors → perfect cosine similarity of 1.0.
    m = memory()
    m.record_episode(
        turn_id="t1", transcript="remind me to call mom",
        reply="Added.", status="done", via="direct:communication",
    )
    m.record_episode(
        turn_id="t2", transcript="what's the capital of france",
        reply="Paris.", status="done", via="direct:research",
    )
    m.record_episode(
        turn_id="t3", transcript="order more coffee",
        reply="Found three options.", status="done", via="direct:commerce",
    )

    top = m.recall("what's the capital of france", k=1)
    assert len(top) == 1
    assert top[0].transcript == "what's the capital of france"
    assert top[0].score == pytest.approx(1.0, abs=1e-6)


def test_recall_respects_user_isolation() -> None:
    m = memory()
    m.record_episode(
        turn_id="t1", transcript="alpha note", reply="ok",
        status="done", via="v", user_id="alice",
    )
    m.record_episode(
        turn_id="t2", transcript="beta note", reply="ok",
        status="done", via="v", user_id="bob",
    )
    alice_hits = m.recall("alpha note", k=5, user_id="alice")
    assert len(alice_hits) == 1
    assert alice_hits[0].transcript == "alpha note"

    bob_hits = m.recall("alpha note", k=5, user_id="bob")
    # Bob has no "alpha" episode — fake embedding of "alpha note" still
    # matches against "beta note" via cosine, but isolation means no
    # cross-user bleed: bob only sees his own rows.
    assert all(ep.transcript == "beta note" for ep in bob_hits)


def test_recall_falls_back_to_like_when_no_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If embeddings aren't available (no key, or older rows), recall should
    degrade to substring match on the transcript/reply."""
    # Undo the autouse embedding stub for this test: force embed to return None.
    monkeypatch.setattr(MemoryStore, "embed", lambda self, text: None)

    # With embed()→None, record_episode inserts blob=NULL, and recall also
    # has no query vector, so it hits the LIKE branch directly.
    m = MemoryStore()  # fresh instance — bypasses any cached _openai client
    memory_mod._store = m

    m.record_episode(
        turn_id="t1", transcript="order coffee from peets",
        reply="done", status="done", via="v",
    )
    m.record_episode(
        turn_id="t2", transcript="call the dentist",
        reply="scheduled", status="done", via="v",
    )

    hits = m.recall("coffee")
    assert len(hits) == 1
    assert hits[0].transcript == "order coffee from peets"
