from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mira.runtime.llm import LLMResponse, Message, llm
from mira.runtime.orchestrator import run_turn

# LLM-as-judge: grade the live MIRA stack against a rubric on a curated set
# of voice-shaped prompts. Deliberately NOT a pytest suite — it hits real
# OpenAI for both the turn-under-test and the judge, so running costs money
# and (more importantly) it's non-deterministic. Pytest's "red/green" ethos
# doesn't fit. This is more like a benchmark you run before a release.
#
# Usage:
#   python -m mira.evals.judge              # runs the built-in case set
#   python -m mira.evals.judge --cases path/to/cases.json
#   python -m mira.evals.judge --output judge.json
#
# Each case is (id, prompt, traits[]). A trait is a short criterion the
# reply should satisfy ("confirms the action happened", "speaks in under
# 20 words", "does not fabricate a price"). The judge returns a per-trait
# pass/fail + a rationale; we aggregate into a per-case score and a run
# summary.


_JUDGE_MODEL = "gpt-4.1-mini"
_JUDGE_SYSTEM = (
    "You are a strict but fair evaluator of a voice assistant's replies. "
    "For each trait, answer pass/fail and give a one-sentence rationale. "
    "A reply that is technically correct but too long for speech should "
    "fail a 'concise' trait. A reply that invents facts should fail a "
    "'does not fabricate' trait. Return strict JSON only, matching the "
    "schema in the user message. No prose outside JSON."
)


@dataclass(frozen=True)
class JudgeCase:
    case_id: str
    prompt: str
    traits: list[str]


@dataclass
class TraitVerdict:
    trait: str
    passed: bool
    rationale: str


@dataclass
class CaseResult:
    case_id: str
    prompt: str
    reply: str
    via: str
    latency_ms: int
    verdicts: list[TraitVerdict] = field(default_factory=list)
    judge_error: str | None = None

    @property
    def pass_rate(self) -> float:
        if not self.verdicts:
            return 0.0
        return sum(1 for v in self.verdicts if v.passed) / len(self.verdicts)


# Curated defaults. Keep this set small and opinionated — the goal is a
# 60-second smoke grade, not exhaustive coverage. Add a new case only when
# a real bug shows you're missing a trait.
_DEFAULT_CASES: list[JudgeCase] = [
    JudgeCase(
        case_id="smalltalk_short",
        prompt="hey, how's it going",
        traits=[
            "reply is under 25 words",
            "reply is conversational and doesn't pivot to an unrelated task",
            "reply does not fabricate facts about the user",
        ],
    ),
    JudgeCase(
        case_id="reminder_create_in_minutes",
        prompt="remind me to take out the trash in 20 minutes",
        traits=[
            "reply confirms a reminder was created",
            "reply mentions the 20-minute timeframe in natural language",
            "reply is under 25 words",
        ],
    ),
    JudgeCase(
        case_id="reminder_create_ambiguous",
        prompt="remind me to do the thing whenever",
        traits=[
            "reply does not silently invent a specific time",
            "reply either asks for a time or acknowledges creating a standalone todo",
            "reply is under 25 words",
        ],
    ),
    JudgeCase(
        case_id="memory_remember",
        prompt="remember that my favorite coffee is oat milk cortado",
        traits=[
            "reply confirms the preference was stored",
            "reply does not invent a location or other unrelated fact",
            "reply is under 25 words",
        ],
    ),
    JudgeCase(
        case_id="commerce_no_checkout",
        prompt="buy me a new wireless mouse",
        traits=[
            "reply does not claim an order was placed",
            "reply offers to research options or asks for a budget/brand",
            "reply is under 40 words",
        ],
    ),
]


async def _run_case(case: JudgeCase) -> CaseResult:
    t0 = time.perf_counter()
    result = await run_turn(case.prompt)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return CaseResult(
        case_id=case.case_id,
        prompt=case.prompt,
        reply=result.reply or "",
        via=result.via,
        latency_ms=latency_ms,
    )


def _judge_prompt(case: JudgeCase, reply: str) -> list[Message]:
    schema_hint = {
        "verdicts": [
            {"trait": "<trait text>", "passed": True, "rationale": "<one sentence>"}
        ]
    }
    user = (
        f"USER_PROMPT:\n{case.prompt}\n\n"
        f"ASSISTANT_REPLY:\n{reply}\n\n"
        f"TRAITS:\n" + "\n".join(f"- {t}" for t in case.traits) + "\n\n"
        f"Return JSON of shape: {json.dumps(schema_hint)}"
    )
    return [
        Message(role="system", content=_JUDGE_SYSTEM),
        Message(role="user", content=user),
    ]


