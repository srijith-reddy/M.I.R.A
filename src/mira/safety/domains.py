from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

# Tier-based domain trust. Boring, inspectable, no ML. Each tier is a frozen
# set of registrable domains (eTLD+1, no www.). `is_trusted(url, mode)`
# returns a verdict with a reason so the caller can decide whether to drop,
# warn, or just log.
#
# Design intent:
#   * Start small. Five entries beat fifty that nobody audits.
#   * Don't silently drop. Rank + log; let the agent see the world as Brave
#     returned it but prefer tier-1 sources. A missed drop is a bug; a silent
#     drop is a worse bug.
#   * Per-mode tiers, because "trustworthy source" depends on what you're
#     doing. nytimes.com is tier-1 for news and tier-3 for shopping.

TrustMode = Literal[
    "off",        # No filtering. Pass-through. Use in dev / debug.
    "default",    # Denylist only. Everything else tier-unknown.
    "strict",     # Must appear in *any* tier-1 set. Tightest.
    "news",       # Research / current-events queries.
    "commerce",   # Shopping, price checks, product research.
    "booking",    # Travel, hotels, restaurants, ticketing.
    "reference",  # Docs, code, academic, gov sources.
]

TrustTier = Literal["tier1", "tier2", "unknown", "denied"]


# ---------- Tier data ----------
#
# Keep entries lowercase and in registrable form (eTLD+1). Don't include
# paths or query. Subdomains are collapsed by the matcher, so "en.wikipedia.org"
# hits the "wikipedia.org" entry.

NEWS_TIER1 = frozenset({
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "nytimes.com",
    "wsj.com",
    "ft.com",
    "washingtonpost.com",
    "bloomberg.com",
    "economist.com",
    "theguardian.com",
    "npr.org",
    "pbs.org",
    "cnbc.com",
    "politico.com",
    "axios.com",
    "aljazeera.com",
})

NEWS_TIER2 = frozenset({
    "cnn.com",
    "foxnews.com",
    "nbcnews.com",
    "cbsnews.com",
    "abcnews.go.com",
    "usatoday.com",
    "latimes.com",
    "time.com",
    "newsweek.com",
    "theatlantic.com",
    "vox.com",
    "businessinsider.com",
})

COMMERCE_TIER1 = frozenset({
    # General retailers with strong counterfeit controls and return policies.
    "amazon.com",
    "walmart.com",
    "target.com",
    "bestbuy.com",
    "costco.com",
    "homedepot.com",
    "lowes.com",
    "macys.com",
    "ikea.com",
    # Apparel / specialty.
    "nike.com",
    "adidas.com",
    "patagonia.com",
    "rei.com",
    # Electronics direct.
    "apple.com",
    "samsung.com",
    "dell.com",
    "microsoft.com",
    # Grocery / household.
    "instacart.com",
    "wholefoodsmarket.com",
})

COMMERCE_REVIEW_TIER1 = frozenset({
    "wirecutter.com",
    "nytimes.com",          # parent of Wirecutter.
    "rtings.com",
    "consumerreports.org",
    "techradar.com",
    "tomshardware.com",
    "anandtech.com",
    "dpreview.com",
})

BOOKING_TIER1 = frozenset({
    # Aggregators.
    "google.com",           # google.com/travel, google.com/flights
    "kayak.com",
    "skyscanner.com",
    "booking.com",
    "expedia.com",
    "hotels.com",
    "trivago.com",
    "airbnb.com",
    "vrbo.com",
    # Airlines direct.
    "delta.com",
    "united.com",
    "aa.com",
    "southwest.com",
    "jetblue.com",
    "alaskaair.com",
    "britishairways.com",
    "lufthansa.com",
    "emirates.com",
    "airfrance.com",
    "klm.com",
    # Hotels direct.
    "marriott.com",
    "hilton.com",
    "hyatt.com",
    "ihg.com",
    "accor.com",
    # Rail / bus / transit.
    "amtrak.com",
    "eurail.com",
    "raileurope.com",
    # Dining.
    "opentable.com",
    "resy.com",
    "yelp.com",
    # Tickets / events.
    "ticketmaster.com",
    "stubhub.com",
    "seatgeek.com",
})

