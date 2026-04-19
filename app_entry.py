"""py2app entry point.

py2app expects a top-level script, not a module path. This file bridges
`MIRA Daemon.app/Contents/MacOS/MIRA` → `mira.__main__.main`.

When launched as a frozen .app bundle, sys.argv has no user flags — the
parser would fall through to `_diag()` and exit. Force `daemon` in that
case so double-clicking the app actually starts the voice loop (and
triggers the mic permission prompt on first run).
"""
import os
import sys

# Alias-mode bundles don't consistently honor .pth files, so guarantee the
# repo's `src/` is on sys.path before importing mira. Relative to this file
# when running from source, absolute when frozen.
_here = os.path.dirname(os.path.abspath(__file__))
_src = os.path.join(_here, "src")
if os.path.isdir(_src) and _src not in sys.path:
    sys.path.insert(0, _src)

# py2app bundles Playwright's Python package but not the Chromium binaries
# (they're ~400MB and get downloaded on `playwright install`, not pip install).
# Point at the user's global ms-playwright cache so the bundle reuses the
# browsers that were installed into the venv.
if getattr(sys, "frozen", False) and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.expanduser(
        "~/Library/Caches/ms-playwright"
    )

from mira.__main__ import main

if __name__ == "__main__":
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        sys.argv.append("daemon")
    raise SystemExit(main())
