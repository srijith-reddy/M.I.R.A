"""Shared tool-call dispatcher for specialist agents.

Three agents (communication, commerce, device, browser) all followed the
same shape: for each tool_call emitted by the planner → check confirmation
→ dispatch serially → append result as a tool message. This module folds
that loop into one async call and layers two wins on top:

  1. Parallel dispatch — read-only tools run via `asyncio.gather`. A planner
     emitting `web.search` + `reminder.list` in one step used to pay both
     latencies serially; now they overlap.
  2. Result summarization — `registry.format_result` is used per call, so
     tools that registered a `summarizer` produce compact prose instead of
     the raw 4-8k-token JSON the planner doesn't need.

The partition is deliberately conservative:
  * Anything `requires_confirmation` short-circuits the whole batch — we
    surface the first such call as a `NEED_CONFIRMATION` response. The
    remaining calls in that batch are discarded; the planner will re-issue
    them after the user approves/denies, with fresh context.
  * `browser.*` always runs serially on the page-owning thread. Two
    concurrent clicks on the same Chromium page are a nightmare we do not
    want to debug.
  * Everything else is eligible for parallel dispatch.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from mira.obs.logging import log_event
from mira.runtime.llm import Message
from mira.runtime.registry import registry
from mira.runtime.schemas import (
    AgentResponse,
    AgentStatus,
    Confirmation,
    ToolCall,
)


@dataclass(frozen=True)
class DispatchOutcome:
    """Result of running a batch of tool_calls.

    Exactly one of these is populated:
      * `confirmation` — the batch hit a confirmation-gated call; the agent
        must return `NEED_CONFIRMATION` immediately.
      * `messages` — tool-response messages to append to the planner's
        conversation for the next hop.
    """

    messages: list[Message]
    confirmation: AgentResponse | None  # pre-built NEED_CONFIRMATION response
    # True if any dispatched tool returned `silent: True` on success. Lets
    # the agent short-circuit the usual "compose a spoken reply" path for
    # tools where the action *is* its own confirmation (music.play, etc.).
    silent: bool = False
    # Raw tool-call results, preserved so agents can mine structured data
    # the summarized tool-message string drops (e.g. web.search thumbnails
    # for card rendering). List of (tool_name, data_dict_or_none).
    raw_results: tuple[tuple[str, Any], ...] = ()


def _parse_args(tc: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    fn = tc.get("function") or {}
    name = fn.get("name") or ""
    args_raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
    except Exception:
        args = {}
    call_id = tc.get("id") or ""
    return name, args, call_id


def _is_serial_only(tool_name: str) -> bool:
    """Tools that mutate shared state (the Chromium page) and must run in
    issue order. Read-only browser scraping still counts — `browser.read_page`
    reads whatever the *current* page is, which depends on the previous
    navigate in the batch."""
    return tool_name.startswith("browser.")


async def run_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    turn_id: str,
    agent_name: str,
    confirmation_prompt: "ConfirmationPromptFn",
) -> DispatchOutcome:
    """Dispatch a batch of tool_calls, parallelizing where safe.

    Contract:
      * Resolves each tool name through the registry (handles the `.` → `_`
        boundary translation already done by `registry.get`).
      * If any call's spec has `requires_confirmation=True`, returns early
        with a pre-built `AgentResponse(NEED_CONFIRMATION)`. The caller
        should return that response directly.
      * Otherwise partitions into parallel vs serial groups. Parallel group
        runs under `asyncio.gather`; serial group runs in original emission
        order. Both use `registry.format_result` to build the tool-response
        content string.

    `confirmation_prompt(tool_name, args) -> str` is provided by the caller
    since prompts vary per agent's voice (communication says "Delete
    reminder 3?"; commerce says "Place the order?").
    """
    parallel_indexed: list[tuple[int, ToolCall]] = []
    serial_indexed: list[tuple[int, ToolCall]] = []

    for idx, tc in enumerate(tool_calls):
        name, args, call_id = _parse_args(tc)
        spec = registry().get(name)

        if spec is not None and spec.requires_confirmation:
            pending = ToolCall(
                tool=name,
                args=args,
                requires_confirmation=True,
                call_id=call_id,
            )
            log_event(
                f"{agent_name}.confirmation_required",
                tool=name,
                args=args,
            )
            response = AgentResponse(
                turn_id=turn_id,
                agent=agent_name,
                status=AgentStatus.NEED_CONFIRMATION,
                confirmation=Confirmation(
                    prompt=confirmation_prompt(name, args),
                    action=pending,
                ),
            )
            return DispatchOutcome(messages=[], confirmation=response)

        call = ToolCall(
            tool=name, args=args, requires_confirmation=False, call_id=call_id
        )
        if _is_serial_only(name):
            serial_indexed.append((idx, call))
        else:
            parallel_indexed.append((idx, call))

    # Dispatch parallel calls. `asyncio.gather` preserves order within its
    # returned list, but we keep the original emission index so tool-response
    # messages land in the same order the planner saw (some planners are
    # picky about this, and it makes traces easier to read).
    results_by_idx: dict[int, Any] = {}
    if parallel_indexed:
        log_event(
            f"{agent_name}.parallel_dispatch",
            count=len(parallel_indexed),
            tools=[c.tool for _, c in parallel_indexed],
        )
        gathered = await asyncio.gather(
            *(registry().dispatch(c) for _, c in parallel_indexed),
            return_exceptions=False,
        )
        for (idx, call), result in zip(parallel_indexed, gathered, strict=True):
            results_by_idx[idx] = (call, result)

    for idx, call in serial_indexed:
        result = await registry().dispatch(call)
        results_by_idx[idx] = (call, result)

    messages: list[Message] = []
    silent = False
    raw: list[tuple[str, Any]] = []
    for idx in sorted(results_by_idx.keys()):
        call, result = results_by_idx[idx]
        content = registry().format_result(call.tool, result)
        messages.append(
            Message(role="tool", tool_call_id=call.call_id, content=content)
        )
        # Tool return values are wrapped in a ToolResult pydantic model by
        # registry.dispatch — the raw dict the tool produced lives in
        # `.data`. Keep a dict-fallback too for any in-tree paths that
        # bypass the wrapper.
        data = getattr(result, "data", None)
        if data is None and isinstance(result, dict):
            data = result
        ok = getattr(result, "ok", None)
        if ok is None and isinstance(result, dict):
            ok = result.get("ok")
        if isinstance(data, dict) and data.get("silent") and ok:
            silent = True
        raw.append((call.tool, data))
    return DispatchOutcome(
        messages=messages, confirmation=None, silent=silent, raw_results=tuple(raw)
    )


# Local type alias — avoids importing Callable at the top of every caller.
ConfirmationPromptFn = Any  # Callable[[str, dict[str, Any]], str]
