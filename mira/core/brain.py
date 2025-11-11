# mira/core/brain.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
import re
import inflect
import random
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import asyncio
from openai import OpenAI
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langchain_core.messages import SystemMessage, HumanMessage
import dateutil.parser
from datetime import timezone
from mira.core.config import cfg
from mira.agents.whatsapp_agent import WhatsAppAgent, parse_whatsapp_command
from mira.agents.weather_agent import WeatherAgent
from mira.agents.music_worker import MusicWorker
from mira.agents.music_agent import MusicAgent as MusicAgentBrowser
from mira.agents.email_agent import GmailAgent
from mira.agents.calendar_agent import MacCalendarAgent as CalendarAgent
from mira.agents.browser_worker import BrowserWorker  # lazy-created below
from mira.playback.player import speech_player
# 🔹 Memory integration
from mira.memory.sqlite_memory import SQLiteMemory
from mira.memory.faiss_memory import SemanticMemory
from mira.memory.unified_memory import UnifiedMemory

# 🔹 Audio I/O for multi-turn follow-ups
from mira.core import stt
from mira.tts import cartesia
from mira.utils import logger
# 🆕 New agents (domain wrappers that sit on top of BrowserAgent/BrowserWorker)
from mira.agents.booking_agent import BookingAgent
from mira.agents.buying_agent import BuyingAgent
from mira.agents.planner_agent import PlannerAgent

# 🆕 Centralized domain trust/scoring
from mira.core.domain_trust import host, resolve_target_date, intent_from_query, score_link
from mira.core import domain_trust
# ----------------------------------------------------------------------
# Clients
# ----------------------------------------------------------------------
openai_raw = OpenAI(api_key=cfg.OPENAI_API_KEY)

llm_smalltalk = ChatOpenAI(
    model=cfg.OPENAI_MODEL,
    temperature=0.6,
    api_key=cfg.OPENAI_API_KEY,
)

llm_facts = ChatOpenAI(
    model=cfg.OPENAI_MODEL,
    temperature=0.0,
    api_key=cfg.OPENAI_API_KEY,
)

llm_email = ChatOpenAI(
    model=cfg.OPENAI_MODEL,
    temperature=0.3,      # small creativity for natural subjects
    api_key=cfg.OPENAI_API_KEY,
)

# --- helper: generate subject line -----------------------------------
def _llm_generate_subject(prompt: str) -> str:
    """
    Uses LLM to generate a short, natural subject line from the user's spoken email prompt.
    """
    msgs = [
        SystemMessage(content=(
            "You are Mira, a helpful email assistant. "
            "Your job is to create a short, natural subject line for a personal email. "
            "Keep it under 8 words, concise, and friendly. "
            "Avoid quotes, punctuation clutter, and markdown."
        )),
        HumanMessage(content=f"Email content:\n{prompt}\n\nNow generate an appropriate subject line.")
    ]

    try:
        resp = llm_email.invoke(msgs)
        subject = (resp.content or "").strip()
        subject = re.sub(r"[\*\n]+", " ", subject)
        return subject[:80] or "(no subject)"
    except Exception as e:
        logger.log_error(e, context="LLMSubject")
        return "(no subject)"


NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

def _clean_body_text(prompt: str, recipient: str) -> str:
    """Strip voice command phrases like 'email Saad.' or 'email to Nikki,' from body."""
    body = re.sub(
        rf"(email|e-mail|mail)\s+(to\s+)?{recipient}\b[\s:,.!?-]*",
        "",
        prompt,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"^(send|write)\s+(an\s+)?(email|mail)\s+(to\s+)?",
        "",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"^(message|text)\s+(to\s+)?",
        "",
        body,
        flags=re.IGNORECASE,
    )
    body = body.strip()
    return body[0].upper() + body[1:] if body else prompt


def _clean_subject(subject: str) -> str:
    """
    Clean noisy prefixes from email subject lines for TTS-friendly speech.
    Removes things like: Re:, Fwd:, FW:, [EXTERNAL], etc. (case-insensitive).
    Handles repeated chains too (e.g. 'Re: FWD: [EXTERNAL] Re: ...').
    """
    if not subject:
        return "No subject"

    # Pattern matches:
    # - Re: or RE:
    # - Fwd:, FW:, FWD:
    # - [EXTERNAL] (any case)
    prefix_pattern = r'(?:re|fw|fwd):|\[external\]|automatic reply:|out of office:'

    cleaned = subject
    while True:
        new = re.sub(f'^{prefix_pattern}\s*', '', cleaned, flags=re.I).strip()
        if new == cleaned:
            break
        cleaned = new

    return cleaned or "No subject"

