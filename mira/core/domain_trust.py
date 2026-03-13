# mira/core/domain_trust.py
import re
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import geocoder 

# ================================================================
# 🌐 Domain Trust Maps (Updated 2025 Edition)
# ================================================================

# ---- Sports -----------------------------------------------------
TRUSTED_SPORTS = {
    "espn.com": 6, "nfl.com": 6, "cbssports.com": 5, "sports.yahoo.com": 5,
    "theathletic.com": 4, "si.com": 4, "foxsports.com": 4, "nbcsports.com": 4,
}

# ---- Retail / Shopping ------------------------------------------
TRUSTED_SHOPS = {
    # 🏪 Core Retailers
    "apple.com": 6,
    "target.com": 4,
    "bhphotovideo.com": 4,
    "costco.com": 4,
    "flipkart.com": 5,
    "reliancedigital.in": 5,

    # 👟 Sneakers / Fashion
    "stockx.com": 6,
    "goat.com": 6,
    "farfetch.com": 5,
    "ssense.com": 5,
    "onitsukatiger.com": 5,
    "nike.com": 5,
    "adidas.com": 5,
    "puma.com": 4,
    "footlocker.com": 4,

    # 💻 Certified Refurb & Used Tech
    "apple.com": 6,             # Certified Refurbished section
    "dellrefurbished.com": 6,
    "lenovo.com": 5,
    "hp.com": 5,
    "reebelo.com": 6,
    "backmarket.com": 5,
    "refurbed.com": 5,
    "refurbed.eu": 5,
    "gazelle.com": 4,
    "swappa.com": 4,
    "refurb.me": 3,
    "bestbuy.com": 3,
    "amazon.com": 3,            # renewed products

    # 🗽 Regional Refurb (US / NYC)
    "paymore.com": 5,
    "computeroverhauls.com": 5,
    "techable.com": 6,
    "plugtech.com": 5,
    "pcliquidations.com": 4,

    # 🧭 Added — Consumer Tech Review / Comparison / Retail Guides
    "cnet.com": 5,
    "techradar.com": 5,
    "tomsguide.com": 5,
    "laptopmag.com": 5,
    "digitaltrends.com": 5,
    "theverge.com": 5,
    "wired.com": 5,
    "arstechnica.com": 4,
    "rtings.com": 5,
    "notebookcheck.net": 5,
    "pocket-lint.com": 4,
    "trustedreviews.com": 4,
    "consumerreports.org": 5,
    "techspot.com": 4,
    "pcmag.com": 5,
    "newegg.com": 5,
    "microcenter.com": 5,
}


# ---- News / Media -----------------------------------------------
TRUSTED_NEWS = {
    "reuters.com": 6, "apnews.com": 6, "bloomberg.com": 6, "wsj.com": 6, "ft.com": 6,
    "nytimes.com": 5, "theguardian.com": 5, "washingtonpost.com": 5,
    "bbc.com": 5, "npr.org": 5, "economist.com": 5,
    "aljazeera.com": 4, "time.com": 4, "axios.com": 4, "semafor.com": 4,
}

# ---- Finance ----------------------------------------------------
TRUSTED_FINANCE = {
    "cnbc.com": 6, "marketwatch.com": 5, "barrons.com": 5, "seekingalpha.com": 4,
    "investopedia.com": 4, "morningstar.com": 4, "nasdaq.com": 4,
    "fool.com": 3, "yahoo.com": 4,
}

# ---- Tech / Startup News ----------------------------------------
TRUSTED_TECH_NEWS = {
    # 🧠 Core Tech & Startup Journalism (Tier 5–6)
    "techcrunch.com": 6, "wired.com": 5, "theverge.com": 5, "arstechnica.com": 5,
    "venturebeat.com": 5, "axios.com": 5, "bloomberg.com": 5,
    "reuters.com": 5, "cnbc.com": 5,

    # 💡 Product / Consumer / Ecosystem (Tier 4)
    "engadget.com": 4, "cnet.com": 4, "fastcompany.com": 4, "theinformation.com": 4,
    "businessinsider.com": 4, "fortune.com": 4, "protocol.com": 4,
    "techradar.com": 4, "zdnet.com": 4,

    # 🕹️ Gaming / Entertainment Tech (Tier 3–4)
    "ign.com": 4, "gamespot.com": 4, "pcgamer.com": 4,
    "polygon.com": 4, "gameinformer.com": 3,

    # 🤖 AI / Developer / Research (Tier 3–4)
    "semianalysis.com": 4, "huggingface.co": 4, "a16z.com": 4,
    "openai.com": 4, "anthropic.com": 4, "developers.googleblog.com": 4,
    "aws.amazon.com": 4, "deeplearning.ai": 3,
    "paperswithcode.com": 3, "importai.com": 3, "towardsdatascience.com": 3,
    "github.blog": 3, "meta.ai": 3,

    # 🚀 Startup Aggregators / Community (Tier 2–3)
    "producthunt.com": 3, "ycombinator.com": 3, "crunchbase.com": 3,
}

