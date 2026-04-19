from __future__ import annotations

import asyncio
import re

from mira.agents.base import Agent
from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.llm import Message, llm
from mira.runtime.schemas import AgentRequest, AgentResponse, AgentStatus

_SYSTEM = """\
You are MIRA's research specialist. Answer the user in one or two short
sentences suitable for text-to-speech: no lists, no markdown, no URLs read
aloud verbatim. If you do not know, say so plainly. Favor directness over
hedging. The user is already impatient — every extra word costs them time.

You have no internet access and no tools for this path. If the question is
about live data (scores, weather, news, prices), say you need to look it up.
Do not guess a number, date, or score. A confident hallucination is worse
than a one-sentence refusal.
"""

# Cheap keyword gate for "this needs fresh data." Matching keeps the common
# trivia case fast (no web pipeline, no streaming setup) while escalating
# anything time-sensitive to the deep-research + streaming path. Err on the
# side of false positives — a web lookup on trivia costs a second, a
# confidently-stale answer on live data is a bug report.
# Prose-refusal detector: the fast (no-tools) LLM path sometimes replies
# with "I don't have current pricing" / "I'd need to look that up" instead
# of answering. That's the router picking the wrong agent (usually commerce
# or device material routed to research). When we see this shape we flip
# status to REFUSED so the orchestrator can retry via supervisor.
_REFUSAL_RE = re.compile(
    r"\b("
    r"i (?:can'?t|cannot|don'?t|do not|couldn'?t|won'?t) "
        r"(?:help|do|handle|access|check|look|find|answer|see|control|play|open|send|place|provide)"
    r"|i (?:don'?t|do not) have "
        r"(?:access|current|real[- ]?time|live|the ability|tools?|a way)"
    r"|(?:i'?d |i )need to look (?:that|it) up"
    r"|(?:that's|that is) not something i can"
    r"|i'?m not able to"
    r"|i'?m sorry,? i (?:can'?t|cannot)"
    r")\b",
    re.IGNORECASE,
)


_LIVE_HINTS = re.compile(
    r"\b(today|tonight|now|currently|latest|recent|this (?:week|month|year)|"
    r"score|scores|news|headline|headlines|weather|price|prices|stock|"
    r"market|released|launched|announced|yesterday|update)\b",
    re.IGNORECASE,
)