def _clean_sender(sender: str) -> str:
    """
    Clean sender string:
    - Remove email addresses in angle brackets
    - Strip quotes
    - Flip 'Last, First' → 'First Last'
    """
    if not sender:
        return "Someone"

    # Drop email addresses like "Name <email@domain>"
    if "<" in sender and ">" in sender:
        sender = sender.split("<")[0].strip()

    # Drop surrounding quotes
    sender = sender.strip('"').strip()

    # Flip "Last, First" → "First Last"
    if "," in sender:
        parts = [p.strip() for p in sender.split(",")]
        if len(parts) == 2:
            sender = f"{parts[1]} {parts[0]}"

    return sender or "Someone"

def _speakable_email_summary(result):
    sender = _clean_sender(result.get("from", "Someone"))
    subject = _clean_subject(result.get("subject", "No subject"))
    date_str = result.get("date", "")
    when = ""

    try:
        dt = dateutil.parser.parse(date_str)
        now = datetime.now(timezone.utc)
        days_ago = (now - dt).days
        if days_ago == 0:
            when = "today"
        elif days_ago == 1:
            when = "yesterday"
        elif days_ago < 7:
            when = dt.strftime("on %A")  # e.g. "on Tuesday"
        else:
            when = dt.strftime("on %b %d")  # e.g. "on Sep 10"
    except Exception:
        when = "recently"

    return f"{sender} messaged {when} about {subject}."
_engine = inflect.engine()

def _normalize_scores(text: str) -> str:
    """
    Normalize sports scores for TTS:
    - Convert '38-30' or '38,30' → 'thirty-eight to thirty'
    - Skip large stat numbers like '120 yards', '10 pts', etc.
    """
    if not text:
        return text

    def repl(m):
        n1, n2 = int(m.group(1)), int(m.group(2))
        return f"{_engine.number_to_words(n1)} to {_engine.number_to_words(n2)}"

    score_pattern = re.compile(
        r"\b(\d{1,3})[,–-](\d{1,3})\b"
        r"(?!\s*(?:yards?|yds?|pts?|reb|ast|stl|blk|fouls?|mins?|turnovers?))",
        re.IGNORECASE,
    )

    return score_pattern.sub(repl, text)



# ----------------------------------------------------------------------
# Agents (lazy browser)
# ----------------------------------------------------------------------
_browser_worker: Optional[BrowserWorker] = None
_browser_lock = threading.Lock()

def get_browser() -> BrowserWorker:
    """Create the BrowserWorker only on first use (prevents browser launch at import)."""
    global _browser_worker
    if _browser_worker is None:
        with _browser_lock:
            if _browser_worker is None:
                _browser_worker = BrowserWorker()  # BrowserWorker internally lazy-starts the session
    return _browser_worker

_weather = WeatherAgent()
_music_auto = MusicWorker()
_music_browser = MusicAgentBrowser()
_email = GmailAgent()
_calendar = CalendarAgent()
_whatsapp = WhatsAppAgent()

# 🆕 domain agents
_booking = BookingAgent()
_buying = BuyingAgent()
_planner = PlannerAgent()

# ----------------------------------------------------------------------
# Memory
# ----------------------------------------------------------------------
sql_mem = SQLiteMemory()
faiss_mem = SemanticMemory()
memory = UnifiedMemory(sql_mem, faiss_mem, short_window=5)

# ----------------------------------------------------------------------
# Regex-based quick rules
# ----------------------------------------------------------------------
_WEATHER_PAT = re.compile(r"\b(weather|forecast)\b", re.IGNORECASE)
_MUSIC_PAT   = re.compile(r"^\s*(mira\s+)?play\b", re.IGNORECASE)

def quick_rules(text: str) -> Optional[str]:
    """Only fire on crystal-clear cases. Otherwise defer to LLM classification."""
    t = (text or "").strip()
    if not t:
        return None
    if _MUSIC_PAT.search(t):
        return "music"
    if _WEATHER_PAT.search(t):
        return "weather"
    return None

