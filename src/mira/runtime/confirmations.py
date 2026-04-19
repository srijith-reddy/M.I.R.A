from __future__ import annotations

from typing import Any, Callable

# Central registry for the TTS confirmation prompts users hear before a
# destructive/side-effectful tool runs. Keeping this in one place means:
#   * Prompt wording can be tuned without touching every agent.
#   * New agents inherit sane defaults — no per-agent copy-paste drift.
#   * Tests can assert the exact phrasing we ship to voice.
#
# Registration pattern: tool name → callable that takes the parsed args and
# returns a short spoken sentence. Unknown tools fall back to a generic
# "Should I run X?" so a missing registration never blocks the loop.


PromptFn = Callable[[dict[str, Any]], str]

_PROMPTS: dict[str, PromptFn] = {}


def register(tool_name: str, fn: PromptFn) -> None:
    _PROMPTS[tool_name] = fn


def prompt_for(tool_name: str, args: dict[str, Any]) -> str:
    fn = _PROMPTS.get(tool_name)
    if fn is None:
        return f"Should I run {tool_name}?"
    try:
        return fn(args)
    except Exception:
        return f"Should I run {tool_name}?"


# ---------- default prompts ----------

def _messages_send(args: dict[str, Any]) -> str:
    body = str(args.get("body") or "")
    if len(body) > 80:
        body = body[:80] + "..."
    return f'Send to {args.get("recipient")}: "{body}"?'


def _app_quit(args: dict[str, Any]) -> str:
    return f"Quit {args.get('name')}?"


def _reminder_delete(args: dict[str, Any]) -> str:
    return f"Delete reminder {args.get('id')}?"


def _memory_forget_episode(args: dict[str, Any]) -> str:
    return f"Forget that past turn (episode {args.get('id')})?"


def _browser_click(args: dict[str, Any]) -> str:
    return f"Should I click {args.get('selector', 'this element')}?"


def _browser_press(args: dict[str, Any]) -> str:
    return f"Should I press {args.get('key', 'that key')}?"


register("messages.send", _messages_send)
register("app.quit", _app_quit)
register("reminder.delete", _reminder_delete)
register("memory.forget_episode", _memory_forget_episode)
register("browser.click", _browser_click)
register("browser.press", _browser_press)