class ResearchAgent(Agent):
    name = "research"
    purpose = (
        "General knowledge, factual Q&A, explanations, and live info lookups "
        "(news, sports scores, current events, weather trends, prices of "
        "things in the news). "
        "Use for: 'who wrote Hamlet', 'explain how DNS works', "
        "'what's the Lakers score', 'latest news on SpaceX', "
        "'what happened in the election', 'is it going to rain tomorrow'. "
        "NOT for: shopping or 'best X under $Y' style queries (use commerce), "
        "the user's own email/calendar/reminders (use communication), "
        "controlling the Mac or playing music (use device), "
        "opening or acting on a specific website (use browser)."
    )

    def __init__(self) -> None:
        self._settings = get_settings()

    async def _run(self, req: AgentRequest) -> AgentResponse:
        question = req.transcript.strip() or req.goal.strip()

        if _LIVE_HINTS.search(question):
            # Live sports scores have a dedicated path — ESPN's JSON
            # scoreboard returns clean data in ~200ms, where the general
            # web pipeline can't reach JS-rendered live widgets.
            if _is_sports_score_query(question):
                sports = await _fetch_live_sports_score(question, req.turn_id)
                if sports is not None:
                    log_event("research.route", path="sports_api", question=question[:120])
                    return AgentResponse(
                        turn_id=req.turn_id,
                        agent=self.name,
                        status=AgentStatus.DONE,
                        speak=sports,
                    )
            log_event("research.route", path="deep_stream", question=question[:120])
            return await self._run_deep_stream(req, question)

        return await self._run_llm_only(req, question)

    async def _run_llm_only(
        self, req: AgentRequest, question: str
    ) -> AgentResponse:
        """Fast path for static trivia / general knowledge. Single LLM call,
        no web tier — "capital of France" shouldn't pay for Brave + Crawl4AI."""
        mem = req.context.get("memory") if isinstance(req.context, dict) else None
        user_content = question
        if isinstance(mem, dict) and mem:
            import json as _json
            user_content = (
                f"{question}\n\n[memory]\n{_json.dumps(mem, ensure_ascii=False)}"
            )

        messages = [
            Message(role="system", content=_SYSTEM),
            Message(role="user", content=user_content),
        ]

        def _call() -> str:
            resp = llm().complete(
                messages,
                model=self._settings.openai_planner_model,
                temperature=0.1,
                max_tokens=220,
            )
            return resp.text.strip()

        text = await asyncio.to_thread(_call)
        status = AgentStatus.DONE
        if text and len(text) < 400 and _REFUSAL_RE.search(text):
            log_event("research.self_refused", reply=text[:200])
            status = AgentStatus.REFUSED
        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=status,
            speak=text,
        )

    async def _run_deep_stream(
        self, req: AgentRequest, question: str
    ) -> AgentResponse:
        """Live-query path: plan → search → fetch → chunk → rerank → stream
        synthesis directly into TTS. First audio ~1s after pipeline completes
        (first DeepSeek token) instead of ~3s after full synthesis returns.

        Deliberately not shared with research.deep tool — this path emits an
        AsyncIterator for TTS; the tool returns a finished dict for callers
        (supervisor) that need the whole answer structured."""
        from mira.runtime.registry import registry
        from mira.runtime.schemas import ToolCall
        from mira.web.chunking import chunk_document
        from mira.web.planner import plan_query
        from mira.web.rerank import hybrid_rerank
        from mira.web.retrieval import progressive_fetch
        from mira.web.synthesize import synthesize_answer_stream

        plan = await plan_query(question)
        log_event(
            "research.deep_stream.start",
            query=question, planned=plan.queries, trust_mode=plan.trust_mode,
        )

        # Parallel search across planned subqueries via the web.search tool
        # so the brave-key gate and per-query cache are honored.
        reg = registry()
        if reg.get("web.search") is None:
            return AgentResponse(
                turn_id=req.turn_id, agent=self.name, status=AgentStatus.REFUSED,
                speak="I can't check that live right now — web search isn't configured.",
            )

        async def _one_search(q: str) -> list[dict]:
            res = await reg.dispatch(ToolCall(
                tool="web.search",
                args={"query": q, "max_results": 5, "trust_mode": plan.trust_mode},
            ))
            if not res.ok or not isinstance(res.data, dict):
                return []
            return [r for r in (res.data.get("results") or []) if r.get("url")]

        task_results = await asyncio.gather(*[_one_search(q) for q in plan.queries])
        # RRF merge by URL — same logic as research_tools._multi_search.
        k = 60
        scores: dict[str, float] = {}
        payload: dict[str, dict] = {}
        for results in task_results:
            for pos, r in enumerate(results):
                url = r.get("url")
                if not url:
                    continue
                scores[url] = scores.get(url, 0.0) + 1.0 / (k + pos)
                payload.setdefault(url, r)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        urls = [u for u, _ in ranked[:5]]

        if not urls:
            return AgentResponse(
                turn_id=req.turn_id, agent=self.name, status=AgentStatus.DONE,
                speak="I couldn't find any sources for that.",
            )

        outcomes = await progressive_fetch(urls)
        chunks = []
        for o in outcomes:
            if not o.text:
                continue
            chunks.extend(chunk_document(
                o.text, url=o.url, title=o.title, source_rank=o.source_rank,
            ))
        if not chunks:
            return AgentResponse(
                turn_id=req.turn_id, agent=self.name, status=AgentStatus.DONE,
                speak="I found sources but couldn't read them clearly.",
            )

        top = hybrid_rerank(question, chunks, top_k=5)
        stream, _citations = await synthesize_answer_stream(question, top)
        log_event(
            "research.deep_stream.synthesizing",
            sources=len(outcomes), top_chunks=len(top),
        )
        # Emit a citations card immediately — we already have titles,
        # URLs, and source domains from retrieval. Doing this before the
        # stream starts gives the user a visual scaffold of *where* the
        # spoken answer comes from while they're hearing it synthesized.
        _emit_citations_card(
            turn_id=req.turn_id,
            question=question,
            outcomes=outcomes,
            top_urls=[u for u, _ in ranked[:5]],
            payload=payload,
        )
        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.DONE,
            speak=None,
            speak_stream=stream,
        )