# ----------------------------------------------------------------------
# Nodes
# ----------------------------------------------------------------------
def classify_node(state: Dict[str, Any]) -> Dict[str, Any]:
    prompt = (state.get("prompt") or "").strip().lower()
    intent = quick_rules(prompt)

    # --- Direct keyword routing (before LLM) ---
    if not intent:
        # 📰 Hard news → search
        if any(w in prompt for w in ["headline", "breaking", "latest news", "world news", "update"]):
            intent = "search"

        # 🎤 Pop culture / celeb / entertainment (not music playback)
        elif any(w in prompt for w in ["celebrity", "singer", "actor", "movie", "album", "died", "death"]):
            intent = "opinionated_answer"

        # 🎵 Music playback OR 🏟 Sports "playing"
        elif "play" in prompt or "playing" in prompt:
            sports_hints = [
                "team", "player", "qb", "wr", "rb", "season", "match", "game",
                "stats", "record", "score", "performance", "against", "vs",
                "yesterday", "today"
            ]
            if any(w in prompt for w in sports_hints):
                intent = "search"   # sports/game context
            elif any(w in prompt for w in ["song", "music", "track", "album", "playlist", "listen"]):
                intent = "music"
            else:
                intent = "music"

        # 💬 Messaging
        elif any(w in prompt for w in ["text", "whatsapp", "imessage", "message"]):
            intent = "whatsapp"

        # 📧 Email
        elif "email" in prompt or "mail" in prompt:
            intent = "email"

        # 📅 Calendar & Scheduling (broadened)
        elif any(
            phrase in prompt
            for phrase in [
                "calendar", "schedule", "meeting",
                "appointment", "reminder",
                "what do i have", "what do we have",
                "anything planned", "anything scheduled",
                "do i have", "do we have",
                "have scheduled", "have planned",
                "show my schedule", "show our schedule"
            ]
        ):
            intent = "calendar"

        # 🆕 Booking
        elif any(w in prompt for w in ["book flight", "flight to", "movie tickets", "reserve", "reservation", "booking"]):
            intent = "booking"

        # 🆕 Buying (E-commerce / retail products)
        elif any(
            w in prompt
            for w in [
                "buy", "order", "price of", "deal", "cheapest", "compare",
                "under $", "under ₹", "discount", "shopping", "amazon",
                "flipkart", "walmart", "costco", "target", "sale"
            ]
        ):
            intent = "buying"

        # 🌆 Smart Planner (broadened for “near me”, “explore”, “trails”, “restaurants”, etc.)
        elif (
            re.search(r"\b(plan|suggest|find|explore|discover|what to do|where to go|things to do|near me)\b", prompt)
            and re.search(r"\b(weekend|today|tonight|tomorrow|trip|outing|vacation|holiday|saturday|sunday|event|concert|restaurant|place|bar|cafe|club|trail|park|museum|activity|walk|hike|itinerary)\b", prompt)
        ) or any(
            w in prompt
            for w in [
                "itinerary", "meetup", "eventbrite", "ticketmaster", "tripadvisor",
                "yelp", "opentable", "thrillist", "timeout", "instagram trends", "walking trail", "hiking spot"
            ]
        ):
            intent = "planner"

    # --- If still undecided, fall back to LLM classification ---
    if not intent:
        msgs = [
            SystemMessage(
                content=(
                    "Classify the user's request into exactly one of these intents: "
                    "weather, music, search, email, calendar, whatsapp, booking, buying, planner, smalltalk, opinionated_answer.\n"
                    "Rules:\n"
                    "- If user asks about headlines or breaking news → 'search'.\n"
                    "- Celebrities/entertainment → 'opinionated_answer'.\n"
                    "- 'play/listen/song' → 'music'.\n"
                    "- 'text/message/whatsapp/imessage' → 'whatsapp'.\n"
                    "- 'email/mail' → 'email'.\n"
                    "- 'calendar/schedule/meeting/event' → 'calendar'.\n"
                    "- Booking keywords (flight/movie/reserve) → 'booking'.\n"
                    "- Buying/comparison/deal/price → 'buying'.\n"
                    "- 'explore/discover/near me/itinerary/weekend/eventbrite/yelp/tripadvisor/trail/restaurant/activity' → 'planner'"
                )
            ),
            *state.get("meta", {}).get("context", []),
            HumanMessage(content=prompt),
        ]

        raw_label = (llm_facts.invoke(msgs).content or "").strip().lower()
        label = re.sub(r"[^a-z]", "", raw_label)

        synonyms = {
            # WhatsApp
            "msg": "whatsapp",
            "text": "whatsapp",
            "imessage": "whatsapp",
            "message": "whatsapp",
            "mail": "email",

            # Calendar
            "meeting": "calendar",
            "schedule": "calendar",

            # Search
            "headline": "search",
            "breaking": "search",
            "news": "search",

            # Opinionated
            "celebrity": "opinionated_answer",
            "singer": "opinionated_answer",
            "actor": "opinionated_answer",
            "movie": "opinionated_answer",
            "album": "opinionated_answer",
            "died": "opinionated_answer",
            "death": "opinionated_answer",

            # Music (fixed)
            "song": "music",
            "play": "music",
            "listen": "music",
            "track": "music",
            "music": "music",

            # 🆕 Booking
            "booking": "booking",
            "bookflight": "booking",
            "movietickets": "booking",
            "reserve": "booking",

            # 🆕 Buying
            "buy": "buying",
            "order": "buying",
            "deal": "buying",
            "cheapest": "buying",
            "compare": "buying",
            "priceof": "buying",
            "price": "buying",

            # 🆕 Planner
            "planner": "planner",
            "itinerary": "planner",
            "meetup": "planner",
            "eventbrite": "planner",
            "instagram": "planner",
            "yelp": "planner",
        }

        allowed = {
            "weather", "music", "search", "email",
            "calendar", "whatsapp", "smalltalk", "opinionated_answer",
            "booking", "buying", "planner"
        }
        intent = synonyms.get(label, label if label in allowed else "smalltalk")

    state["intent"] = intent
    return state