REFERENCE_TIER1 = frozenset({
    "wikipedia.org",
    "wikimedia.org",
    "arxiv.org",
    "github.com",
    "gitlab.com",
    "stackoverflow.com",
    "stackexchange.com",
    "developer.mozilla.org",
    "mdn.mozilla.org",
    "python.org",
    "docs.python.org",
    "pypi.org",
    "npmjs.com",
    "rust-lang.org",
    "go.dev",
    "kernel.org",
    "ietf.org",
    "rfc-editor.org",
    "w3.org",
    "who.int",
    "nih.gov",
    "cdc.gov",
    "nasa.gov",
})

# Universal denylist. Content farms, AI-generated slop networks, known
# counterfeit marketplaces, and sites that exist to rank — not to inform.
# Grow this list when you hit a junk result; don't preemptively pad it.
DENYLIST = frozenset({
    # Content / answer farms — frequently wrong, rarely sourced.
    "answers.com",
    "ask.com",
    "quora.com",              # occasionally useful but very noisy
    "ehow.com",
    "wikihow.com",
    "chacha.com",
    # AI-slop destinations seen in the wild.
    "contentbot.ai",
    "buzzlewire.com",
    # Listicle / SEO-first sites.
    "listverse.com",
    "therichest.com",
    "thethings.com",
    # Counterfeit-prone marketplaces (booking/commerce blast radius).
    "wish.com",
    "dhgate.com",
})

# TLD-level allowlist for reference mode. Gov / edu domains are wildcarded
# because enumerating every .edu is hopeless — if it's on a university or
# government TLD, it's tier-1 for reference work.
_ALLOW_TLDS_REFERENCE = (".gov", ".edu", ".mil")


# ---------- Verdict type ----------


@dataclass(frozen=True)
class TrustVerdict:
    trusted: bool
    tier: TrustTier
    reason: str
    domain: str


# ---------- URL normalization ----------


def registrable_domain(url_or_host: str) -> str:
    """Collapse a URL or host string to its registrable domain (eTLD+1).

    Handles:
      * Full URLs (`https://www.nytimes.com/...` → `nytimes.com`)
      * Bare hosts (`en.wikipedia.org` → `wikipedia.org`)
      * `www.` stripping and subdomain collapsing.

    We don't use the `publicsuffix2` lib because it's another dep for a
    narrow win. This heuristic takes the last two labels — wrong for
    `.co.uk` / `.com.au` / `.gov.uk`, which we handle explicitly below.
    """
    if not url_or_host:
        return ""
    s = url_or_host.strip().lower()
    if "://" not in s:
        s = "//" + s
    host = urlparse(s).hostname or ""
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]

    parts = host.split(".")
    if len(parts) <= 2:
        return host

    # Two-label public suffixes we care about. Not exhaustive; add when it
    # actually matters for an entry we want to support.
    two_label_suffixes = {
        "co.uk", "co.jp", "co.kr", "co.nz", "com.au", "com.br", "com.cn",
        "gov.uk", "ac.uk", "org.uk", "gov.au", "edu.au", "net.au",
    }
    tail_two = ".".join(parts[-2:])
    tail_three = ".".join(parts[-3:]) if len(parts) >= 3 else ""
    if tail_two in two_label_suffixes and len(parts) >= 3:
        return tail_three
    return tail_two


def _tld_match(domain: str, tlds: tuple[str, ...]) -> bool:
    return any(domain == t.lstrip(".") or domain.endswith(t) for t in tlds)


# ---------- Policy ----------


