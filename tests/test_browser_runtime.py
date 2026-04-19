from __future__ import annotations

from typing import Any

import pytest

from mira.browser.runtime import BrowserRuntime, _looks_like_crash


# ---------- Crash classifier ----------


class _FakeTargetClosedError(Exception):
    pass
_FakeTargetClosedError.__name__ = "TargetClosedError"


class _FakeBrowserClosedError(Exception):
    pass
_FakeBrowserClosedError.__name__ = "BrowserClosedError"


class _FakePlaywrightError(Exception):
    pass
_FakePlaywrightError.__name__ = "Error"


def test_crash_classifier_recognizes_close_errors() -> None:
    assert _looks_like_crash(_FakeTargetClosedError("nope"))
    assert _looks_like_crash(_FakeBrowserClosedError("gone"))


def test_crash_classifier_requires_crash_hint_for_generic_error() -> None:
    # Generic `Error` with a non-crash message must NOT be classified as a
    # crash — otherwise every selector-not-found would trigger a restart.
    assert not _looks_like_crash(_FakePlaywrightError("waiting for selector #foo"))
    assert not _looks_like_crash(_FakePlaywrightError("timeout 30000ms exceeded"))


def test_crash_classifier_matches_crash_hinted_errors() -> None:
    assert _looks_like_crash(_FakePlaywrightError("target closed"))
    assert _looks_like_crash(_FakePlaywrightError("Browser has been closed"))
    assert _looks_like_crash(_FakePlaywrightError("page was closed mid-action"))


def test_crash_classifier_ignores_unrelated_exceptions() -> None:
    assert not _looks_like_crash(ValueError("bad input"))
    assert not _looks_like_crash(RuntimeError("tool said no"))


# ---------- run_with_recovery ----------


class _FakePage:
    """Minimal stand-in for a Playwright page. Tracks whether it's been
    replaced so the test can assert recovery actually swapped pages."""

    def __init__(self, *, label: str, closed: bool = False) -> None:
        self.label = label
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


@pytest.mark.asyncio
async def test_run_with_recovery_passes_through_on_success() -> None:
    rt = BrowserRuntime()
    rt._context = object()  # type: ignore[assignment]
    rt._context_closed = False  # type: ignore[attr-defined]
    rt._page = _FakePage(label="p1")

    async def _noop_alive() -> bool:
        return True

    rt._is_context_alive = _noop_alive  # type: ignore[assignment]

    async def _op(page: Any) -> str:
        return page.label

    result = await rt.run_with_recovery(_op, tool_name="test")
    assert result == "p1"


@pytest.mark.asyncio
async def test_run_with_recovery_restarts_on_crash_once() -> None:
    """On a crash error, teardown + relaunch should happen and the operation
    should be retried. A successful second attempt returns normally."""
    rt = BrowserRuntime()
    rt._context = object()  # type: ignore[assignment]
    rt._context_closed = False  # type: ignore[attr-defined]
    rt._page = _FakePage(label="p1")

    async def _alive() -> bool:
        return True

    rt._is_context_alive = _alive  # type: ignore[assignment]

    recover_calls = {"n": 0}

    async def _fake_recover() -> None:
        recover_calls["n"] += 1
        rt._page = _FakePage(label="p2")

    rt._recover = _fake_recover  # type: ignore[assignment]

    attempts = {"n": 0}

    async def _op(page: Any) -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _FakeTargetClosedError("target closed")
        return page.label

    out = await rt.run_with_recovery(_op, tool_name="test")
    assert out == "p2"
    assert attempts["n"] == 2
    assert recover_calls["n"] == 1


@pytest.mark.asyncio
async def test_run_with_recovery_lets_non_crash_errors_bubble() -> None:
    rt = BrowserRuntime()
    rt._context = object()  # type: ignore[assignment]
    rt._context_closed = False  # type: ignore[attr-defined]
    rt._page = _FakePage(label="p1")

    async def _alive() -> bool:
        return True

    rt._is_context_alive = _alive  # type: ignore[assignment]

    recover_calls = {"n": 0}

    async def _fake_recover() -> None:
        recover_calls["n"] += 1

    rt._recover = _fake_recover  # type: ignore[assignment]

    async def _op(page: Any) -> str:
        raise ValueError("selector missing")

    with pytest.raises(ValueError):
        await rt.run_with_recovery(_op, tool_name="test")
    assert recover_calls["n"] == 0


@pytest.mark.asyncio
async def test_run_with_recovery_propagates_second_crash() -> None:
    rt = BrowserRuntime()
    rt._context = object()  # type: ignore[assignment]
    rt._context_closed = False  # type: ignore[attr-defined]
    rt._page = _FakePage(label="p1")

    async def _alive() -> bool:
        return True

    rt._is_context_alive = _alive  # type: ignore[assignment]

    async def _fake_recover() -> None:
        rt._page = _FakePage(label="p2")

    rt._recover = _fake_recover  # type: ignore[assignment]

    async def _op(page: Any) -> str:
        raise _FakeTargetClosedError("still dead")

    with pytest.raises(_FakeTargetClosedError):
        await rt.run_with_recovery(_op, tool_name="test")


# ---------- stats / page replacement ----------


@pytest.mark.asyncio
async def test_page_is_replaced_when_closed_without_full_restart() -> None:
    """If only the page is closed (not the context), we should mint a new
    page rather than tearing everything down."""

    created_pages: list[_FakePage] = []

    class _FakeContext:
        def __init__(self) -> None:
            self.pages: list[Any] = []

        async def new_page(self) -> Any:
            p = _FakePage(label=f"fresh-{len(created_pages)}")
            created_pages.append(p)
            return p

    rt = BrowserRuntime()
    rt._context = _FakeContext()  # type: ignore[assignment]
    rt._context_closed = False  # type: ignore[attr-defined]
    rt._page = _FakePage(label="old", closed=True)

    async def _alive() -> bool:
        return True

    rt._is_context_alive = _alive  # type: ignore[assignment]

    page = await rt.page()
    assert page.label.startswith("fresh-")
    assert len(created_pages) == 1


def test_stats_reports_restart_counter() -> None:
    rt = BrowserRuntime()
    assert rt.stats()["restarts"] == 0
    assert rt.stats()["started"] is False
