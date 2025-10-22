# mira/agents/buying_agent.py
import re
import urllib.parse
from typing import Dict, Any, Optional, List
from urllib.parse import quote
from datetime import datetime

import requests
from scrapy.http import HtmlResponse
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from mira.utils import logger, pdf_utils
from mira.core import domain_trust
from mira.core.config import cfg
from browser_use import BrowserSession  # ✅ visible tab


class BuyingAgent:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.browser_session: Optional[BrowserSession] = None
        self._started = False

    # ----------------- Browser-use -----------------
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
            logger.log_error(e, context="BuyingAgent.browser_use_get")
            return ""

    # ----------------- Requests / Scrapy fallback -----------------
    def _scrape_with_requests(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.log_error(e, context="BuyingAgent._scrape_with_requests")
        return ""

    def _scrape_with_scrapy(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                return HtmlResponse(url=url, body=resp.content, encoding="utf-8").text
        except Exception as e:
            logger.log_error(e, context="BuyingAgent._scrape_with_scrapy")
        return ""

    # ----------------- Playwright Scraper -----------------
    async def _scrape_with_playwright(self, url: str) -> str:
        """Scraper using persistent Playwright context."""
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=cfg.PLAYWRIGHT_PROFILE,  # ✅ cookies/session persist
                    headless=self.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                    ],
                )
                page = await context.new_page()
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)  # ⏳ allow lazy-loaded content
                html = await page.content()
                try:
                    await page.close()
                except Exception:
                    pass
                return html or ""
        except Exception as e:
            logger.log_error(e, context="BuyingAgent._scrape_with_playwright")
            return ""

    # ----------------- Playwright-based Search -----------------
    async def search(self, query: str, max_sites: int = 8) -> Dict[str, Any]:
            """Use Playwright persistent context to collect real search result links from Brave, Bing, Google."""
            today = datetime.now().strftime("%b %d, %Y")
            primed_q = f"{query}" if "price" in query.lower() else f"{query} {today}"

            search_engines = {
                "brave": f"https://search.brave.com/search?q={quote(primed_q)}",
                "bing": f"https://www.bing.com/search?q={quote(primed_q)}",
                "google": f"https://www.google.com/search?q={quote(primed_q)}",
            }

            links, seen = [], set()
            BLOCKED_DOMAINS = {"walmart.com", "bestbuy.com"}  # 🚫 hard block

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

                    for engine, url in search_engines.items():
                        try:
                            page = await context.new_page()
                            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                            await page.wait_for_timeout(2000)

                            try:
                                await page.wait_for_selector("a[href]", timeout=3000)
                            except Exception:
                                logger.log_error(f"No anchors found on {engine}", context="BuyingAgent.search")

                            elements = await page.query_selector_all("a[href]") or []

                            for el in elements:
                                try:
                                    href = await el.get_attribute("href")
                                    text = (await el.inner_text()) or ""
                                except Exception:
                                    continue

                                if not href or not href.startswith("http"):
                                    continue

                                # 🚫 filter junk + blocked domains
                                h = domain_trust.host(href)
                                if any(bad in href for bad in [
                                    "accounts.google.com", "support.google.com", "policies.google.com",
                                    "captcha", "help.brave.com"
                                ]):
                                    continue
                                if h in BLOCKED_DOMAINS:
                                    continue

                                if href not in seen:
                                    seen.add(href)
                                    links.append({"title": text.strip()[:100], "url": href})
                                    if len(links) >= max_sites:
                                        break

                            try:
                                await page.close()
                            except Exception:
                                pass

                            if len(links) >= max_sites:
                                break
                        except Exception as e:
                            logger.log_error(e, context=f"BuyingAgent.search.{engine}")

            except Exception as e:
                logger.log_error(e, context="BuyingAgent.search (Playwright)")

            return {"query": query, "links": links[:max_sites]}


    # ----------------- Crawl Products -----------------
    async def crawl_products(self, query: str, filename="products.pdf") -> List[Dict[str, Any]]:
        results = await self.search(f"buy {query}")
        intent = "price"
        ranked = sorted(results["links"],
                        key=lambda l: domain_trust.score_link(l["title"], l["url"], None, intent),
                        reverse=True)
        trusted = [l for l in ranked if domain_trust.intent_trust_weight(domain_trust.host(l["url"]), intent, query) > 0]
        chosen_links = trusted or ranked[:3]

        products = []
        for link in chosen_links:
            await self.browser_use_get(link["url"])
            html = await self._scrape_with_playwright(link["url"])
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            for item in soup.select("div, li, article")[:20]:
                text = item.get_text(" ", strip=True)
                if not text:
                    continue

                price_match = None
                price_el = item.select_one('span[class*="price"], div[class*="price"], span[class*="amount"]')
                if price_el:
                    price_match = price_el.get_text(strip=True)
                else:
                    m = re.search(r"[\$₹€£]\s?\d+[,.]?\d*", text)
                    if m:
                        price_match = m.group(0)

                if price_match:
                    products.append({"title": text[:80], "price": price_match, "url": link["url"]})

        pdf_utils.export_to_pdf(f"Product Comparison: {query}", products, filename)
        return products

    # ----------------- Stock Price -----------------
    async def stock_price(self, symbol: str) -> Dict[str, Any]:
        results = await self.search(f"{symbol} stock price")
        intent = "finance"
        ranked = sorted(
            results["links"],
            key=lambda l: domain_trust.score_link(l["title"], l["url"], None, intent),
            reverse=True,
        )
        trusted = [
            l for l in ranked
            if domain_trust.intent_trust_weight(domain_trust.host(l["url"]), intent, symbol) > 0
        ]
        chosen_links = trusted or ranked[:3]

        prices = []
        for link in chosen_links:
            await self.browser_use_get(link["url"])
            html = await self._scrape_with_playwright(link["url"])
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            price = None

            # --- Try Yahoo Finance style ---
            el = soup.select_one('fin-streamer[data-field="regularMarketPrice"]')
            if el:
                price = el.text.strip()

            # --- Generic price selectors (MarketWatch, CNBC, etc.) ---
            if not price:
                el = soup.select_one('[class*="price"], [data-field*="Last"], .last-price')
                if el:
                    price = el.text.strip()

            # --- Regex fallback (any $123.45 style match) ---
            if not price:
                m = re.search(r"\$?\s?\d{2,4}(?:\.\d{1,2})?", soup.get_text(" ", strip=True))
                if m:
                    price = m.group(0)

            if price:
                prices.append({"symbol": symbol, "price": price, "source": link["url"]})

        if not prices:
            return {"ok": False, "error": f"No stock price found for {symbol}."}
        return {"ok": True, "prices": prices}

    
        # ----------------- General Query -----------------
    async def answer_query(self, query: str, url: Optional[str] = None, stateful: bool = False) -> Dict[str, Any]:
        # 🚨 Auto-route stock price queries (voice-friendly)
        if "stock" in query.lower():
            words = query.replace("’", "'").split()
            words = [w.replace("'s", "") for w in words]  # ✅ strip possessives
            ticker = None

            try:
                if "stock" in words:
                    idx = words.index("stock")
                    if idx > 0:
                        ticker = words[idx - 1]
                elif "price" in words:
                    idx = words.index("price")
                    if idx > 0:
                        ticker = words[idx - 1]
            except ValueError:
                pass

            if ticker:
                candidate = ticker.strip("?.!,'\"").upper()
                if candidate.isalpha() and len(candidate) <= 5:
                    ticker = candidate
                else:
                    ticker = ticker.capitalize()
                return await self.stock_price(ticker)



        # 🔹 existing logic unchanged
        if url and url.startswith("http"):
            await self.browser_use_get(url)
            html = await self._scrape_with_playwright(url)
            return {"ok": True, "results": [{"url": url, "content": html[:2000]}]}

        results = await self.search(query, max_sites=5)
        intent = domain_trust.intent_from_query(query)
        ranked = sorted(results.get("links", []),
                        key=lambda l: domain_trust.score_link(l["title"], l["url"], None, intent),
                        reverse=True)
        trusted = [l for l in ranked if domain_trust.intent_trust_weight(domain_trust.host(l["url"]), intent, query) > 0]
        chosen_links = trusted or ranked[:3]

        all_results = []
        for link in chosen_links:
            await self.browser_use_get(link["url"])
            html = await self._scrape_with_playwright(link["url"])
            if html:
                all_results.append({"url": link["url"], "content": html[:2000]})

        if not all_results:
            return {"ok": False, "error": f"No valid content for '{query}'"}
        return {"ok": True, "results": all_results}

    # ----------------- Unified Handler -----------------
    async def handle(self, payload: dict):
        fn = payload.get("fn")

        if fn == "crawl":
            query = payload.get("query", "products")
            products = await self.crawl_products(query)
            return {"ok": True, "products": products}

        elif fn == "stock_price":
            symbol = payload.get("query")
            if not symbol:
                return {"ok": False, "error": "query (symbol) is required for stock_price"}
            return await self.stock_price(symbol)

        elif fn == "query":
            q = payload.get("query")
            if not q:
                return {"ok": False, "error": "query is required"}
            return await self.answer_query(q)

        return {"ok": False, "error": f"Unknown fn: {fn}"}
