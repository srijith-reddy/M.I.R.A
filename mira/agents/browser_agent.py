# mira/agents/browser_agent.py
import asyncio
import re
import inflect
from typing import Dict, Any, Optional
from urllib.parse import quote
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from scrapy.http import HtmlResponse
from scrapy.selector import Selector
from mira.core.config import cfg
from mira.utils import logger
from browser_use import BrowserSession
from mira.core import domain_trust
import brotli
import zlib

_engine = inflect.engine()
def _normalize_scores(text: str) -> str:
    """
    Normalize sports scores for TTS:
    - Convert '38-30' or '38,30' → 'thirty-eight to thirty'
    - Skip large stat numbers like '120 yards', '10 pts', etc.
    """
    if not text:
        return text

    def repl(m):
        n1, n2 = int(m.group(1)), int(m.group(2))
        return f"{_engine.number_to_words(n1)} to {_engine.number_to_words(n2)}"

    score_pattern = re.compile(
        r"\b(\d{1,3})[,–-](\d{1,3})\b"
        r"(?!\s*(?:yards?|yds?|pts?|reb|ast|stl|blk|fouls?|mins?|turnovers?))",
        re.IGNORECASE,
    )

    return score_pattern.sub(repl, text)

class BrowserAgent:
    def __init__(self, headless: bool = False, max_tabs: int = 5):
        self.headless = headless
        self.browser_session: Optional[BrowserSession] = None
        self.max_tabs = max_tabs
        self._started = False

    # ----------------- Browser-use Session -----------------
    async def _ensure_browser_use(self):
        """Lazy init for browser-use session."""
        if not self.browser_session or not self._started:
            try:
                self.browser_session = BrowserSession(
                    headless=self.headless,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/116 Safari/537.36"
                    ),
                )
                await self.browser_session.start()
                self._started = True
            except Exception as e:
                logger.log_error(e, context="BrowserAgent._ensure_browser_use")

    async def browser_use_get(self, url: str) -> str:
        """Fetch page content using browser-use (opens or navigates current tab)."""
        await self._ensure_browser_use()
        try:
            await self.browser_session.navigate_to(url)
            html = await self.browser_session.get_current_page_url()
            title = await self.browser_session.get_current_page_title()
            return f"<title>{title}</title> {html}"
        except Exception as e:
            logger.log_error(e, context="BrowserAgent.browser_use_get")
            return ""

    # ----------------- Requests + Scrapy (with Brotli/gzip) -----------------
    def _decode_body(self, resp: requests.Response) -> str:
        try:
            enc = resp.headers.get("content-encoding", "").lower()
            if "br" in enc:
                return brotli.decompress(resp.content).decode("utf-8", errors="ignore")
            elif "gzip" in enc:
                return zlib.decompress(resp.content, 16 + zlib.MAX_WBITS).decode("utf-8", errors="ignore")
            return resp.text
        except Exception:
            return resp.text

    def _scrape_with_scrapy(self, url: str) -> str:
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/116 Safari/537.36"
                ),
                "Accept-Encoding": "gzip, deflate, br"
            }
            resp = requests.get(url, timeout=12, headers=headers)
            if not resp.ok:
                return ""
            body = self._decode_body(resp)
            response = HtmlResponse(url=url, body=body, encoding="utf-8")
            return Selector(response).get()
        except Exception as e:
            logger.log_error(e, context="BrowserAgent._scrape_with_scrapy")
            return ""

    def _scrape_with_requests(self, url: str) -> str:
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/116 Safari/537.36"
                    ),
                    "Accept-Encoding": "gzip, deflate, br"
                },
                timeout=12,
            )
            if not resp.ok:
                return ""
            return self._decode_body(resp)
        except Exception as e:
            logger.log_error(e, context="BrowserAgent._scrape_with_requests")
            return ""

    # ----------------- Playwright -----------------
    async def _scrape_with_playwright(self, url: str, intent: Optional[str] = None) -> str:
        """General page scraper with persistent context and consistent waits."""
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

                # Always use domcontentloaded
                await page.goto(url, timeout=45000, wait_until="domcontentloaded")

                # ⏳ Extra wait for dynamic rendering
                extra_wait = 8000 if intent in ("sports", "finance") else 5000
                await page.wait_for_timeout(extra_wait)

                # News-specific: check article paragraphs
                if intent == "news":
                    try:
                        await page.wait_for_selector(
                            "article p, main p, .story p, #content p, #main-content p, "
                            ".article-body p, .post-content p, .entry-content p, .paragraph",
                            timeout=6000
                        )
                    except Exception:
                        logger.log_error(
                            "No article paragraphs found",
                            context="BrowserAgent._scrape_with_playwright"
                        )

                # --- Trigger lazy load (shallow scroll) ---
                try:
                    await page.evaluate(
                        "window.scrollBy(0, Math.min(1200, document.body.scrollHeight));"
                    )
                    await page.wait_for_timeout(1000)  # allow JS to render
                except Exception:
                    pass

                html = await page.content()

                try:
                    await page.close()
                except Exception:
                    pass

                return html or ""
        except Exception as e:
            logger.log_error(e, context="BrowserAgent._scrape_with_playwright")
            return ""


    # ----------------- Unified Entry -----------------
    async def _scrape_page(self, url: str, stateful: bool = False, intent: Optional[str] = None) -> str:
        if stateful:
            await self.browser_use_get(url)
            html = await self._scrape_with_playwright(url, intent=intent)
            if html:
                return html

        html = self._scrape_with_requests(url)
        if html and len(html) > 500:
            return html
        html = self._scrape_with_scrapy(url)
        if html and len(html) > 500:
            return html
        return await self._scrape_with_playwright(url, intent=intent)

    # ----------------- Query Normalization -----------------
    def _normalize_query(self, query: str) -> str:
        fillers = [
            "could you", "can you", "please", "tell me", "thank you",
            "add", "show me", "what is", "what's", "fetch", "search for", "find",
        ]
        q = query.lower()
        for f in fillers:
            q = q.replace(f, "")
        return re.sub(r"\s+", " ", q).strip()

    # ----------------- Search Engine Aggregator -----------------
    async def search(self, query: str, max_sites: int = 8) -> Dict[str, Any]:
        today = datetime.now().strftime("%b %d, %Y")
        primed_q = f"{self._normalize_query(query)} {today}"
        engines = {
            "brave": f"https://search.brave.com/search?q={quote(primed_q)}",
            "bing": f"https://www.bing.com/search?q={quote(primed_q)}",
            "google": f"https://www.google.com/search?q={quote(primed_q)}",
        }
        all_links, seen = [], set()
        for _, url in engines.items():
            html = self._scrape_with_requests(url) or self._scrape_with_scrapy(url)
            if not html:
                html = await self._scrape_with_playwright(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            raw_links = [
                (a.get("href", ""), a.get_text(strip=True))
                for a in soup.select("a")
            ]
            links = [
                {"title": text or href, "url": href}
                for href, text in raw_links
                if href and href.startswith("http")
                and not any(bad in href for bad in ["accounts.google.com", "support.google.com", "policies.google.com"])
            ]
            for link in links[:max_sites]:
                if link["url"] not in seen:
                    seen.add(link["url"])
                    all_links.append(link)
        return {"query": query, "links": all_links[:max_sites]}

    # ----------------- Smart Extract -----------------
    async def smart_extract(self, query: str, url: str, stateful: bool = False) -> str:
        intent = domain_trust.intent_from_query(query)
        html = await self._scrape_page(url, stateful=stateful, intent=intent)
        if not html:
            return f"I couldn’t extract details, but I opened the article: {url}"
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string.strip() if soup.title else "").strip()
        text_content = soup.get_text(" ", strip=True)
        q = query.lower()

        # 🏟 Sports
        if intent == "sports":
            # detect sport context
            sport = ""
            q_lower = query.lower()
            if any(w in q_lower for w in ["mlb", "baseball"]):
                sport = "baseball"
            elif any(w in q_lower for w in ["soccer", "football (soccer)", "premier", "la liga", "serie a", "fifa"]):
                sport = "soccer"
            elif any(w in q_lower for w in ["nhl", "hockey"]):
                sport = "hockey"
            else:
                sport = "high_scoring"  # NBA, NFL default

            final_score_pattern = re.compile(
                r"(?:final|full[-\s]?time|ft|result)\D{0,30}?(\d{1,3})\s*[-–:]\s*(\d{1,3})",
                re.IGNORECASE
            )
            if m := final_score_pattern.search(text_content):
                h, a = int(m.group(1)), int(m.group(2))
                if sport == "high_scoring":
                    if max(h, a) >= 10:
                        return f"Final score: {h} to {a}"
                else:
                    return f"Final score: {h} to {a}"

            score_pattern = re.compile(
                r"(?:score|quarter|half|period|innings|beat|defeated).{0,40}?"
                r"(\d{1,3})\s*[-–:]\s*(\d{1,3})",
                re.IGNORECASE
            )
            if m := score_pattern.search(text_content):
                h, a = int(m.group(1)), int(m.group(2))
                if sport == "high_scoring":
                    if h > 3 or a > 3:  # filter out bogus 0–2 in NBA/NFL
                        return f"Latest score I found is {h} to {a}"
                else:
                    return f"Latest score I found is {h} to {a}"

        # 📰 News
        if intent == "news":
            paras = [
                p.get_text(" ", strip=True)
                for p in soup.find_all("p")
                if re.search(r"[A-Z].+[.!?]", p.get_text())
            ][:3]
            if paras:
                cleaned = _normalize_scores(' '.join(paras))
                return f"Here’s a quick update: {cleaned}"

        snippet = (text_content[:400] + "...") if len(text_content) > 400 else text_content
        cleaned = _normalize_scores(snippet)
        return f"{title}: {cleaned}" if title else cleaned

    # ----------------- Answer Query -----------------
    async def answer_query(self, query: str, url: Optional[str] = None, stateful: bool = False) -> str:
            if url and url.startswith("http"):
                return await self.smart_extract(query, url.strip(), stateful=stateful)

            results = await self.search(query, max_sites=5)
            links = results.get("links", [])
            if not links:
                return f"Sorry, I couldn’t find any relevant sources for '{query}'."

            q_lower = query.lower()
            force_stateful = False
            if any(w in q_lower for w in ["news", "update", "headline", "breaking"]):
                force_stateful = True
            elif any(w in q_lower for w in ["score", "match", "game", "result", "record", "stats", "player", "team"]):
                force_stateful = True

            intent = domain_trust.intent_from_query(query)

            chosen = None
            for link in links:
                link_url = (link.get("url") or "").strip()
                if link_url.startswith("http"):
                    h = domain_trust.host(link_url)
                    if domain_trust.intent_trust_weight(h, intent, query) > 0:
                        chosen = link
                        break

            if not chosen:
                for link in links:
                    link_url = (link.get("url") or "").strip()
                    if link_url.startswith("http"):
                        chosen = link
                        break

            if not chosen:
                return f"Found results for '{query}', but none had valid URLs."

            return await self.smart_extract(query, chosen["url"], stateful=force_stateful)