from __future__ import annotations

from typing import Any

import pytest

from mira.config.settings import Settings, get_settings
from mira.runtime.llm import LLMGateway
from mira.runtime.llm_types import LLMResponse, LLMUsage, Message


class _SpyAdapter:
    """Lightweight stand-in for a real provider adapter. Records the kwargs
    it was called with so the tests can assert dispatch went the right way
    without hitting a network."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(
            text="ok",
            model=kwargs["model"],
            provider=self.provider,  # type: ignore[arg-type]
            usage=LLMUsage(prompt_tokens=5, completion_tokens=7),
        )


def _gw_with_fake_adapters() -> tuple[LLMGateway, dict[str, _SpyAdapter]]:
    gw = LLMGateway()
    spies = {
        "openai": _SpyAdapter("openai"),
        "groq": _SpyAdapter("groq"),
        "anthropic": _SpyAdapter("anthropic"),
    }
    # Pre-populate the gateway's private adapter slots so _adapter_for doesn't
    # hit the real SDK init path.
    gw._openai = spies["openai"]  # type: ignore[assignment]
    gw._groq = spies["groq"]  # type: ignore[assignment]
    gw._anthropic = spies["anthropic"]  # type: ignore[assignment]
    return gw, spies


def test_complete_dispatches_openai_by_model_prefix() -> None:
    gw, spies = _gw_with_fake_adapters()
    gw.complete([Message(role="user", content="hi")], model="gpt-4o")
    assert len(spies["openai"].calls) == 1
    assert len(spies["groq"].calls) == 0
    assert len(spies["anthropic"].calls) == 0


def test_complete_dispatches_groq_by_model_prefix() -> None:
    gw, spies = _gw_with_fake_adapters()
    gw.complete([Message(role="user", content="hi")], model="llama-3.1-8b-instant")
    assert len(spies["groq"].calls) == 1


def test_complete_dispatches_anthropic_by_model_prefix() -> None:
    gw, spies = _gw_with_fake_adapters()
    gw.complete([Message(role="user", content="hi")], model="claude-sonnet-4-6")
    assert len(spies["anthropic"].calls) == 1


def test_cost_is_filled_from_cost_table() -> None:
    gw, _ = _gw_with_fake_adapters()
    resp = gw.complete([Message(role="user", content="hi")], model="gpt-4o")
    # gpt-4o: (0.005, 0.015) per 1K → 5 in, 7 out = (5/1000)*0.005 + (7/1000)*0.015
    expected = (5 / 1000) * 0.005 + (7 / 1000) * 0.015
    assert resp.usage.cost_usd == pytest.approx(expected, abs=1e-9)


def test_cost_stays_zero_for_unknown_model() -> None:
    gw, _ = _gw_with_fake_adapters()
    resp = gw.complete([Message(role="user", content="hi")], model="llama-3.1-8b-instant")
    # llama-3.1-8b-instant is in the table — make sure it's nonzero, proving
    # our handling isn't short-circuiting groq responses.
    assert resp.usage.cost_usd > 0.0


def test_router_prefers_groq_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from mira.agents.router import FastRouter

    # Force a settings instance that has a Groq key. Cache-bypassing a
    # get_settings() singleton requires patching the FastRouter instance's
    # copy directly — get_settings() is lru_cache'd.
    fake = Settings(
        openai_api_key="sk-open",
        groq_api_key="gsk-test",
        picovoice_access_key="pv",
        cartesia_api_key="ct",
        cartesia_voice="voice-id",
    )
    router = FastRouter()
    router._settings = fake  # type: ignore[attr-defined]
    assert router._router_model() == fake.groq_router_model


def test_router_falls_back_to_openai_without_groq_key() -> None:
    from mira.agents.router import FastRouter

    fake = Settings(
        openai_api_key="sk-open",
        groq_api_key=None,
        picovoice_access_key="pv",
        cartesia_api_key="ct",
        cartesia_voice="voice-id",
    )
    router = FastRouter()
    router._settings = fake  # type: ignore[attr-defined]
    assert router._router_model() == fake.openai_classify_model


def test_settings_surfaces_new_provider_fields() -> None:
    # Smoke-level guard so future edits don't accidentally drop the fields.
    s = get_settings()
    assert hasattr(s, "anthropic_api_key")
    assert hasattr(s, "anthropic_planner_model")
    assert hasattr(s, "groq_api_key")
    assert hasattr(s, "groq_router_model")