# ---- Local Events / Activities / Planner ------------------------
TRUSTED_PLANNER = {
    # 🎟️ Event Discovery / Booking
    "eventbrite.com": 6,
    "luma.com": 6,
    "meetup.com": 6,
    "ticketmaster.com": 6,
    "dice.fm": 5,
    "seatgeek.com": 5,
    "bandsintown.com": 5,
    "stubhub.com": 5,
    "feverup.com": 5,
    "allevents.in": 5,
    "bookmyshow.com": 5,
    "10times.com": 4,
    "everfest.com": 4,

    # 🌆 Local City Guides / Magazines
    "timeout.com": 6,
    "thrillist.com": 5,
    "do312.com": 5,
    "do617.com": 5,
    "do512.com": 5,
    "nycgo.com": 5,
    "sfstation.com": 5,
    "discoverlosangeles.com": 5,
    "lonelyplanet.com": 4,
    "matadornetwork.com": 4,

    # 🍽 Food / Dining / Lifestyle
    "yelp.com": 5,                # ⬆️ covers restaurants + nightlife
    "eater.com": 5,
    "zomato.com": 5,
    "opentable.com": 5,
    "resy.com": 5,
    "michelinguide.com": 5,
    "tripadvisor.com": 4,
    "exploretock.com": 4,
    "timeout.com/food": 5,        # food vertical
    "thrillist.com/eat": 5,

    # 🎭 Arts / Culture / Theatre
    "broadway.com": 5,
    "todaytix.com": 5,
    "artforum.com": 4,
    "metmuseum.org": 4,
    "moma.org": 4,
    "smithsonianmag.com": 4,
    "artsandculture.google.com": 4,

    # 🧘‍♂️ Fitness / Wellness / Workshops
    "classpass.com": 5,
    "mindbodyonline.com": 5,
    "fitternity.com": 4,
    "cult.fit": 4,
    "yogatrail.com": 4,
    "wellnessliving.com": 4,

    # 🌃 Nightlife / Entertainment
    "residentadvisor.net": 5,
    "ra.co": 5,
    "songkick.com": 5,
    "bandsintown.com": 5,
    "timeout.com/nightlife": 5,
    "edmtrain.com": 4,
    "clubbookers.com": 3,

    # 🗺️ Regional Event Aggregators / Lifestyle
    "eventseeker.com": 4,
    "discover.events.com": 4,
    "visitdubai.com": 4,
    "visitsingapore.com": 4,
    "visitlondon.com": 4,
    "visitphilly.com": 4,
}


# ---- Untrusted / Low Signal -------------------------------------
UNTRUSTED = {
    "reddit.com": -12, "old.reddit.com": -12, "quora.com": -12,
    "pinterest.com": -8, "tumblr.com": -6, "medium.com": -3,
    "9to5mac.com": -2, "macrumors.com": -2,
    "walmart.com": -8,  # Overly price-scraped, low signal

    # 🚫 Peer-to-peer used / risky marketplaces
    "ebay.com": -4,
    "craigslist.org": -10,
    "facebook.com": -8,  # Marketplace
}

