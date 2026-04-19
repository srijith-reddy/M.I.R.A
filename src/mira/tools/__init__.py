from __future__ import annotations

# Importing these modules registers their tools into the global registry as
# a side effect. Keep the imports in one place so "which tools exist" has a
# single obvious answer.
from mira.tools import app_tools  # noqa: F401
from mira.tools import browser_tools  # noqa: F401
from mira.tools import calendar_tools  # noqa: F401
from mira.tools import contacts_tools  # noqa: F401
from mira.tools import files_tools  # noqa: F401
from mira.tools import gmail_tools  # noqa: F401
from mira.tools import maps_tools  # noqa: F401
from mira.tools import memory_tools  # noqa: F401
from mira.tools import messaging_tools  # noqa: F401
from mira.tools import music_tools  # noqa: F401
from mira.tools import reminder_tools  # noqa: F401
from mira.tools import system_tools  # noqa: F401
from mira.tools import time_tools  # noqa: F401
from mira.tools import weather_tools  # noqa: F401
from mira.tools import web_tools  # noqa: F401
from mira.tools import research_tools  # noqa: F401  research depends on web_tools
from mira.tools.web_tools import install_web_tools


def install_default_tools() -> None:
    """Install all tools whose dependencies / keys are currently satisfied.
    Safe to call multiple times — registration guards against duplicates."""
    install_web_tools()


__all__ = ["install_default_tools"]
