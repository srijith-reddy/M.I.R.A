from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from mira.obs.logging import log_event
from mira.runtime.schemas import AgentRequest, AgentResponse, AgentStatus
from mira.runtime.tracing import span


class Agent(ABC):
    """Base class for every agent in the system.

    An agent is a narrow, role-bound worker that accepts an AgentRequest and
    returns a single AgentResponse. It never calls sibling agents directly —
    delegation flows through the Supervisor via HANDOFF. This is what keeps
    the system multi-agent instead of a tangle of point-to-point RPC.

    Subclasses implement `_run(req)`. The base `handle()` wraps every call in
    a traced span, enforces the latency budget as a soft warning, and packs
    unhandled exceptions into a structured error response so the control
    plane never crashes on a single agent blowup.
    """

    name: str = "agent"

    @abstractmethod
    async def _run(self, req: AgentRequest) -> AgentResponse:
        ...

    async def handle(self, req: AgentRequest) -> AgentResponse:
        t0 = time.perf_counter()
        with span(
            "agent.handle",
            agent=self.name,
            turn_id=req.turn_id,
            goal=req.goal[:120],
            budget_ms=req.budget_ms,
        ):
            try:
                resp = await self._run(req)
            except Exception as exc:
                log_event(
                    "agent.error",
                    agent=self.name,
                    turn_id=req.turn_id,
                    error=repr(exc),
                )
                return AgentResponse(
                    turn_id=req.turn_id,
                    agent=self.name,
                    status=AgentStatus.ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )

        resp.latency_ms = resp.latency_ms or int((time.perf_counter() - t0) * 1000)
        if resp.latency_ms > req.budget_ms:
            log_event(
                "agent.budget_exceeded",
                agent=self.name,
                turn_id=req.turn_id,
                latency_ms=resp.latency_ms,
                budget_ms=req.budget_ms,
            )
        return resp


class AgentDirectory:
    """Name → agent-instance lookup. Not the tool registry — different concern.

    Tools are callable side-effects; agents are LLM-backed policies. Keeping
    them separate means a tool never accidentally gets picked by the router
    as a destination for a handoff."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        if agent.name in self._agents:
            raise ValueError(f"agent already registered: {agent.name}")
        self._agents[agent.name] = agent

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def names(self) -> list[str]:
        return sorted(self._agents.keys())

    def describe(self) -> list[dict[str, Any]]:
        """Short catalogue for the router prompt — agent name + one-line purpose."""
        out = []
        for a in self._agents.values():
            out.append({"name": a.name, "purpose": getattr(a, "purpose", "")})
        return out


_directory: AgentDirectory | None = None


def agents() -> AgentDirectory:
    global _directory
    if _directory is None:
        _directory = AgentDirectory()
    return _directory
