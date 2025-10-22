# mira/core/domain_trust.py
import re
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

# ---- Domain trust maps -----------------------------------------------------
TRUSTED_SPORTS = {
    "espn.com": 6, "nfl.com": 6, "cbssports.com": 5, "sports.yahoo.com": 5,
    "theathletic.com": 4, "si.com": 4, "foxsports.com": 4, "nbcsports.com": 4,
}
TRUSTED_SHOPS = {
    "amazon.com": 6,
    "apple.com": 6,
    "target.com": 4,
    "bhphotovideo.com": 4,
    "costco.com": 4,
    "flipkart.com": 5,
    "reliancedigital.in": 5,
}
TRUSTED_NEWS = {
    "reuters.com": 6, "apnews.com": 6, "bloomberg.com": 6, "wsj.com": 6, "ft.com": 6,
    "nytimes.com": 5, "theguardian.com": 5, "washingtonpost.com": 5,
    "bbc.com": 5, "npr.org": 5, "economist.com": 5,
    "aljazeera.com": 4, "time.com": 4, "axios.com": 4, "semafor.com": 4,
}
TRUSTED_FINANCE = {
    "cnbc.com": 6, "marketwatch.com": 5, "barrons.com": 5, "seekingalpha.com": 4,
    "investopedia.com": 4, "morningstar.com": 4, "nasdaq.com": 4,
    "fool.com": 3, "yahoo.com": 4,  # (finance vertical)
}
TRUSTED_TECH_NEWS = {
    "techcrunch.com": 4, "theverge.com": 4, "arstechnica.com": 4,
    "engadget.com": 3, "wired.com": 4,
}

# Forums / low-signal for prices/news (we still might show them, but de-prioritize hard)
UNTRUSTED = {
    "reddit.com": -12, "old.reddit.com": -12, "quora.com": -12,
    "pinterest.com": -8, "tumblr.com": -6, "medium.com": -3,   # Medium varies; keep mild penalty
    "9to5mac.com": -2,  # sometimes newsy but often rumor-y for prices
    "macrumors.com": -2,  # rumor-heavy; keep mild
    "walmart.com": -8,
    "bestbuy.com": -8,
}

_FORUM_HINTS = ("forum.", "/forum", "/forums/", "/community/", "/thread/", "/threads/")
_VIDEO_HINTS = ("/video/", "youtube.com", "youtu.be", "v.redd.it")

_DATE_WORD = re.compile(
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\w*,?\s+\d{4})",
    re.I
)

# ---- Helpers ---------------------------------------------------------------
def _season_anchor(sport: str, year: int) -> date:
    """Return approximate season start date for a given sport + year."""
    if sport == "nfl":
        return date(year, 9, 5)   # early Sept
    if sport == "nba":
        return date(year, 10, 20) # late Oct
    if sport == "mlb":
        return date(year, 4, 1)   # early Apr
    if sport == "nhl":
        return date(year, 10, 5)  # early Oct
    if sport in ("soccer", "epl", "premier league", "la liga", "serie a", "bundesliga"):
        return date(year, 8, 10)  # mid-Aug
    return date(year, 1, 1)       # fallback to Jan 1

def _tz_now_ny():
    return datetime.now(ZoneInfo("America/New_York"))

def resolve_target_date(text: str):
    """Resolve 'today', 'yesterday', weekday refs, or week+year (sport season) into a date."""
    t = (text or "").lower()
    today = _tz_now_ny().date()

    # 🔹 Natural language (today/yesterday)
    if any(w in t for w in ("yesterday", "last night")):
        return today - timedelta(days=1)
    if any(w in t for w in ("today", "tonight")):
        return today

    # 🔹 Weekday reference
    wd_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6
    }
    for wd, idx in wd_map.items():
        if wd in t:
            delta = (_tz_now_ny().weekday() - idx) % 7
            candidate = today - timedelta(days=delta or 7)
            return candidate

    # 🔹 Week number + season year (e.g., "week 12 of 2013 season")
    year_match = re.search(r"\b(20\d{2})\b", t)
    week_match = re.search(r"week\s+(\d{1,2})", t)
    if year_match and week_match:
        target_year = int(year_match.group(1))
        week_num = int(week_match.group(1))

        # detect sport context
        if "nfl" in t:
            sport = "nfl"
        elif "nba" in t:
            sport = "nba"
        elif "mlb" in t:
            sport = "mlb"
        elif "nhl" in t:
            sport = "nhl"
        elif any(w in t for w in ("soccer", "premier league", "la liga", "serie a", "bundesliga", "epl")):
            sport = "soccer"
        else:
            sport = "nfl"  # default if not specified

        season_start = _season_anchor(sport, target_year)
        return season_start + timedelta(weeks=week_num - 1)

    return None