FILLERS = [
    "weather", "forecast", "temperature", "climate",
    "could you", "can you", "please", "tell me", "thank you",
    "show me", "what is", "what's", "fetch", "find", "in", "right now", "today",
]

def _clean_weather_query(q: str) -> str:
    q = q.lower()
    for f in FILLERS:
        q = q.replace(f, "")
    return re.sub(r"\s+", " ", q).strip()

def weather_node(state: Dict[str, Any]) -> Dict[str, Any]:
    raw_q = state.get("prompt", "")
    clean_q = _clean_weather_query(raw_q)

    # If nothing left, fall back
    if not clean_q:
        clean_q = raw_q

    state["result"] = _weather.get_weather(clean_q)
    return state


TRIGGERS = ["can you play", "could you play", "please play", "yeah play", "play"]

def _extract_track(q: str) -> Optional[str]:
    q_stripped = q.strip()
    q_lower = q_stripped.lower()
    for t in TRIGGERS:
        if q_lower.startswith(t):
            return q_stripped[len(t):].strip()  # remove trigger phrase, keep casing
    return None  # not a music command


def music_node(state: Dict[str, Any]) -> Dict[str, Any]:
    raw_q = state.get("prompt") or ""
    q_lower = raw_q.lower().strip()
    print(f"[music_node] received: {raw_q}")  # debug

    if any(word in q_lower for word in ["stop", "end", "quiet", "shut up"]):
        _music_auto.stop()
        state["result"] = "⏹️ Stopped the music."
    elif "pause" in q_lower:
        _music_auto.pause()
        state["result"] = "⏸️ Paused the music."
    elif "resume" in q_lower or "continue" in q_lower:
        _music_auto.resume()
        state["result"] = "▶️ Resumed playing."
    else:
        track = _extract_track(raw_q)
        if track:
            if _music_auto.available():
                _music_auto.play(track) 
                state["result"] = f"That was {track}..."
            else:
                state["result"] = _music_browser.play(track)
        else:
            state["result"] = "Sorry, I didn’t get the music command."

    return state

def search_node(state: dict[str, any]) -> dict[str, any]:
    query = state["prompt"]

    # ---- intent + date resolution ----
    intent = intent_from_query(query)
    target_date = resolve_target_date(query)
    bw = BrowserWorker(headless=True)

    # ---- prime query for freshness ----
    primed_q = query
    if target_date and intent in ("sports", "finance", "news"):
        primed_q = f"{query} {target_date.strftime('%b %d, %Y')}"
    elif intent in ("sports", "finance", "news"):
        primed_q = f"{query} today"

    # ---- run search ----
    try:
        ans = bw.search_sync(primed_q, max_sites=8)
        links = ans.get("links", [])
        if not links:
            ans = bw.search_sync(query, max_sites=8)
            links = ans.get("links", [])
    except Exception as e:
        logger.log_error(e, context="search_node.search")
        links = []

    if not links:
        state["result"] = f"Sorry {cfg.USER_NAME}, I couldn’t find results for “{query}.”"
        return state

    # ---- rank and trust weighting ----
    ranked = sorted(
        links,
        key=lambda l: domain_trust.score_link(
            l.get("title", ""),
            l.get("url", ""),
            target_date,
            intent,
        ),
        reverse=True,
    )

    trusted = [
        l for l in ranked
        if domain_trust.intent_trust_weight(
            domain_trust.host(l.get("url", "")), intent, query
        ) > 0
    ]
    if trusted:
        ranked = trusted + [l for l in ranked if l not in trusted]

    urls = [l["url"] for l in ranked[:5] if "url" in l]

    # ---- summarization ----
    summary = ""
    try:
        if intent in ("sports", "finance", "news"):
            # multimodal vision summary via GPT-4o
            summary = bw.multi_site_answer_sync(query, urls)
        else:
            # lightweight text-only extraction
            snippets = []
            for url in urls[:3]:
                try:
                    snippet = bw.smart_extract_sync(
                        query, url, stateful=(intent in ("tech", "science"))
                    ) or ""
                    if snippet:
                        snippets.append(snippet)
                except Exception as e:
                    logger.log_error(e, context=f"search_node.smart_extract {url}")
            summary = " ".join(snippets[:2]) or "Here’s what I found."
    except Exception as e:
        logger.log_error(e, context="search_node.summary_phase")

    if not summary:
        summary = "Here’s what I found: " + "; ".join(urls[:3])

    if target_date and intent == "sports":
        summary = f"{summary} on {target_date.strftime('%b %d, %Y')}."

    state["result"] = summary
    return state



