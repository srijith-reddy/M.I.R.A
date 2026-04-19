"""Pytest bootstrap.

Runs *before* any `mira.*` import so the data/config/cache paths point at a
pytest-owned temp directory and nothing leaks into the user's real profile.
We can't use a normal fixture for this because `mira.config.paths.paths` is
frozen at import time.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="mira-evals-"))
os.environ.setdefault("MIRA_DATA_DIR", str(_TMP_ROOT / "data"))
os.environ.setdefault("MIRA_CACHE_DIR", str(_TMP_ROOT / "cache"))
os.environ.setdefault("MIRA_CONFIG_DIR", str(_TMP_ROOT / "config"))
# FakeLLMGateway never touches the network, but some code paths peek at the
# key to decide whether to register optional tools. A placeholder is enough.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-placeholder")

import pytest  # noqa: E402

from mira.evals import install_fake_llm, reset_session_db, restore_llm  # noqa: E402
from mira.evals.fakes import FakeLLMGateway  # noqa: E402


@pytest.fixture
def fake_llm() -> Iterator[FakeLLMGateway]:
    """Swap the module-level LLM singleton for a fresh fake; restore on teardown."""
    fake = install_fake_llm()
    try:
        yield fake
    finally:
        restore_llm()


@pytest.fixture(autouse=True)
def _clean_session() -> Iterator[None]:
    """Wipe session + reminder rows before each test so turn records from one
    test don't leak into the next."""
    reset_session_db()
    yield
    reset_session_db()