# ================================================================
# Existing regexes / constants (keep from your file)
# ================================================================
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
    """Infer semantic intent from natural language search queries."""
    t = (q or "").lower().strip()

    # 📊 Finance / Markets / Crypto  (check BEFORE price)
    finance_keywords = (
        "stock", "stocks", "share", "market", "ipo", "earnings", "revenue",
        "investment", "finance", "financial", "company performance", "dividend",
        "guidance", "crypto", "bitcoin", "ethereum", "nasdaq", "dow", "s&p",
        "bse", "nse", "ticker", "company", "index", "mutual fund", "bond"
    )
    if any(k in t for k in finance_keywords):
        return "finance"

    # 💰 Price / Shopping / Products (secondary)
    price_keywords = (
        "price", "cost", "how much", "₹", "$", "buy", "deal", "offer", "discount",
        "coupon", "shopping", "product", "specs", "compare", "vs", "review",
        "order", "under $", "under ₹", "cheap", "sale", "wishlist"
    )
    if any(k in t for k in price_keywords):
        # detect if it's a stock/company context (contains finance hints)
        if any(k in t for k in finance_keywords):
            return "finance"
        return "price"

    # 🏟 Sports
    if any(k in t for k in (
        "score", "match", "fixture", "result", "standings", "odds", "game",
        "final score", "box score", "stats", "player", "team", "highlights"
    )):
        return "sports"

    # 📰 News / Media
    if any(k in t for k in (
        "news", "headline", "update", "latest", "breaking", "article", "press release"
    )):
        return "news"

    # 💼 Networking / Startup / Tech Events (comes BEFORE 'activities')
    if any(k in t for k in (
        "startup", "founder", "networking", "meetup", "conference", "summit",
        "luma", "eventbrite", "demo day", "pitch", "accelerator", "vc",
        "entrepreneur", "tech event", "product meetup"
    )):
        return "networking"

    # 🍽 Food / Restaurants / Cafes
    if any(k in t for k in (
        "restaurant", "restaurants", "food", "cuisine", "eat", "brunch", "lunch",
        "dinner", "bar", "coffee", "cafe", "bistro", "bakery", "dessert", "menu"
    )):
        return "food"

    # 🎟 Events / Activities / Things to Do
    if any(k in t for k in (
        "things to do", "activities", "events", "places to visit", "hangouts",
        "what to do", "classes", "workshops", "experiences"
    )):
        return "activities"

    # 🌄 Outdoors / Nature / Hiking
    if any(k in t for k in (
        "trail", "trails", "hike", "hiking", "walk", "walking", "run", "park",
        "beach", "outdoor", "nature", "scenic", "camping", "cycling", "picnic"
    )):
        return "outdoors"

    # 🌃 Nightlife / Entertainment
    if any(k in t for k in (
        "club", "nightlife", "party", "bar", "pub", "dj", "concert", "live music",
        "karaoke", "standup", "festival", "gig"
    )):
        return "nightlife"

    # 🔥 Trending / Viral / Social
    if any(k in t for k in (
        "trending", "viral", "popular", "latest trends", "buzzing",
        "instagram", "tiktok", "youtube", "reel", "social media"
    )):
        return "trending"

    # 🧠 Default
    return "search"