def _emit_citations_card(
    *,
    turn_id: str,
    question: str,
    outcomes: list,
    top_urls: list[str],
    payload: dict,
) -> None:
    """Build a ui.card straight from retrieval output — no LLM pass
    needed. Prefer successful fetches (titles from trafilatura) over
    raw search hits, since the titles tend to be cleaner. Cap at the
    top 5 so a noisy search doesn't overflow the HUD card."""
    try:
        # Map URL → outcome for richer titles / snippets when we fetched
        # the page successfully.
        by_url = {o.url: o for o in outcomes if getattr(o, "url", None)}

        rows: list[dict] = []
        for url in top_urls[:5]:
            outcome = by_url.get(url)
            search_hit = payload.get(url) or {}

            title = None
            if outcome is not None:
                title = getattr(outcome, "title", None)
            title = title or search_hit.get("title") or _domain_from(url)

            snippet = search_hit.get("snippet") or search_hit.get("description")
            if snippet and len(snippet) > 100:
                snippet = snippet[:97].rstrip() + "…"

            row: dict = {
                "title": str(title)[:80],
                "subtitle": snippet,
                "meta": _domain_from(url),
                "url": url,
            }
            thumb = search_hit.get("thumbnail")
            if thumb:
                row["thumbnail"] = thumb
            rows.append(row)

        if not rows:
            return

        card = {
            "card_type": "list",
            "kind": "source",
            "title": f"Sources for: {question[:50]}" if len(question) > 50 else f"Sources for: {question}",
            "rows": rows,
            "footer": f"{len(top_urls)} sources · {len(outcomes)} read",
        }
        log_event("ui.card", **card, agent="research", turn_id=turn_id)
    except Exception as exc:
        log_event("research.citations_card_error", error=repr(exc))


def _domain_from(url: str) -> str:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return url


# ESPN's unauthenticated scoreboard API. Public, no key, powers espn.com's
# own mobile apps. One endpoint per league — we fan out across the current
# in-season set on every score query. ~200ms round-trip each, fully parallel.
_ESPN_LEAGUES: tuple[tuple[str, str], ...] = (
    ("NBA",  "basketball/nba"),
    ("WNBA", "basketball/wnba"),
    ("NFL",  "football/nfl"),
    ("MLB",  "baseball/mlb"),
    ("NHL",  "hockey/nhl"),
    ("NCAAF", "football/college-football"),
    ("NCAAM", "basketball/mens-college-basketball"),
    ("NCAAW", "basketball/womens-college-basketball"),
    ("MLS",  "soccer/usa.1"),
)

_SPORTS_SCORE_RE = re.compile(
    r"\b(score|scores|winning|losing|leading|ahead|game|match|beating|beat)\b",
    re.IGNORECASE,
)