def _tier1_for(mode: TrustMode) -> frozenset[str]:
    if mode == "news":
        return NEWS_TIER1
    if mode == "commerce":
        return COMMERCE_TIER1 | COMMERCE_REVIEW_TIER1
    if mode == "booking":
        return BOOKING_TIER1
    if mode == "reference":
        return REFERENCE_TIER1
    if mode == "strict":
        # Union of every tier-1 set; anything not here gets demoted.
        return (
            NEWS_TIER1
            | COMMERCE_TIER1
            | COMMERCE_REVIEW_TIER1
            | BOOKING_TIER1
            | REFERENCE_TIER1
        )
    return frozenset()


def _tier2_for(mode: TrustMode) -> frozenset[str]:
    if mode == "news":
        return NEWS_TIER2
    return frozenset()


def is_trusted(url: str, mode: TrustMode = "default") -> TrustVerdict:
    """Classify a URL for a given operating mode.

    Modes:
      * `off`:       Everything is trusted ("unknown" tier).
      * `default`:   Denylist only. Tier-unknown otherwise.
      * `strict`:    Must be in a tier-1 set (any mode).
      * `news`/`commerce`/`booking`/`reference`: tier-aware per mode.

    Always returns a verdict — callers decide whether to drop, warn, or
    just log. Silent drops are a footgun; downstream code should log the
    reason field for traceability.
    """
    domain = registrable_domain(url)
    if not domain:
        return TrustVerdict(trusted=False, tier="unknown", reason="no-domain", domain="")

    if mode == "off":
        return TrustVerdict(trusted=True, tier="unknown", reason="mode-off", domain=domain)

    if domain in DENYLIST:
        return TrustVerdict(
            trusted=False, tier="denied", reason="denylist", domain=domain
        )

    # Reference-mode TLD allowlist (.gov / .edu / .mil).
    if mode == "reference" and _tld_match(domain, _ALLOW_TLDS_REFERENCE):
        return TrustVerdict(
            trusted=True, tier="tier1", reason="reference-tld", domain=domain
        )

    tier1 = _tier1_for(mode)
    if domain in tier1:
        return TrustVerdict(
            trusted=True, tier="tier1", reason=f"{mode}-tier1", domain=domain
        )

    tier2 = _tier2_for(mode)
    if domain in tier2:
        return TrustVerdict(
            trusted=True, tier="tier2", reason=f"{mode}-tier2", domain=domain
        )

    if mode == "strict":
        return TrustVerdict(
            trusted=False, tier="unknown", reason="strict-miss", domain=domain
        )

    return TrustVerdict(
        trusted=True, tier="unknown", reason="default-allow", domain=domain
    )


# ---------- Result post-processing ----------


_TIER_RANK = {"tier1": 0, "tier2": 1, "unknown": 2, "denied": 3}


def tag_and_sort(
    results: list[dict[str, Any]],
    mode: TrustMode,
    *,
    drop_denied: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (kept, filtered).

    Each kept result gets `trust_tier`, `trust_reason`, and `trust_domain`
    appended so the caller (LLM or UI) can see why it was ranked where it
    was. Results are stable-sorted by tier rank, preserving Brave's
    original ordering within each tier.

    `drop_denied=True` moves denylisted rows into `filtered`; default is to
    keep them so the caller has visibility (they still sort last)."""
    tagged: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    for idx, r in enumerate(results):
        url = r.get("url") or ""
        v = is_trusted(url, mode)
        enriched = dict(r)
        enriched["trust_tier"] = v.tier
        enriched["trust_reason"] = v.reason
        enriched["trust_domain"] = v.domain
        enriched["_orig_idx"] = idx
        if v.tier == "denied" and drop_denied:
            filtered.append(enriched)
        else:
            tagged.append(enriched)
    tagged.sort(key=lambda r: (_TIER_RANK.get(r["trust_tier"], 9), r["_orig_idx"]))
    for r in tagged:
        r.pop("_orig_idx", None)
    for r in filtered:
        r.pop("_orig_idx", None)
    return tagged, filtered
