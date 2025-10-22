# mira/agents/booking_agent.py
import re
from typing import Optional
from urllib.parse import quote, urljoin
from playwright.async_api import async_playwright
import requests
from bs4 import BeautifulSoup
from scrapy.http import HtmlResponse
from scrapy.selector import Selector
from mira.core.config import cfg
from mira.utils import logger
from browser_use import BrowserSession  # ✅ visible tab
import os

WORDS_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10
}

# ✅ Only Fandango now
BOOKING_SITES = [
    "fandango.com",
]

SEARCH_ENDPOINTS = {
    "fandango.com": "/search?q=",
}


class BookingAgent:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.browser_session: Optional[BrowserSession] = None
        self._started = False

    # ----------------------------
    # Browser-use Session
    # ----------------------------
    async def _ensure_browser_use(self):
        if not self.browser_session or not self._started:
            self.browser_session = BrowserSession(
                headless=self.headless,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/116 Safari/537.36"
            )
            await self.browser_session.start()
            self._started = True

    async def browser_use_get(self, url: str) -> str:
        await self._ensure_browser_use()
        try:
            await self.browser_session.navigate_to(url)
            return await self.browser_session.get_current_page_url()
        except Exception as e:
            logger.log_error(e, context="BookingAgent.browser_use_get")
            return ""

    # ----------------------------
    # Bot wall detection
    # ----------------------------
    def _is_bot_wall(self, html: str, url: str) -> bool:
        if not html:
            return False
        return any(k in html.lower() for k in ["captcha", "g-recaptcha", "detected unusual traffic"])

    # ----------------------------
    # Scraping layers
    # ----------------------------
    def _scrape_with_requests(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            return resp.text if resp.ok else ""
        except Exception as e:
            logger.log_error(e, context="BookingAgent._scrape_with_requests")
            return ""

    def _scrape_with_scrapy(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            if not resp.ok:
                return ""
            response = HtmlResponse(url=url, body=resp.content, encoding=resp.encoding or "utf-8")
            return Selector(response).get()
        except Exception as e:
            logger.log_error(e, context="BookingAgent._scrape_with_scrapy")
            return ""

    async def _scrape_with_playwright(self, url: str) -> str:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                if os.path.exists("booking_state.json"):
                    context = await browser.new_context(
                        storage_state="booking_state.json",
                        permissions=["geolocation"],  # ✅ geo permission
                        geolocation={"latitude": 40.7128, "longitude": -74.0060},  # NYC default
                    )
                else:
                    context = await browser.new_context(
                        permissions=["geolocation"],
                        geolocation={"latitude": 40.7128, "longitude": -74.0060},
                    )

                page = await context.new_page()
                await page.goto(url, timeout=30000, wait_until="networkidle")
                try:
                    await page.wait_for_selector(
                        "a[href*='ticket'], a[href*='showtime'], button:has-text('Tickets')",
                        timeout=8000
                    )
                except Exception:
                    pass  # continue even if not found

                html = await page.content()
                await context.storage_state(path="booking_state.json")
                await browser.close()
                return html
        except Exception as e:
            logger.log_error(e, context="BookingAgent._scrape_with_playwright")
            return ""

    async def _scrape_page(self, url: str, stateful: bool = True) -> str:
        if stateful:
            await self.browser_use_get(url)
            html = await self._scrape_with_playwright(url)
            if html and not self._is_bot_wall(html, url):
                return html

        html = self._scrape_with_requests(url)
        if html and not self._is_bot_wall(html, url):
            return html
        html = self._scrape_with_scrapy(url)
        if html and not self._is_bot_wall(html, url):
            return html

        html = await self._scrape_with_playwright(url)
        return "" if self._is_bot_wall(html, url) else html

    # ----------------------------
    # Extract links
    # ----------------------------
    def _extract_links_from_html(self, html: str, base_url: str = "", max_links=20):
        soup = BeautifulSoup(html or "", "html.parser")
        links = []

        CTA_KEYWORDS = [
            "buy tickets", "get tickets", "see showtimes", "select seats",
            "reserve seats", "book tickets"
        ]

        # Anchors
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            text = (a.get_text(strip=True) or "").lower()
            if not href:
                continue
            if href.startswith("/") and base_url:
                href = urljoin(base_url, href)

            is_cta = any(kw in text for kw in CTA_KEYWORDS) or any(kw in href.lower() for kw in CTA_KEYWORDS)
            links.append({"title": text or href, "url": href, "priority": is_cta})

        # Buttons
        for btn in soup.select("button, div[role='button']"):
            text = (btn.get_text(strip=True) or "").lower()
            if any(kw in text for kw in CTA_KEYWORDS):
                links.append({"title": text, "url": base_url, "priority": True})

        for l in links[:5]:
            logger.log_event("BookingAgent._extract_links_from_html", f"Extracted link: {l}")

        links = sorted(links, key=lambda l: not l["priority"])
        return links[:max_links]

    # ----------------------------
    # Book Ticket
    # ----------------------------
    async def book_ticket(self, title: str, theater: Optional[str] = None,
                          count: int = 1, url: Optional[str] = None):
        if url:
            return {"ok": True, "link": url, "count": count}

        last_search_url = None
        for site in BOOKING_SITES:
            endpoint = SEARCH_ENDPOINTS.get(site, "/search?q=")
            if not endpoint:
                continue

            # ✅ Only Fandango
            query = title
            search_url = f"https://{site}{endpoint}{quote(query)}&mode=movies"
            last_search_url = search_url
            html = await self._scrape_page(search_url, stateful=True)
            links = self._extract_links_from_html(html, base_url=search_url, max_links=20)

            booking_url = next((l["url"] for l in links if l.get("priority")), None)

            # Try detail page if no CTA
            if not booking_url:
                detail_url = next((l["url"] for l in links if "movie" in l["url"] or "film" in l["url"]), None)
                if detail_url:
                    detail_html = await self._scrape_page(detail_url, stateful=True)
                    detail_links = self._extract_links_from_html(detail_html, base_url=detail_url, max_links=20)
                    booking_url = next((l["url"] for l in detail_links if l.get("priority")), None)

            if booking_url:
                await self.browser_use_get(booking_url)
                return {"ok": True, "title": title, "theater": theater, "count": count, "booking_url": booking_url}

            await self.browser_use_get(search_url)

        return {"ok": False, "error": f"No direct booking link found for {title}. Last tried {last_search_url}."}


    # ----------------------------
    # Flights (Google Flights)
    # ----------------------------
    async def search_flights(self, origin: str, dest: str, depart_date: str,
                             return_date: Optional[str] = None, passengers: int = 1):
        base = "https://www.google.com/travel/flights"
        if return_date:
            q = f"flights from {origin} to {dest} on {depart_date} returning {return_date}"
        else:
            q = f"flights from {origin} to {dest} on {depart_date}"
        url = f"{base}?q={quote(q)}"
        await self.browser_use_get(url)
        return {"ok": True, "origin": origin, "dest": dest,
                "depart": depart_date, "return": return_date,
                "passengers": passengers, "url": url}

    # ----------------------------
    # Unified Handler
    # ----------------------------
    async def handle(self, payload: dict | str):
        if isinstance(payload, str):
            q = payload.lower()

            # Flights
            if "flight" in q or "fly" in q:
                m = re.search(r"from (.+?) to (.+?)(?: on|$)", q)
                d = re.search(r"on ([A-Za-z0-9 ,]+)", q)
                r = re.search(r"return(?:ing)? ([A-Za-z0-9 ,]+)", q)
                c = re.search(r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten) (?:passengers?|tickets?)", q)

                passengers_raw = c.group(1) if c else "1"
                passengers = int(WORDS_TO_NUM.get(passengers_raw.lower(), passengers_raw))

                return await self.search_flights(
                    origin=m.group(1) if m else "NYC",
                    dest=m.group(2) if m else "LAX",
                    depart_date=d.group(1) if d else "next week",
                    return_date=r.group(1) if r else None,
                    passengers=passengers
                )

            # Movie tickets
            if "ticket" in q or "movie" in q:
                m = re.search(r"ticket(?:s)? for (.+?)(?=\s+(?:at|in)\s+|[,.]|$)", q)
                title = m.group(1).strip() if m else None

                t = re.search(r"(?:at|in)\s+([a-zA-Z\s]+?)(?:[,.]|$)", q)
                theater = t.group(1).strip() if t else None

                c = re.search(r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten) tickets?", q)
                count_raw = c.group(1) if c else "1"
                count = int(WORDS_TO_NUM.get(count_raw.lower(), count_raw))

                return await self.book_ticket(
                    title=title,
                    theater=theater,
                    count=count
                )

            return {"ok": False, "error": "Could not parse booking request."}

        # Dict payload
        fn = payload.get("fn")
        if fn == "flights":
            return {"ok": True, "results": await self.search_flights(
                payload.get("origin"), payload.get("dest"),
                payload.get("depart"), payload.get("return"),
                int(payload.get("passengers", 1))
            )}
        elif fn == "book":
            return {"ok": True, "results": await self.book_ticket(
                title=payload.get("title"), theater=payload.get("theater"),
                count=int(payload.get("count", 1))
            )}
        return {"ok": False, "error": f"Unknown fn: {fn}"}