def _is_sports_score_query(question: str) -> bool:
    """Gate the ESPN path: question must mention a score/game AND reference
    a team, league, or live-sports keyword. Conservative on purpose — we
    don't want 'what's the best score for a midterm' hitting ESPN."""
    if not _SPORTS_SCORE_RE.search(question):
        return False
    lower = question.lower()
    # League abbreviations / sport words as a loose gate. We could ship a
    # huge team-name list, but a league keyword is enough signal to justify
    # the ~200ms parallel ESPN fan-out.
    sport_markers = (
        "nba", "nfl", "mlb", "nhl", "mls", "wnba", "ncaa", "college",
        "basketball", "football", "baseball", "hockey", "soccer",
        "rockets", "lakers", "warriors", "celtics", "heat", "knicks",
        "nets", "bulls", "cavaliers", "bucks", "sixers", "pistons",
        "pacers", "raptors", "thunder", "nuggets", "jazz", "suns",
        "clippers", "kings", "mavericks", "spurs", "pelicans", "grizzlies",
        "timberwolves", "blazers", "yankees", "dodgers", "mets", "red sox",
        "cubs", "giants", "braves", "phillies", "astros", "padres", "cardinals",
        "chiefs", "bills", "49ers", "eagles", "cowboys", "packers", "bears",
        "patriots", "ravens", "bengals", "steelers", "broncos", "raiders",
        "rangers", "bruins", "oilers", "canucks", "maple leafs",
    )
    return any(m in lower for m in sport_markers)


async def _fetch_live_sports_score(question: str, turn_id: str) -> str | None:
    """Query ESPN's scoreboard across major leagues in parallel, pick the
    one event whose team names best match the question, and format a short
    spoken reply. Returns None if no match — the caller falls through to
    the web pipeline."""
    import httpx
    from urllib.parse import quote

    tokens = _extract_team_tokens(question)
    if not tokens:
        return None

    async def _one(league_tag: str, path: str) -> tuple[str, dict | None]:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                return league_tag, None
            return league_tag, resp.json()
        except Exception as exc:
            log_event("research.espn.fetch_error", league=league_tag, error=repr(exc))
            return league_tag, None

    results = await asyncio.gather(*(_one(tag, path) for tag, path in _ESPN_LEAGUES))

    best: tuple[int, str, dict] | None = None
    for league_tag, payload in results:
        if not payload:
            continue
        for event in payload.get("events", []) or []:
            score = _score_event_match(event, tokens)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, league_tag, event)

    if best is None:
        log_event("research.espn.no_match", question=question[:120])
        return None

    score, league_tag, event = best
    spoken = _format_event_spoken(event, league_tag)
    log_event(
        "research.espn.match",
        league=league_tag,
        match_score=score,
        event_name=event.get("shortName", ""),
    )
    _emit_espn_card(turn_id=turn_id, league=league_tag, event=event)
    return spoken


_STOP_TOKENS = frozenset({
    "what", "whats", "what's", "is", "the", "of", "who", "won", "winning",
    "score", "scores", "game", "match", "today", "tonight", "now", "right",
    "currently", "latest", "and", "vs", "versus", "playing", "play", "a",
    "in", "between", "did", "does", "are", "live", "nba", "nfl", "mlb",
    "nhl", "mls", "wnba", "ncaa", "college", "basketball", "football",
    "baseball", "hockey", "soccer", "this", "that", "for", "on", "at",
    "against", "beating", "beat", "be", "me",
})


def _extract_team_tokens(question: str) -> list[str]:
    """Strip question-shape words and return lowercase team-name tokens.
    No proper-noun heuristic (voice transcripts are lowercase); we rely on
    the later match-scoring against ESPN's team names to find the hit."""
    cleaned = re.sub(r"[^\w\s']", " ", question.lower())
    return [t for t in cleaned.split() if len(t) >= 3 and t not in _STOP_TOKENS]


