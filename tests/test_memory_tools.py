from __future__ import annotations

from typing import Iterator

import numpy as np
import pytest

from mira.runtime.memory import MemoryStore
from mira.runtime.registry import registry
from mira.runtime.schemas import ToolCall
from mira.runtime.store import connect
from mira.tools import memory_tools  # noqa: F401 — registers tools


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Wipe profile + episodes so assertions are deterministic across tests.
    with connect() as conn:
        conn.execute("DELETE FROM profile")
        conn.execute("DELETE FROM episodes")

    # Deterministic fake embedding — same text → same vector → cosine 1.0.
    def _fake_embed(self: MemoryStore, text: str) -> np.ndarray | None:
        if not text.strip():
            return None
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        vec = rng.standard_normal(1536).astype(np.float32)
        vec /= np.linalg.norm(vec)
        return vec

    monkeypatch.setattr(MemoryStore, "embed", _fake_embed)
    yield
    with connect() as conn:
        conn.execute("DELETE FROM profile")
        conn.execute("DELETE FROM episodes")


async def _call(name: str, args: dict) -> dict:
    call = ToolCall(call_id="c1", tool=name, args=args)
    result = await registry().dispatch(call)
    assert result.ok, f"tool {name} failed: {result.error}"
    return result.data  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_remember_stores_profile() -> None:
    data = await _call(
        "memory.remember", {"key": "coffee", "value": "oat milk cortado"}
    )
    assert data["key"] == "coffee"
    assert data["value"] == "oat milk cortado"
    assert data["prior_value"] is None
    assert data["overwritten"] is False


@pytest.mark.asyncio
async def test_remember_overwrite_flag() -> None:
    await _call("memory.remember", {"key": "city", "value": "Austin"})
    data = await _call("memory.remember", {"key": "city", "value": "Denver"})
    assert data["prior_value"] == "Austin"
    assert data["overwritten"] is True


@pytest.mark.asyncio
async def test_forget_requires_confirmation_flag() -> None:
    # The registry's `requires_confirmation` attribute is declarative; we're
    # not exercising the orchestrator's gate here — just asserting the tool
    # is registered with the right policy so agents know to confirm.
    spec = registry().get("memory.forget")
    assert spec is not None
    assert spec.requires_confirmation is True


@pytest.mark.asyncio
async def test_forget_deletes_known_key() -> None:
    await _call("memory.remember", {"key": "city", "value": "Austin"})
    data = await _call("memory.forget", {"key": "city"})
    assert data["deleted"] is True
    assert data["prior_value"] == "Austin"

    # Recall should no longer surface it in the profile.
    recall_data = await _call("memory.recall", {"query": "where do I live?"})
    assert "city" not in recall_data["profile"]


@pytest.mark.asyncio
async def test_forget_unknown_key_is_noop() -> None:
    data = await _call("memory.forget", {"key": "never-set"})
    assert data["deleted"] is False


@pytest.mark.asyncio
async def test_recall_surfaces_profile_and_items() -> None:
    await _call("memory.remember", {"key": "name", "value": "Shrey"})
    data = await _call("memory.recall", {"query": "what is my name"})
    # No episodes written yet, but profile surfaces unconditionally.
    assert data["profile"] == {"name": "Shrey"}
    assert data["count"] == 0
    assert data["items"] == []