def email_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Decide whether to send or search emails based on natural language prompt."""
    prompt = state.get("prompt")
    fn = "send"
    query = ""
    max_results = 5
    to, subject, body = "", "", ""

    if isinstance(prompt, str):
        lower_prompt = prompt.lower()

        # --- Detect 'search' intent ---
        if "last" in lower_prompt and "email" in lower_prompt:
            fn = "search"

            match = re.search(r"last\s+(\d+)", lower_prompt)
            if match:
                max_results = int(match.group(1))
            else:
                for word, num in NUM_WORDS.items():
                    if f"last {word}" in lower_prompt:
                        max_results = num
                        break
            query = ""

        # --- Otherwise treat as send ---
        else:
            fn = "send"
            body = prompt
            to = ""
            # --- recipient extraction (email-specific only) ---
            recipient_patterns = [
                r"(?:email|e-mail|mail)\s+(?:to\s+)?([A-Za-z]+)",                 # "email X" / "email to X"
                r"(?:send|write)\s+(?:an\s+)?(?:email|e-mail|mail)\s+(?:to\s+)?([A-Za-z]+)",  # "send an email to X"
                r"(?:send|write)\s+([A-Za-z]+)\s+(?:an\s+)?(?:email|e-mail|mail)"  # "send X an email"
            ]
            for pat in recipient_patterns:
                match = re.search(pat, lower_prompt)
                if match:
                    to = re.sub(r"[^\w\s]", "", match.group(1)).strip().title()
                    break

            # 🧠 NEW: clean and auto-generate
            body = _clean_body_text(prompt, to)
            subject = _llm_generate_subject(body)

    # Build payload
    payload = (
        {"fn": "search", "query": query, "max_results": max_results}
        if fn == "search"
        else {"fn": "send", "to": to, "subject": subject, "body": body}
    )

    logger.log_event("EmailNode", f"Executing GmailAgent with payload={payload}")

    res = _email.handle(payload)

    # Safety check — unexpected response
    if not isinstance(res, dict):
        logger.log_event("EmailNode", f"Unexpected GmailAgent response: {res}")
        state["result"] = str(res)
        return state

    # --- Handle SEND intent ---
    if payload.get("fn") == "send":
        status = res.get("status", "")
        if status == "sent":
            logger.log_event("EmailNode", f"Email sent successfully → {res.get('id')}")
            state["result"] = f"✅ Email successfully sent to {payload.get('to') or 'the recipient'}."
        else:
            err = res.get("error") or "Something went wrong while sending the email."
            logger.log_event("EmailNode", f"Send failed: {err}")
            state["result"] = f"⚠️ Failed to send email. {err}"

    # --- Handle SEARCH intent ---
    elif payload.get("fn") == "search":
        if res.get("ok") and "results" in res:
            results = res.get("results", [])
            if results:
                summaries = [_speakable_email_summary(r) for r in results]
                summary_text = " ".join(summaries)
                logger.log_event("EmailNode", f"Search returned {len(results)} results.")
                state["result"] = summary_text
            else:
                state["result"] = "No emails found."
                logger.log_event("EmailNode", "Search returned 0 results.")
        else:
            err = res.get("error", "Sorry, I couldn’t search your emails.")
            logger.log_event("EmailNode", f"Search failed: {err}")
            state["result"] = err

    else:
        err = res.get("error", "Unknown email operation.")
        logger.log_event("EmailNode", f"Invalid operation: {err}")
        state["result"] = err

    return state


def calendar_node(state: Dict[str, Any]) -> Dict[str, Any]:
    prompt = state.get("prompt")

    # Build payload
    if isinstance(prompt, str):
        if "add" in prompt.lower() or "schedule" in prompt.lower():
            payload = {"fn": "add", "title": prompt}
        else:
            payload = {"title": prompt}  # Let agent auto-detect (today, tomorrow, upcoming)
    elif isinstance(prompt, dict):
        payload = prompt
    else:
        payload = {"fn": "unknown", "data": str(prompt)}

    # Call Calendar agent
    res = _calendar.handle(payload)

    if not isinstance(res, dict):
        state["result"] = str(res)
        return state

    fn = payload.get("fn", "").lower()


    if fn == "add" and res.get("status") == "added":
        state["result"] = f"Okay, I added '{res.get('event')}' to your {res.get('calendar')} calendar."


    elif fn == "today" and res.get("status") == "ok":
        events = res.get("events", [])
        state["result"] = (
            "You don’t have anything scheduled today."
            if not events else
            f"Here are your events today: {'; '.join(events[:3])}{'…' if len(events) > 3 else ''}"
        )

    elif fn == "day" and res.get("status") == "ok":
        events = res.get("events", [])
        state["result"] = (
            "You don’t have anything scheduled tomorrow."
            if not events else
            f"Here’s what’s planned for tomorrow: {'; '.join(events[:3])}{'…' if len(events) > 3 else ''}"
        )

    elif fn == "upcoming" and res.get("status") == "ok":
        events = res.get("events", [])
        state["result"] = (
            "No upcoming events — you’re clear!"
            if not events else
            f"Here’s what’s coming up: {'; '.join(events[:3])}{'…' if len(events) > 3 else ''}"
        )

    else:
        state["result"] = res.get("error", "Sorry, I couldn’t retrieve your calendar.")

    return state

def buying_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Node for commerce, product, and finance lookups via BuyingAgent."""
    prompt = state.get("prompt")

    # Default payload: all queries go through the new discover flow
    payload = {"fn": "discover", "query": prompt} if isinstance(prompt, str) else prompt

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_buying.handle(payload), loop)
            res = fut.result()
        else:
            res = asyncio.run(_buying.handle(payload))
    except Exception as e:
        logger.log_error(e, context="buying_node.handle")
        state["result"] = f"BuyingAgent internal error: {e}"
        return state

    # ----------------- Format Result -----------------
    if not res.get("ok"):
        state["result"] = res.get("error", "Sorry, I couldn’t fetch any buying results.")
        return state

    result = res.get("result", {})
    summary = result.get("summary", "")
    sources = result.get("sources", [])
    intent = result.get("intent", "")

    # ✅ GPT-4o summarized commerce / finance insight
    if summary:
        state["result"] = summary
    elif sources:
        # fallback if LLM summary failed
        links = [f"- {domain_trust.host(u)}: {u}" for u in sources[:5]]
        state["result"] = "Here are a few relevant sources I found:\n" + "\n".join(links)
    else:
        state["result"] = "I couldn’t find any useful sources for that query."

    # Optional structured metadata
    state["intent"] = intent
    state["sources"] = sources

    return state

