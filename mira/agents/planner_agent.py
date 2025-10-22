import re
import asyncio
from typing import Dict, Any, Optional, List
from urllib.parse import quote
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from scrapy.http import HtmlResponse
from scrapy.selector import Selector
from playwright.async_api import async_playwright

from mira.utils import logger, pdf_utils
from mira.core import domain_trust
from mira.core.config import cfg
from browser_use import BrowserSession  # ✅ visible tab


class PlannerAgent:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.browser_session: Optional[BrowserSession] = None
        self._started = False

    # ----------------- Browser-use -----------------
    async def _ensure_browser_use(self):
        if not self.browser_session or not self._started:
            self.browser_session = BrowserSession(
                headless=self.headless,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/116 Safari/537.36"
                )
            )
            await self.browser_session.start()
            self._started = True

    async def browser_use_get(self, url: str) -> str:
        await self._ensure_browser_use()
        try:
            await self.browser_session.navigate_to(url)
            return await self.browser_session.get_current_page_url()
        except Exception as e:
            logger.log_error(e, context="PlannerAgent.browser_use_get")
            return ""

    # ----------------- Requests / Scrapy -----------------
    def _scrape_with_requests(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            if resp.ok:
                return resp.text
        except Exception as e:
            logger.log_error(e, context="PlannerAgent._scrape_with_requests")
        return ""

    def _scrape_with_scrapy(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            if resp.ok:
                return HtmlResponse(url=url, body=resp.content, encoding="utf-8").text
        except Exception as e:
            logger.log_error(e, context="PlannerAgent._scrape_with_scrapy")
        return ""

    # ----------------- Playwright -----------------
    async def _scrape_with_playwright(self, url: str, intent: Optional[str] = None) -> str:
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=cfg.PLAYWRIGHT_PROFILE,
                    headless=self.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                    ],
                )
                page = await context.new_page()
                try:
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    logger.log_error(e, context="PlannerAgent._scrape_with_playwright.goto")
                    return ""

                html = await page.content()
                try:
                    await page.close()
                except Exception:
                    pass
                return html or ""
        except Exception as e:
            logger.log_error(e, context="PlannerAgent._scrape_with_playwright")
            return ""

    # ----------------- Unified Scrape -----------------
    async def _scrape_page(self, url: str, intent: Optional[str] = None) -> str:
        await self.browser_use_get(url)  # try visible tab
        html = self._scrape_with_requests(url)
        if html and len(html) > 500:
            return html
        html = self._scrape_with_scrapy(url)
        if html and len(html) > 500:
            return html
        return await self._scrape_with_playwright(url, intent=intent)

    # ----------------- Search -----------------
    async def search(self, query: str, max_sites: int = 8) -> Dict[str, Any]:
        today = datetime.now().strftime("%b %d, %Y")
        primed_q = f"{query} {today}"
        engines = {
            "brave": f"https://search.brave.com/search?q={quote(primed_q)}",
            "bing": f"https://www.bing.com/search?q={quote(primed_q)}",
            "google": f"https://www.google.com/search?q={quote(primed_q)}",
        }
        links, seen = [], set()
        for name, url in engines.items():
            html = self._scrape_with_requests(url) or self._scrape_with_scrapy(url)
            if not html:
                html = await self._scrape_with_playwright(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a"):
                href, text = a.get("href", ""), a.get_text(strip=True)
                if not href or not href.startswith("http"):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                links.append({"title": text or href, "url": href})
                if len(links) >= max_sites:
                    break
            if len(links) >= max_sites:
                break
        return {"query": query, "links": links}

    # ----------------- Weekend Suggestion -----------------
    async def suggest_weekend(self, city: str, filename="itinerary.pdf", sites=None):
        default_sites = [
            "meetup.com", "eventbrite.com", "ticketmaster.com", "stubhub.com",
            "dice.fm", "songkick.com", "seatgeek.com",
            "timeout.com", "thrillist.com", "eventful.com",
            "tripadvisor.com", "viator.com", "atlasobscura.com",
            "yelp.com", "opentable.com", "resy.com",
            "instagram.com"
        ]
        site_filter = " OR ".join([f"site:{s}" for s in (sites or default_sites)])
        query = f"things to do this weekend in {city} {site_filter}"

        results = await self.search(query)

        activities = []
        for link in results.get("links", []):
            url = link.get("url")
            if not url:
                continue
            html = await self._scrape_page(url, intent="news")
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            snippet = " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p")[:3])

            activities.append({
                "title": link.get("title") or url,
                "url": url,
                "summary": snippet or "No summary available."
            })

        pdf_utils.export_to_pdf(f"Weekend Planner for {city}", activities, filename)
        return activities

    # ----------------- Unified Handler -----------------
    async def handle(self, payload: dict):
        fn = payload.get("fn")
        if fn == "weekend":
            city = payload.get("city", "New York")
            activities = await self.suggest_weekend(city)
            return {"ok": True, "activities": activities}
        return {"ok": False, "error": f"Unknown fn: {fn}"}


