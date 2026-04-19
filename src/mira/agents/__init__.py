from __future__ import annotations

from mira.agents.base import Agent, AgentDirectory, agents
from mira.agents.browser_agent import BrowserActionAgent
from mira.agents.commerce import CommerceAgent
from mira.agents.communication import CommunicationAgent
from mira.agents.device import DeviceAgent
from mira.agents.memory import MemoryAgent
from mira.agents.research import ResearchAgent
from mira.agents.router import FastRouter, router
from mira.agents.supervisor import SupervisorAgent


_installed = False


def install_default_agents() -> None:
    """Register the current baseline. Called once at process start.

    Adding an agent later is a two-liner: import + `directory.register(...)`.
    We deliberately don't auto-import agents into the directory from their
    own modules — keeping registration centralized here makes the full set
    of active agents a single grep away.
    """
    global _installed
    if _installed:
        return
    # Importing `tools` triggers @tool decorators (browser tools, etc.) and
    # `install_default_tools()` registers any optional tools whose keys are set.
    from mira.tools import install_default_tools

    install_default_tools()

    directory = agents()
    directory.register(SupervisorAgent())
    directory.register(ResearchAgent())
    directory.register(BrowserActionAgent())
    directory.register(CommunicationAgent())
    directory.register(CommerceAgent())
    directory.register(DeviceAgent())
    mem = MemoryAgent()
    directory.register(mem)
    mem.start()
    _installed = True


__all__ = [
    "Agent",
    "AgentDirectory",
    "BrowserActionAgent",
    "CommerceAgent",
    "CommunicationAgent",
    "DeviceAgent",
    "FastRouter",
    "MemoryAgent",
    "ResearchAgent",
    "SupervisorAgent",
    "agents",
    "install_default_agents",
    "router",
]
