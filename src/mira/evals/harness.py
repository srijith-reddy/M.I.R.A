from __future__ import annotations

import json
from typing import Any

from mira import agents as agents_pkg
from mira.agents.base import AgentDirectory
from mira.agents import base as agents_base_mod
from mira.runtime.store import connect


def reset_agents() -> None:
    """Clear the agent directory and re-arm `install_default_agents`.

    Used between tests that want a clean specialist lineup (e.g. to register
    only a stub research agent instead of the real one)."""
    agents_base_mod._directory = AgentDirectory()
    agents_pkg._installed = False


def install_test_agents(*instances: Any) -> None:
    """Replace the directory with just the passed agents. Mirrors the prod
    install path without pulling in specialists that need external keys."""
    reset_agents()
    directory = agents_base_mod.agents()
    for inst in instances:
        directory.register(inst)
    agents_pkg._installed = True


def reset_session_db() -> None:
    """Wipe session/reminder/observability rows but keep the schema. Faster
    than re-creating the DB file and avoids races with WAL files pytest
    hasn't released yet."""
    with connect() as conn:
        conn.execute("DELETE FROM session_state")
        conn.execute("DELETE FROM reminders")
        # Observability tables (Batch 14). Wiping between tests keeps the
        # dashboard recorder tests from leaking rows into unrelated tests.
        conn.execute("DELETE FROM turns")
        conn.execute("DELETE FROM events")


def script_tool_call(
    *, call_id: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Build one OpenAI-shaped tool_call dict. Matches what the real gateway
    would emit so FakeLLMGateway responses round-trip through agent code."""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def script_text(text: str) -> dict[str, Any]:
    """Sugar for building a plain-text FakeLLMGateway response payload."""
    return {"text": text}