def intent_trust_weight(host: str, intent: str, query: str = "") -> int:
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
    if intent in ("activities", "events", "food", "restaurant", "outdoors", "nightlife", "networking", "startup", "conference", "meetup"):
        return TRUSTED_PLANNER.get(host, 0)

    return max(
        TRUSTED_SPORTS.get(host, 0),
        TRUSTED_SHOPS.get(host, 0),
        TRUSTED_NEWS.get(host, 0),
        TRUSTED_FINANCE.get(host, 0),
        TRUSTED_TECH_NEWS.get(host, 0),
        TRUSTED_PLANNER.get(host, 0),
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

def extract_city(text: str) -> str:
    """
    Infer a city from the query or user location.
    Falls back to approximate location via IP if 'near me' detected.
    """
    t = (text or "").lower()
    city_match = re.search(r"in\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", text)
    if city_match:
        return city_match.group(1)

    if "near me" in t or "around me" in t or "nearby" in t:
        try:
            g = geocoder.ip('me')
            if g.city:
                return g.city
        except Exception:
            pass
    return None

def expand_query(intent: str, query: str = "") -> str:
    """
    Expand search queries with high-signal keywords tuned to both intent and time context.
    Automatically treats 'near me' as same-day context.
    """
    q = (query or "").lower()
    expansions = []

    # ----------------------------
    # 1️⃣ Time Context Awareness
    # ----------------------------
    if any(k in q for k in ("today", "tonight", "near me", "around me", "nearby")):
        time_focus = "today"
        time_terms = [
            "today", "tonight", "happening now", "open now",
            "ongoing events", "today's schedule", "places open today"
        ]
    elif any(k in q for k in ("tomorrow", "tmrw")):
        time_focus = "tomorrow"
        time_terms = [
            "tomorrow", "tomorrow night", "upcoming tomorrow", "book for tomorrow"
        ]
    elif "weekend" in q:
        time_focus = "weekend"
        time_terms = [
            "this weekend", "weekend events", "Saturday", "Sunday",
            "festivals", "markets", "live music", "sports events"
        ]
    else:
        time_focus = "week"
        time_terms = [
            "this week", "events this week", "weekly lineup",
            "upcoming events", "trending places this week"
        ]

    # ----------------------------
    # 2️⃣ Intent-Specific Keywords
    # ----------------------------

    if intent in ("networking", "startup", "conference", "meetup"):
        expansions += [
            "startup events near me", "tech meetups", "founder networking", "Luma",
            "Eventbrite",  "Meetup", "TechCrunch events", "LinkedIn Local",
            "accelerator demo day", "pitch competitions", "innovation summits"
        ]


    elif intent in ("activities", "outdoors", "weekend"):
        expansions += [
            "things to do", "local activities", "TripAdvisor", "Luma", "Eventbrite",
            "Meetup", "TimeOut events", "guided tours", "unique experiences"
        ]
        if any(k in q for k in ("hike", "trail", "walk", "park", "beach")):
            expansions += ["best hiking trails", "nature walks", "AllTrails", "parks near me"]
        if any(k in q for k in ("concert", "music")):
            expansions += ["concerts near me", "live music", "Ticketmaster", "Bandsintown"]
        if any(k in q for k in ("sports", "match", "game")):
            expansions += ["sports events", "NBA", "cricket", "football", "ESPN", "StubHub"]
        if any(k in q for k in ("comedy", "show", "theatre", "musical")):
            expansions += ["comedy shows", "standup comedy", "plays", "Eventbrite comedy"]
        if any(k in q for k in ("art", "exhibition", "museum")):
            expansions += ["art exhibitions", "gallery shows", "museum events", "TimeOut art"]
        if any(k in q for k in ("food", "restaurant", "market")):
            expansions += ["food festivals", "pop-up markets", "street food events"]

    elif intent in ("restaurant", "food"):
        expansions += [
            "top restaurants", "cafes", "Yelp", "Zomato", "Eater", "OpenTable",
            "new openings", "trending dining", "TimeOut food", "Michelin Guide"
        ]

    elif intent == "nightlife":
        expansions += [
            "bars", "clubs", "live DJs", "rooftop lounges", "karaoke",
            "night events", "TimeOut nightlife", "concerts tonight"
        ]

    elif intent == "trending":
        expansions += [
            "trending spots", "viral", "popular now", "new openings",
            "TikTok", "Instagram trends", "buzzing places"
        ]

    elif intent in ("finance", "price"):
        # auto-detect finance vs retail based on query content
        finance_hints = [
            "stock", "share", "market", "ipo", "earnings", "dividend",
            "nasdaq", "dow", "s&p", "company", "revenue", "guidance",
            "financial", "invest", "crypto", "trading"
        ]
        retail_hints = [
            "buy", "order", "deal", "discount", "coupon", "shopping",
            "amazon", "flipkart", "target", "costco", "specs", "product",
            "laptop", "phone", "watch"
        ]

        is_finance_query = any(k in q for k in finance_hints) and not any(k in q for k in retail_hints)

        if is_finance_query:
            expansions += [
                "stock price", "share price", "market update", "latest earnings",
                "Bloomberg", "CNBC", "Reuters finance", "Nasdaq", "Yahoo Finance",
                "company performance", "Q4 2025 results", "financial outlook"
            ]
        else:
            current_year = datetime.now().year
            expansions += [
        "current price", "latest model", "buy online", "available now",
        "new release", f"{current_year}", f"{current_year} model",
        "official site", "authentic product", "user ratings",
        "customer reviews", "specifications", "buying guide",
        "best deals", "price comparison", "battery life", "performance test"
    ]


            # 🧠 Optional refurb bias — only if user query hints at refurb/used
            if any(k in q for k in ("used", "refurb", "renewed", "pre-owned")):
                expansions += ["certified refurbished", "factory renewed", "used tech deals"]



    # ----------------------------
    # 3️⃣ Combine & Clean
    # ----------------------------
    if intent in (
        "activities", "outdoors", "weekend", "food", "restaurant",
        "nightlife", "trending", "networking", "startup", "conference", "meetup"
    ):
        combined = time_terms + list(dict.fromkeys(expansions))
    else:
        combined = list(dict.fromkeys(expansions))

    return " ".join(combined)



__all__ = [
    "host", "resolve_target_date", "parse_date_from_text",
    "intent_from_query", "intent_trust_weight", "page_type_bonus", "score_link",
    "TRUSTED_SPORTS", "TRUSTED_SHOPS", "TRUSTED_NEWS",
    "TRUSTED_FINANCE", "TRUSTED_TECH_NEWS", "UNTRUSTED",
]