def host(u: str) -> str:
    """Extract normalized hostname from URL."""
    try:
        h = u.split("/")[2].lower() if "://" in u else u.lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return u


def parse_date_from_text(s: str):
    """Parse a date (YYYY/MM/DD or Month DD, YYYY) from text or URL."""
    if not s:
        return None
    m = _DATE_WORD.search(s)
    if m:
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(m.group(0), fmt).date()
            except Exception:
                pass
    m = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})", s)
    if m:
        y, mo, da = map(int, m.groups())
        try:
            return datetime(y, mo, da).date()
        except Exception:
            pass
    m = re.search(r"\b(20\d{2})\b", s)
    if m:
        try:
            return datetime(int(m.group(1)), 1, 1).date()
        except Exception:
            pass
    return None


def page_type_bonus(url: str, title: str, intent: str) -> int:
    """Apply bonuses/penalties based on URL/title patterns for a given intent."""
    t = (title or "").lower()
    u = (url or "").lower()
    bonus = 0
    if intent == "sports_stats":
        if "boxscore" in u or "box score" in t: bonus += 6
        if "gameid=" in u or "/game/" in u:    bonus += 3
        if "final score" in t or "recap" in u: bonus += 2
    if intent == "price":
        if any(k in t for k in ("price", "buy", "from ₹", "from $", "deal")): bonus += 2
        if any(k in u for k in ("/dp/", "/product/")): bonus += 2
    if intent in ("news", "finance"):
        if any(k in u for k in ("/markets/", "/business/", "/technology/")): bonus += 1
        if any(k in t for k in ("earnings", "guidance", "downgrade", "upgrade", "ipo", "acquires")): bonus += 1
    if any(v in u for v in _VIDEO_HINTS): bonus -= 3
    if any(h in u for h in _FORUM_HINTS): bonus -= 4
    return bonus


def intent_from_query(q: str) -> str:
    """Guess intent type from a query string."""
    t = (q or "").lower()

    # 💰 Price / Shopping
    if any(k in t for k in ("price", "cost", "how much", "₹", "$")):
        return "price"

    # 🏟 Sports
    if any(k in t for k in (
        "stats", "box score", "boxscore", "score", "final score",
        "match", "game", "result", "record"
    )):
        return "sports"

    # 📊 Finance
    if any(k in t for k in (
        "earnings", "guidance", "revenue", "q1", "q2", "q3", "q4",
        "ipo", "downgrade", "upgrade", "stock price", "share price"
    )):
        return "finance"

    # 📰 News
    if any(k in t for k in ("news", "headline", "breaking", "update", "latest")):
        return "news"

    # 🌐 Fallback
    return "search"



def intent_trust_weight(host: str, intent: str, query: str = "") -> int:
    """Assign trust weight for a host depending on intent."""
    host = host.lower()
    q = (query or "").lower()

    if intent == "sports_stats":
        return TRUSTED_SPORTS.get(host, 0)
    if intent == "price":
        base = TRUSTED_SHOPS.get(host, 0)
        if host.endswith("amazon.com") and any(k in q for k in ("lowest", "cheapest", "deal", "discount")):
            base += 2
        return base
    if intent == "finance":
        return max(TRUSTED_FINANCE.get(host, 0), TRUSTED_NEWS.get(host, 0))
    if intent == "news":
        return max(TRUSTED_NEWS.get(host, 0), TRUSTED_TECH_NEWS.get(host, 0))

    return max(
        TRUSTED_SPORTS.get(host, 0),
        TRUSTED_SHOPS.get(host, 0),
        TRUSTED_NEWS.get(host, 0),
        TRUSTED_FINANCE.get(host, 0),
        TRUSTED_TECH_NEWS.get(host, 0),
    )

def score_link(title: str, url: str, target_date, intent: str) -> float:
    """Final weighted score for ranking links."""
    h = host(url)
    trust   = intent_trust_weight(h, intent)
    page    = page_type_bonus(url, title, intent)
    penalty = UNTRUSTED.get(h, 0)

    d_in_link = parse_date_from_text((title or "") + " " + url)
    recency = 0
    if target_date and d_in_link:
        diff = abs((d_in_link - target_date).days)
        recency = max(0, 12 - diff)
    elif intent in ("news", "finance", "sports_stats"):
        recency = 2
    else:
        recency = 1

    return trust * 12 + page * 4 + recency + penalty


__all__ = [
    "host", "resolve_target_date", "parse_date_from_text",
    "intent_from_query", "intent_trust_weight", "page_type_bonus", "score_link",
    "TRUSTED_SPORTS", "TRUSTED_SHOPS", "TRUSTED_NEWS",
    "TRUSTED_FINANCE", "TRUSTED_TECH_NEWS", "UNTRUSTED",
]