def _score_event_match(event: dict, tokens: list[str]) -> int:
    """Count how many of the query's team-name tokens appear anywhere in
    the event's team names / abbreviations. Returns 0 if no tokens match —
    the caller skips events that score zero."""
    haystack_parts: list[str] = []
    for field in ("name", "shortName"):
        v = event.get(field)
        if isinstance(v, str):
            haystack_parts.append(v.lower())
    for comp in event.get("competitions", []) or []:
        for team in comp.get("competitors", []) or []:
            t = team.get("team") or {}
            for key in ("displayName", "shortDisplayName", "name", "nickname",
                        "location", "abbreviation"):
                v = t.get(key)
                if isinstance(v, str):
                    haystack_parts.append(v.lower())
    haystack = " ".join(haystack_parts)
    return sum(1 for tok in tokens if tok in haystack)


def _format_event_spoken(event: dict, league_tag: str) -> str:
    """Turn an ESPN event dict into one voice-friendly sentence. Distinct
    phrasings for: not-yet-started, in-progress, final. Prices don't belong
    in sports — but tip-off times and period labels do."""
    comp = (event.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        # Fall back to whatever order ESPN returns.
        if len(competitors) >= 2:
            home, away = competitors[0], competitors[1]
        else:
            return f"The {league_tag} game is scheduled but I couldn't read the scoreboard."

    home_name = (home.get("team") or {}).get("displayName") or "home"
    away_name = (away.get("team") or {}).get("displayName") or "away"
    home_score = home.get("score") or "0"
    away_score = away.get("score") or "0"

    status = event.get("status") or {}
    stype = (status.get("type") or {})
    state = stype.get("state")  # "pre" | "in" | "post"

    if state == "pre":
        start = stype.get("shortDetail") or stype.get("detail") or "later today"
        return f"{away_name} at {home_name} hasn't started yet — {start}."

    if state == "post":
        try:
            hs = int(home_score); as_ = int(away_score)
        except ValueError:
            return f"{home_name} {home_score}, {away_name} {away_score}, final."
        if hs > as_:
            return f"{home_name} beat {away_name} {hs} to {as_}."
        if as_ > hs:
            return f"{away_name} beat {home_name} {as_} to {hs}."
        return f"{home_name} and {away_name} tied {hs} to {as_}."

    # In-progress: include period/clock so "the score right now" feels live.
    period_desc = stype.get("shortDetail") or stype.get("detail") or ""
    try:
        hs = int(home_score); as_ = int(away_score)
    except ValueError:
        return f"{home_name} {home_score}, {away_name} {away_score}, {period_desc}."
    leader, l_score, trailer, t_score = (
        (home_name, hs, away_name, as_) if hs >= as_
        else (away_name, as_, home_name, hs)
    )
    if l_score == t_score:
        return f"{home_name} and {away_name} tied {hs} to {as_}, {period_desc}."
    return f"{leader} leads {trailer} {l_score} to {t_score}, {period_desc}."


def _emit_espn_card(*, turn_id: str, league: str, event: dict) -> None:
    """Emit a ui.card so the HUD shows the matchup alongside the spoken
    answer. We render even pre-game and post-game states — the card is
    useful for venue, broadcaster, and final scores you missed live."""
    try:
        comp = (event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        rows: list[dict] = []
        for c in competitors:
            team = c.get("team") or {}
            rows.append({
                "title": team.get("displayName") or team.get("shortDisplayName") or "—",
                "subtitle": (team.get("abbreviation") or "") + (" · home" if c.get("homeAway") == "home" else " · away"),
                "trailing": str(c.get("score") or "—"),
                "thumbnail": team.get("logo"),
            })
        if not rows:
            return
        status = (event.get("status") or {}).get("type") or {}
        footer = status.get("shortDetail") or status.get("detail") or league
        card = {
            "card_type": "list",
            "kind": "source",
            "title": event.get("name") or event.get("shortName") or f"{league} game",
            "rows": rows,
            "footer": footer,
        }
        log_event("ui.card", **card, agent="research", turn_id=turn_id)
    except Exception as exc:
        log_event("research.espn.card_error", error=repr(exc))
