from __future__ import annotations

from mira.agents.base import Agent
from mira.evals.fakes import FakeLLMGateway
from mira.evals.harness import install_test_agents
from mira.runtime.orchestrator import run_turn
from mira.runtime.schemas import AgentRequest, AgentResponse, AgentStatus


class _ScriptedAgent(Agent):
    """Pass-through specialist: returns a pre-configured reply regardless of input.
    Used to verify the orchestrator's routing + persistence plumbing without
    dragging a real LLM-driven agent into scope."""

    def __init__(self, name: str, purpose: str, reply: str) -> None:
        self.name = name
        self.purpose = purpose
        self._reply = reply

    async def _run(self, req: AgentRequest) -> AgentResponse:
        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.DONE,
            speak=self._reply,
        )


async def test_direct_route_dispatches_to_named_agent(fake_llm: FakeLLMGateway) -> None:
    research = _ScriptedAgent("research", "Factual Q&A.", "Paris.")
    install_test_agents(research)

    fake_llm.push_json(
        {"kind": "direct", "agent": "research", "confidence": 0.9, "reason": "fact"}
    )
    result = await run_turn("what is the capital of france")

    assert result.status == AgentStatus.DONE
    assert result.reply == "Paris."
    assert result.via == "direct:research"


async def test_supervisor_fallback_when_agent_missing(fake_llm: FakeLLMGateway) -> None:
    # Only a supervisor is registered — direct routes that name an unknown
    # agent should land on supervisor anyway (router guards this; orchestrator
    # additionally falls back if the resolved name doesn't exist).
    supervisor = _ScriptedAgent(
        "supervisor", "Plans and delegates.", "Here's the plan."
    )
    install_test_agents(supervisor)

    fake_llm.push_json(
        {"kind": "supervisor", "agent": None, "confidence": 0.5, "reason": "multi"}
    )
    result = await run_turn("book me a flight and email my boss")

    assert result.status == AgentStatus.DONE
    assert result.via == "supervisor"
    assert result.reply == "Here's the plan."


async def test_smalltalk_routes_to_supervisor(fake_llm: FakeLLMGateway) -> None:
    supervisor = _ScriptedAgent("supervisor", "Plans.", "Hey!")
    install_test_agents(supervisor)

    fake_llm.push_json(
        {"kind": "smalltalk", "agent": None, "confidence": 0.95, "reason": "hi"}
    )
    result = await run_turn("hello")
    assert result.via == "smalltalk"
    assert result.reply == "Hey!"


async def test_router_error_falls_back_to_supervisor(fake_llm: FakeLLMGateway) -> None:
    supervisor = _ScriptedAgent("supervisor", "Plans.", "Caught it.")
    install_test_agents(supervisor)

    # Malformed router output forces the fallback path.
    fake_llm.push(text="not json")
    result = await run_turn("do something")
    assert result.via == "supervisor"
    assert result.status == AgentStatus.DONE