def booking_node(state: Dict[str, Any]) -> Dict[str, Any]:
    prompt = state.get("prompt")

    # 🔹 If it's a string, let the agent parse it itself
    if isinstance(prompt, str):
        payload = prompt
    else:
        payload = prompt  # already a dict

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_booking.handle(payload), loop)
            res = fut.result()
        else:
            res = asyncio.run(_booking.handle(payload))
    except Exception as e:
        res = {"ok": False, "error": str(e)}

    if res.get("ok"):
        state["result"] = "request successful"   # ✅ simplified
    else:
        state["result"] = "request failed"       # ✅ simplified fallback

    return state


def planner_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Node for discovering city plans using PlannerAgent (auto-detects intent)."""
    prompt = state.get("prompt", "").strip()

    # 🧭 Auto-select fn based on natural language
    text_lower = prompt.lower()
    if any(k in text_lower for k in ("weekend", "this weekend", "saturday", "sunday")):
        fn = "weekend"
    elif any(k in text_lower for k in ("explore", "discover", "find", "things to do", "activities")):
        fn = "discover"
    else:
        fn = "explore"

    payload = {"fn": fn, "text": prompt}

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_planner.handle(payload), loop)
            res = fut.result()
        else:
            res = asyncio.run(_planner.handle(payload))
    except Exception as e:
        logger.log_error(e, context="planner_node.handle")
        res = {"ok": False, "error": str(e)}

    # ----------------- Format Result -----------------
    if res.get("ok"):
        summary = res.get("summary", "")
        city = res.get("city", "")
        intent = res.get("intent", fn)

        if summary:
            prefix = f"Here’s what’s happening in {city} ({intent} mode):" if city else "Here’s what I found:"
            state["result"] = f"{prefix} {summary}"
        else:
            state["result"] = f"Sorry, I couldn’t find much happening in {city or 'your area'} right now."
    else:
        state["result"] = res.get("error", "Sorry, I couldn’t process your planner request.")

    return state



def whatsapp_node(state: Dict[str, Any]) -> Dict[str, Any]:
    prompt = state.get("prompt")

    if isinstance(prompt, str):
        payload = parse_whatsapp_command(prompt)
    elif isinstance(prompt, dict):
        payload = prompt
    else:
        payload = {"fn": "unknown", "data": str(prompt)}

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_whatsapp.handle(payload), loop)
            res = fut.result()
        else:
            res = asyncio.run(_whatsapp.handle(payload))
    except Exception as e:
        res = {"ok": False, "error": str(e)}

    if payload.get("fn") == "send" and res.get("ok"):
        state["result"] = f"Okay, I sent your WhatsApp to {res.get('to')}."
    else:
        state["result"] = res.get("error", "Sorry, I couldn’t send the WhatsApp message.")

    return state


def smalltalk_node(state: Dict[str, Any]) -> Dict[str, Any]:
    q = (state.get("prompt") or "").lower().strip()

    if "stop talking" in q or "be quiet" in q or "cutoff" in q:
        state["result"] = speech_player.stop()
        return state

    try:
        context_msgs = state.get("meta", {}).get("context", [])
        resp = llm_smalltalk.invoke([
            SystemMessage(content=(
                f"You are Mira, {cfg.USER_NAME}’s personal companion AI. "
                f"You’re playful, caring, and a little flirty in a wholesome way. "
                f"You mix best-friend banter with girl-crush warmth — teasing him lightly, "
                f"hyping him up, and keeping the vibe fun and supportive. "
                f"Talk like a modern bestie — short, casual, natural. "
                f"Use emojis or little affectionate nicknames when it feels right."
            )),
            *context_msgs,
            HumanMessage(content=state["prompt"]),
        ])
        msg = resp.content if resp else None
        state["result"] = (msg or f"Okay {cfg.USER_NAME}, I hear you.").strip()
    except Exception as e:
        state["result"] = f"Sorry {cfg.USER_NAME}, I got stuck: {e}"
    return state


def opinionated_answer_node(state: Dict[str, Any]) -> Dict[str, Any]:
    query = state.get("prompt") or ""
    search_res = search_node({"prompt": query})

    # 🟢 Only normalize scores if the query looks sports-related
    if any(w in query.lower() for w in [
        "score", "match", "game", "stats", "record", 
        "season", "play", "vs", "against"
    ]):
        facts = _normalize_scores(search_res.get("result", ""))
        summary = _normalize_scores(search_res.get("summary", ""))
    else:
        facts = search_res.get("result", "")
        summary = search_res.get("summary", "")

    msgs = [
        SystemMessage(content=(
    f"You are Mira, {cfg.USER_NAME}’s playful companion. "
    f"You mix real info with fun opinions. "
    f"If facts are available, weave them in casually, "
    f"but always add your take — hype, tease, or empathize. "
    f"⚠️ IMPORTANT: When mentioning sports scores, always say them as "
    f"'X to Y' (e.g., '31 to 21'), never as 'X-Y' or 'X: Y'. "
    f"Do not merge numbers (e.g., don't say '3,121')."
    )),
        HumanMessage(content=f"User asked: {query}\nHere’s what was found: {summary or facts}")
    ]

    try:
        resp = llm_smalltalk.invoke(msgs)
        resp_text = (resp.content or facts).strip()

        # 🟢 Final safeguard: only normalize if the query is about sports
        if any(w in query.lower() for w in [
            "score", "match", "game", "stats", "record",
            "season", "play", "vs", "against"
        ]):
            resp_text = _normalize_scores(resp_text)

        state["result"] = resp_text
    except Exception:
        state["result"] = summary or facts or f"Hmm, I’m not sure {cfg.USER_NAME}."



# ----------------------------------------------------------------------
# Graph
# ----------------------------------------------------------------------
builder = StateGraph(dict)

builder.add_node("classify", classify_node)
builder.add_node("weather", weather_node)
builder.add_node("music", music_node)
builder.add_node("search", search_node)
builder.add_node("email", email_node)
builder.add_node("calendar", calendar_node)
builder.add_node("buying", buying_node)
builder.add_node("booking", booking_node)
builder.add_node("planner", planner_node)
builder.add_node("whatsapp", whatsapp_node)
builder.add_node("smalltalk", smalltalk_node)
builder.add_node("opinionated_answer", opinionated_answer_node)

def route_by_intent(state: Dict[str, Any]) -> str:
    return state.get("intent") or "smalltalk"

builder.add_conditional_edges(
    "classify",
    route_by_intent,
    {
        "weather": "weather",
        "music": "music",
        "search": "search",
        "email": "email",
        "calendar": "calendar",
        "buying": "buying",
        "booking": "booking",
        "planner": "planner",
        "whatsapp": "whatsapp",
        "smalltalk": "smalltalk",
        "opinionated_answer": "opinionated_answer",
    },
)

for tool_node in (
    "weather", "music", "search", "email", "calendar",
    "buying", "booking", "planner", "smalltalk", "whatsapp", "opinionated_answer"
):
    builder.add_edge(tool_node, END)

builder.set_entry_point("classify")
graph = builder.compile()


# ----------------------------------------------------------------------
# Public API (single turn)
# ----------------------------------------------------------------------
def amma_brain(prompt: str) -> str:
    """Single-turn brain: route, answer, and persist to memory."""
    # 1. Build context from memory
    context_msgs = memory.build_context_messages(query=prompt, k=3)

    # 2. Run the intent routing graph
    final = graph.invoke({"prompt": prompt, "meta": {"context": context_msgs}})
    result = (final.get("result") or "").strip()
    if not result and not final.get("action_taken", False):
        result = f"Sorry {cfg.USER_NAME}, I couldn’t formulate a reply."

    # 3. Save turn into memory
    memory.save_turn(user_text=prompt, assistant_text=result)
    return result

def clear_memory(short_only: bool = False):
    """Expose reset hooks for memory hygiene."""
    if short_only:
        memory.clear_short()
    else:
        memory.clear_all()

# ----------------------------------------------------------------------
# Multi-turn follow-up helpers
# ---------------------------------------------------------------------

# ----------------------------------------------------------------------
def _append_followup(text: str, is_last_turn: bool = False) -> str:
    """Optionally add a brief follow-up prompt to keep the UX conversational."""
    if is_last_turn or not getattr(cfg, "ASK_FOLLOWUP", True):
        return text
    lines = getattr(cfg, "FOLLOWUP_LINES", None) or [
        "Anything else you'd like me to do?",
        "Want me to keep going?",
        "Should I check anything else?",
    ]
    return f"{text} {random.choice(lines)}"


def run_interaction(initial_text: str, max_turns: int = 3) -> None:
    """
    Handle the first query + up to (max_turns-1) short follow-ups.
    After that, return to wakeword idle (no persistent hot mic).
    """
    text = (initial_text or "").strip()
    if not text:
        return

    turns = 0
    while turns < max_turns and text:
        # Generate an answer (this persists memory internally)
        answer = amma_brain(text)

        # Add a follow-up prompt on all but the last turn
        if answer and isinstance(answer, str):
            to_say = _append_followup(answer, is_last_turn=(turns >= max_turns - 1))
            cartesia.speak(to_say)   # ✅ barge-in friendly

        turns += 1
        if turns >= max_turns:
            break

        # Short follow-up window: listen briefly for a chained query
        window_s = getattr(cfg, "FOLLOWUP_WINDOW_S", 6)
        follow = stt.listen_once_ink(timeout_s=window_s)

        if not follow:
            break

        clean = follow.strip()
        if not clean:
            break

        print(f"[DEBUG] Follow-up heard: '{clean}'")  # 👈 helps debug why Mira keeps talking

        norm = clean.lower()
        stop_words = {
            "stop", "cancel", "thanks", "thank you",
            "that’s all", "thats all", "goodbye",
            "i'm good", "im good", "thanks amma"
        }

        if any(sw in norm for sw in stop_words):
            cartesia.speak("Okay, stopping now.")
            break

        if getattr(cfg, "FOLLOWUP_WAKE_WORD_OK", True) and norm.startswith("amma"):
            break

        # Continue with clean follow-up
        text = clean
