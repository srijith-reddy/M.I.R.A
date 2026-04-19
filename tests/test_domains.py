from __future__ import annotations

from mira.safety.domains import (
    DENYLIST,
    is_trusted,
    registrable_domain,
    tag_and_sort,
)


# ---------- registrable_domain ----------


def test_registrable_domain_strips_scheme_and_www() -> None:
    assert registrable_domain("https://www.nytimes.com/article/x") == "nytimes.com"
    assert registrable_domain("http://WWW.NYTimes.com") == "nytimes.com"


def test_registrable_domain_handles_subdomains() -> None:
    assert registrable_domain("https://en.wikipedia.org/wiki/X") == "wikipedia.org"
    assert registrable_domain("https://docs.python.org/3/") == "python.org"


def test_registrable_domain_handles_two_label_tlds() -> None:
    # .co.uk should collapse to the full three-label registrable domain.
    assert registrable_domain("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert registrable_domain("https://some.random.gov.uk/x") == "random.gov.uk"


def test_registrable_domain_bare_host() -> None:
    assert registrable_domain("reuters.com") == "reuters.com"
    assert registrable_domain("") == ""


# ---------- is_trusted ----------


def test_off_mode_passes_everything() -> None:
    v = is_trusted("https://wish.com/random", mode="off")
    assert v.trusted is True
    assert v.reason == "mode-off"


def test_default_mode_denies_denylist() -> None:
    v = is_trusted("https://quora.com/q", mode="default")
    assert v.trusted is False
    assert v.tier == "denied"
    assert v.domain == "quora.com"


def test_news_mode_tiers_tier1() -> None:
    v = is_trusted("https://www.reuters.com/world/x", mode="news")
    assert v.trusted is True
    assert v.tier == "tier1"
    assert v.reason == "news-tier1"


def test_news_mode_tiers_tier2() -> None:
    v = is_trusted("https://cnn.com/2025/x", mode="news")
    assert v.trusted is True
    assert v.tier == "tier2"


def test_commerce_mode_allows_retailers_and_reviews() -> None:
    assert is_trusted("https://amazon.com/dp/X", mode="commerce").tier == "tier1"
    assert is_trusted("https://wirecutter.com/reviews/x", mode="commerce").tier == "tier1"


def test_booking_mode_allows_airlines_and_aggregators() -> None:
    assert is_trusted("https://kayak.com/flights", mode="booking").tier == "tier1"
    assert is_trusted("https://www.delta.com", mode="booking").tier == "tier1"


def test_reference_mode_tld_allowlist() -> None:
    # Any .gov / .edu should be tier-1 under reference mode even if not
    # listed explicitly.
    v = is_trusted("https://weather.noaa.gov/x", mode="reference")
    assert v.trusted is True
    assert v.tier == "tier1"
    assert v.reason == "reference-tld"


def test_strict_mode_demotes_unknown() -> None:
    # Unknown domain (not in any tier-1 set) under strict mode → not trusted.
    v = is_trusted("https://random-blog.example/x", mode="strict")
    assert v.trusted is False
    assert v.tier == "unknown"
    assert v.reason == "strict-miss"


def test_strict_mode_allows_any_tier1() -> None:
    # strict unions all tier-1 sets, so a news tier-1 site still passes.
    assert is_trusted("https://nytimes.com", mode="strict").trusted is True


def test_denylist_beats_any_mode() -> None:
    for mode in ("default", "news", "commerce", "booking", "reference", "strict"):
        v = is_trusted("https://wish.com/x", mode=mode)  # type: ignore[arg-type]
        assert v.tier == "denied", f"mode={mode} should still deny wish.com"


def test_empty_url_returns_no_domain() -> None:
    v = is_trusted("", mode="default")
    assert v.trusted is False
    assert v.domain == ""
    assert v.reason == "no-domain"


def test_denylist_is_non_empty() -> None:
    # Sanity guard: if somebody empties the denylist by accident, this
    # test should scream. Matching a specific entry is brittle; just
    # assert the set has mass.
    assert len(DENYLIST) >= 5


# ---------- tag_and_sort ----------


def test_tag_and_sort_ranks_tier1_first() -> None:
    results = [
        {"title": "rando", "url": "https://some-random.example/a", "snippet": "s"},
        {"title": "nyt",   "url": "https://nytimes.com/a",          "snippet": "s"},
        {"title": "junk",  "url": "https://quora.com/a",            "snippet": "s"},
    ]
    kept, filtered = tag_and_sort(results, mode="news", drop_denied=False)
    assert filtered == []
    # Order: nytimes (tier1) → rando (unknown) → quora (denied).
    assert kept[0]["url"] == "https://nytimes.com/a"
    assert kept[0]["trust_tier"] == "tier1"
    assert kept[1]["trust_tier"] == "unknown"
    assert kept[-1]["url"] == "https://quora.com/a"
    assert kept[-1]["trust_tier"] == "denied"


def test_tag_and_sort_preserves_order_within_tier() -> None:
    results = [
        {"title": "reuters", "url": "https://reuters.com/a", "snippet": "s"},
        {"title": "bbc",     "url": "https://bbc.com/b",     "snippet": "s"},
    ]
    kept, _ = tag_and_sort(results, mode="news")
    assert [r["url"] for r in kept] == [
        "https://reuters.com/a",
        "https://bbc.com/b",
    ]


def test_tag_and_sort_drop_denied_moves_to_filtered() -> None:
    results = [
        {"title": "good", "url": "https://nytimes.com/a", "snippet": "s"},
        {"title": "bad",  "url": "https://wish.com/a",   "snippet": "s"},
    ]
    kept, filtered = tag_and_sort(results, mode="commerce", drop_denied=True)
    assert [r["url"] for r in kept] == ["https://nytimes.com/a"]
    assert [r["url"] for r in filtered] == ["https://wish.com/a"]
    assert filtered[0]["trust_tier"] == "denied"


def test_tag_and_sort_off_mode_keeps_everyone_as_unknown() -> None:
    results = [
        {"title": "a", "url": "https://wish.com/x",    "snippet": "s"},
        {"title": "b", "url": "https://reuters.com/y", "snippet": "s"},
    ]
    kept, filtered = tag_and_sort(results, mode="off")
    assert filtered == []
    # In off-mode everything is "unknown", so Brave's original order wins.
    assert [r["url"] for r in kept] == [
        "https://wish.com/x",
        "https://reuters.com/y",
    ]
    for r in kept:
        assert r["trust_tier"] == "unknown"
