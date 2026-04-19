from __future__ import annotations

import pytest

from mira.agents.router import FastRouter
from mira.evals.fakes import FakeLLMGateway


_CATALOG = [
    {"name": "research", "purpose": "Factual Q&A."},
    {"name": "communication", "purpose": "Email + reminders."},
    {"name": "commerce", "purpose": "Shopping."},
]


@pytest.mark.asyncio
async def test_router_direct_to_research(fake_llm: FakeLLMGateway) -> None:
    fake_llm.push_json(
        {"kind": "direct", "agent": "research", "confidence": 0.9, "reason": "factual"}
    )
    decision = await FastRouter().decide(
        "what is the capital of france", agents_catalog=_CATALOG
    )
    assert decision.kind == "direct"
    assert decision.agent == "research"
    assert decision.confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_router_supervisor_for_multistep(fake_llm: FakeLLMGateway) -> None:
    fake_llm.push_json(
        {"kind": "supervisor", "agent": None, "confidence": 0.6, "reason": "multi"}
    )
    decision = await FastRouter().decide(
        "find the best price and order one", agents_catalog=_CATALOG
    )
    assert decision.kind == "supervisor"
    assert decision.agent is None


@pytest.mark.asyncio
async def test_router_smalltalk(fake_llm: FakeLLMGateway) -> None:
    fake_llm.push_json(
        {"kind": "smalltalk", "agent": None, "confidence": 0.95, "reason": "greeting"}
    )
    decision = await FastRouter().decide("hey there", agents_catalog=_CATALOG)
    assert decision.kind == "smalltalk"


@pytest.mark.asyncio
async def test_router_hallucinated_direct_target_falls_back(
    fake_llm: FakeLLMGateway,
) -> None:
    # Router picks an agent name that isn't in the catalog — orchestrator
    # would then have no one to dispatch to. Guard falls back to supervisor.
    fake_llm.push_json(
        {"kind": "direct", "agent": "ghost", "confidence": 0.9, "reason": "bad"}
    )
    decision = await FastRouter().decide("whatever", agents_catalog=_CATALOG)
    assert decision.kind == "supervisor"
    assert decision.agent is None


@pytest.mark.asyncio
async def test_router_parse_error_falls_back(fake_llm: FakeLLMGateway) -> None:
    fake_llm.push(text="not json at all {{{")
    decision = await FastRouter().decide("whatever", agents_catalog=_CATALOG)
    assert decision.kind == "supervisor"
    assert decision.confidence == 0.0


@pytest.mark.asyncio
async def test_router_empty_transcript_short_circuits(fake_llm: FakeLLMGateway) -> None:
    decision = await FastRouter().decide("   ", agents_catalog=_CATALOG)
    assert decision.kind == "supervisor"
    # No LLM call should have happened on the empty-transcript path.
    assert len(fake_llm.calls) == 0
