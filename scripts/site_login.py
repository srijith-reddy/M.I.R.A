"""One-time site login helper.

Opens MIRA's persistent Chromium profile (same one the daemon uses) in
headful mode, navigates to a site, and waits until a "logged in" signal
appears on the page. Run with the MIRA daemon STOPPED — a persistent
Playwright context can't be shared across processes.

    PYTHONPATH=src .venv/bin/python scripts/site_login.py whatsapp
    PYTHONPATH=src .venv/bin/python scripts/site_login.py gmail
"""

from __future__ import annotations

import asyncio
import sys

from mira.config.paths import paths


SITES = {
    "whatsapp": {
        "url": "https://web.whatsapp.com/",
        # Side panel with chat list only renders after login.
        "ready_selector": "#pane-side",
    },
    "gmail": {
        "url": "https://mail.google.com/",
        # Compose button — only present inside the inbox, not on the
        # Google login / account-chooser pages.
        "ready_selector": 'div[gh="cm"]',
    },
}


async def login(site: str) -> int:
    cfg = SITES[site]
    from playwright.async_api import async_playwright

    paths.ensure()
    print(f"profile: {paths.browser_profile}")
    print(f"site:    {site}  ({cfg['url']})")
    print("make sure the MIRA daemon is not running.\n")

    async with async_playwright() as p:
        # Google (and a few others) sniff out Playwright via the
        # --enable-automation flag and navigator.webdriver. Strip both so
        # the sign-in flow treats us like a real Chrome install.
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(paths.browser_profile),
            headless=False,
            viewport={"width": 1280, "height": 800},
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(cfg["url"], wait_until="domcontentloaded")

        print("sign in in the browser window.")
        print("waiting for login (up to 5 minutes)...\n")

        deadline = asyncio.get_event_loop().time() + 300
        logged_in = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                if await page.locator(cfg["ready_selector"]).count() > 0:
                    logged_in = True
                    break
            except Exception:
                pass
            await asyncio.sleep(2)

        if not logged_in:
            print("timed out waiting for sign-in. closing without saving.")
            await ctx.close()
            return 1

        print("logged in. syncing session...")
        await asyncio.sleep(5)
        await ctx.close()
        print("done. session saved to the profile.")
        return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in SITES:
        print(f"usage: site_login.py [{'|'.join(SITES)}]", file=sys.stderr)
        return 2
    return asyncio.run(login(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
