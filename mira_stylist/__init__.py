"""MIRA Stylist package.

This package is intentionally self-contained so it can be integrated into the
existing MIRA codebase incrementally.
"""

from .config import StylistSettings, get_settings

__all__ = ["StylistSettings", "get_settings"]
