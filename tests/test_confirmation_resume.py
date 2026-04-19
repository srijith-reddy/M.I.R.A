from __future__ import annotations

from pydantic import BaseModel

from mira.agents.base import Agent
from mira.evals.fakes import FakeLLMGateway
from mira.evals.harness import install_test_agents
from mira.runtime.orchestrator import run_turn
from mira.runtime.registry import registry
from mira.runtime.schemas import (
    AgentRequest,
    AgentResponse,
    AgentStatus,
    ToolCall,
)
from mira.runtime.session import (
    PendingConfirmation,
    load_pending,
    set_pending,
)


# Shared probe tool. Registered once at module load — the registry rejects
# duplicate names, so we guard against double registration under pytest
# re-imports.
_CALLS: list[dict] = []


class _ProbeArgs(BaseModel):
    tag: str


if registry().get("test.probe") is None:

    @registry().register(
        "test.probe",
        description="Test-only tool; records each invocation's args.",
        params=_ProbeArgs,
        requires_confirmation=True,
        tags=("test",),
    )
    def _probe(args: _ProbeArgs) -> dict:
        _CALLS.append({"tag": args.tag})
        return {"ok": True, "tag": args.tag}


class _DummyAgent(Agent):
    name = "supervisor"
    purpose = "Plans."

    async def _run(self, req: AgentRequest) -> AgentResponse:
        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.DONE,
            speak="Fallback reply.",
        )


def _arm_pending(user_id: str = "local") -> PendingConfirmation:
    call = ToolCall(
        tool="test.probe",
        args={"tag": "alpha"},
        requires_confirmation=True,
        call_id="call-1",
    )
    pending = PendingConfirmation(
        original_turn_id="t-original",
        agent="supervisor",
        tool_call=call,
        prompt="Run the probe?",
    )
    set_pending(pending, user_id=user_id)
    return pending


async def test_yes_dispatches_pending_tool(fake_llm: FakeLLMGateway) -> None:
    install_test_agents(_DummyAgent())
    _CALLS.clear()
    _arm_pending()

    # No LLM calls are needed on the resume path — classifier is deterministic.
    result = await run_turn("yes")

    assert result.via == "confirmation-resume"
    assert result.status == AgentStatus.DONE
    assert _CALLS == [{"tag": "alpha"}]
    # Pending row cleared so the next "yes" doesn't re-fire.
    assert load_pending("local") is None
    # And no LLM calls on this path.
    assert len(fake_llm.calls) == 0


async def test_no_clears_pending_and_acknowledges(fake_llm: FakeLLMGateway) -> None:
    install_test_agents(_DummyAgent())
    _CALLS.clear()
    _arm_pending()

    result = await run_turn("cancel")

    assert result.via == "confirmation-resume"
    assert result.status == AgentStatus.DONE
    assert _CALLS == []  # Tool was NOT dispatched.
    assert load_pending("local") is None
    assert len(fake_llm.calls) == 0


async def test_unclear_reply_runs_normal_turn_and_keeps_pending(
    fake_llm: FakeLLMGateway,
) -> None:
    install_test_agents(_DummyAgent())
    _CALLS.clear()
    _arm_pending()

    # Router call is needed because the resume path falls through.
    fake_llm.push_json(
        {"kind": "smalltalk", "agent": None, "confidence": 0.9, "reason": "chatty"}
    )
    result = await run_turn("tell me a joke")

    # Normal turn ran, reply came from the dummy supervisor.
    assert result.reply == "Fallback reply."
    assert result.via == "smalltalk"
    # Pending confirmation still armed — user hasn't answered it yet.
    assert load_pending("local") is not None
    # Probe tool was not dispatched on this path.
    assert _CALLS == []