def _judge_case(case: JudgeCase, reply: str) -> CaseResult | None:
    """Not async — uses sync LLMGateway.complete (the judge has no TTS path,
    so streaming buys us nothing)."""
    try:
        resp: LLMResponse = llm().complete(
            _judge_prompt(case, reply),
            model=_JUDGE_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        return CaseResult(
            case_id=case.case_id,
            prompt=case.prompt,
            reply=reply,
            via="",
            latency_ms=0,
            judge_error=f"judge call failed: {exc}",
        )
    try:
        parsed = json.loads(resp.text)
        raw_verdicts = parsed.get("verdicts") or []
        verdicts = [
            TraitVerdict(
                trait=str(v.get("trait", "")),
                passed=bool(v.get("passed", False)),
                rationale=str(v.get("rationale", "")),
            )
            for v in raw_verdicts
        ]
    except Exception as exc:
        return CaseResult(
            case_id=case.case_id,
            prompt=case.prompt,
            reply=reply,
            via="",
            latency_ms=0,
            judge_error=f"judge output parse failed: {exc}; raw={resp.text[:200]}",
        )
    return CaseResult(
        case_id=case.case_id,
        prompt=case.prompt,
        reply=reply,
        via="",
        latency_ms=0,
        verdicts=verdicts,
    )


async def run(
    cases: list[JudgeCase],
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Run every case, judge each, aggregate. Returns a serializable dict.

    Failures in the turn-under-test are captured as reply='' with via empty;
    the judge still runs and will typically fail every trait, which is the
    correct signal."""
    results: list[dict[str, Any]] = []
    for case in cases:
        turn_result = await _run_case(case)
        judged = _judge_case(case, turn_result.reply)
        if judged is not None:
            # Merge judge verdicts onto the turn result so latency/via are kept.
            turn_result.verdicts = judged.verdicts
            turn_result.judge_error = judged.judge_error
        results.append(asdict(turn_result))

    total_traits = sum(len(r["verdicts"]) for r in results)
    passed = sum(
        1 for r in results for v in r["verdicts"] if v["passed"]
    )
    summary = {
        "cases": len(results),
        "traits_total": total_traits,
        "traits_passed": passed,
        "overall_pass_rate": (passed / total_traits) if total_traits else 0.0,
        "results": results,
    }
    if output_path is not None:
        output_path.write_text(json.dumps(summary, indent=2))
    return summary


def load_cases(path: Path) -> list[JudgeCase]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("cases file must be a JSON list")
    return [
        JudgeCase(
            case_id=str(item["case_id"]),
            prompt=str(item["prompt"]),
            traits=[str(t) for t in item.get("traits", [])],
        )
        for item in raw
    ]


def _format_report(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"{summary['traits_passed']}/{summary['traits_total']} traits passed "
        f"across {summary['cases']} cases "
        f"({summary['overall_pass_rate']:.0%})"
    )
    lines.append("")
    for r in summary["results"]:
        lines.append(f"[{r['case_id']}] via={r['via']} latency={r['latency_ms']}ms")
        lines.append(f"  prompt: {r['prompt']}")
        lines.append(f"  reply:  {r['reply']}")
        if r.get("judge_error"):
            lines.append(f"  !! judge error: {r['judge_error']}")
            continue
        for v in r["verdicts"]:
            mark = "PASS" if v["passed"] else "FAIL"
            lines.append(f"  [{mark}] {v['trait']}")
            lines.append(f"         {v['rationale']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MIRA LLM-as-judge eval runner")
    parser.add_argument(
        "--cases",
        type=Path,
        default=None,
        help="Path to JSON cases file. Defaults to the built-in set.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write raw JSON summary to this path.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the pretty text report (useful with --output).",
    )
    args = parser.parse_args()

    cases = load_cases(args.cases) if args.cases else list(_DEFAULT_CASES)
    summary = asyncio.run(run(cases, output_path=args.output))
    if not args.quiet:
        print(_format_report(summary))
    # Non-zero exit on <80% pass so CI / a release script can gate on it.
    return 0 if summary["overall_pass_rate"] >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
