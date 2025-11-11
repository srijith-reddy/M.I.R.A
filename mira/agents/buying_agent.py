# ======================================
# mira/agents/buying_agent.py  (Hybrid-safe Playwright + BrowserUse + GPT-4o summarization)
# ======================================
import asyncio, os, re, base64, zlib, requests, shutil, psutil
import brotlicffi as brotli
from datetime import datetime
from time import time
from urllib.parse import quote
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup
from scrapy.http import HtmlResponse
from scrapy.selector import Selector
from playwright.async_api import async_playwright
from browser_use import BrowserSession
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from mira.utils import logger
from mira.core import domain_trust
from mira.core.config import cfg

# ======================================================
# 🔹 GLOBALS
# ======================================================
PROFILE_DIR = "/Users/shrey24/Desktop/mira-browser-profiles/buying"  # persistent shared cookies
PLAYWRIGHT_LOCK = asyncio.Lock()

# ======================================================================
# MAIN AGENT
# ======================================================================
class BuyingAgent:
    def __init__(self, headless: bool = False, max_tabs: int = 5):
        self.headless = headless
        self.max_tabs = max_tabs
        self.browser_session: Optional[BrowserSession] = None
        self._started = False
        self._pw = None
        self._pw_context = None

        try:
            self.llm_facts = ChatOpenAI(model="gpt-4o", temperature=0.0, max_tokens=800)
            print("✅ [DEBUG] LLM initialized for BuyingAgent.")
        except Exception as e:
            logger.log_error(e, context="BuyingAgent.llm_init")
            self.llm_facts = None

    # ======================================================
    # 🔹 Safe Playwright management (with profile sharing)
    # ======================================================
    async def _ensure_playwright(self):
        async with PLAYWRIGHT_LOCK:
            # 🧠 If BrowserUse still active → close it before Playwright starts
            if self._started:
                print("🕐 [BuyingAgent] Waiting for BrowserUse to close before Playwright starts...")
                await self.browser_session.stop()
                self._started = False

            # ✅ Clean only stale lock files (not running processes)
            lock_file = os.path.join(PROFILE_DIR, "SingletonLock")
            if os.path.exists(lock_file):
                print("🧹 [BuyingAgent] Removing stale Chrome lock...")
                try:
                    os.remove(lock_file)
                except Exception:
                    pass

            if self._pw_context:
                return  # already running

            try:
                self._pw = await async_playwright().start()
                self._pw_context = await self._pw.chromium.launch_persistent_context(
                    user_data_dir=PROFILE_DIR,
                    headless=self.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                    ],
                )
                print(f"✅ [DEBUG] Playwright context launched using shared profile: {PROFILE_DIR}")

            except Exception as e:
                # 🧩 Handle locked profile (rare, after crash)
                if "ProcessSingleton" in str(e):
                    print("⚠️ [BuyingAgent] Chrome profile locked — cleaning and retrying...")
                    shutil.rmtree(PROFILE_DIR, ignore_errors=True)
                    self._pw_context = await self._pw.chromium.launch_persistent_context(
                        user_data_dir=PROFILE_DIR,
                        headless=self.headless,
                        args=[
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-blink-features=AutomationControlled",
                            "--disable-dev-shm-usage",
                        ],
                    )
                    print("✅ [DEBUG] Relaunched clean persistent Playwright context.")
                else:
                    logger.log_error(e, context="BuyingAgent._ensure_playwright")


    async def close_playwright(self):
        if self._pw_context:
            await self._pw_context.close()
            self._pw_context = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        print("🧹 [DEBUG] Closed Playwright context for BuyingAgent.")

    # ======================================================
    # 🔹 BrowserUse (persistent cookie store)
    # ======================================================
    async def _ensure_browser_use(self):
        if not self.browser_session or not self._started:
            self.browser_session = BrowserSession(
                headless=self.headless,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116 Safari/537.36"),
                user_data_dir=PROFILE_DIR,  # same shared folder
            )
            await self.browser_session.start()
            self._started = True
            print(f"✅ [DEBUG] BrowserUse session started using profile: {PROFILE_DIR}")

    async def browser_use_get(self, url: str) -> str:
        await self._ensure_browser_use()
        try:
            await self.browser_session.navigate_to(url)
            html = await self.browser_session.get_current_page_url()
            title = await self.browser_session.get_current_page_title()
            return f"<title>{title}</title> {html}"
        except Exception as e:
            logger.log_error(e, context="BuyingAgent.browser_use_get")
            return ""

    # ======================================================
    # 🔹 Requests + Scrapy fallback
    # ======================================================
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

    def _scrape_with_requests(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            if not resp.ok:
                return ""
            return self._decode_body(resp)
        except Exception as e:
            logger.log_error(e, context="BuyingAgent._scrape_with_requests")
            return ""

    def _scrape_with_scrapy(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            if not resp.ok:
                return ""
            body = self._decode_body(resp)
            response = HtmlResponse(url=url, body=body, encoding="utf-8")
            return Selector(response).get()
        except Exception as e:
            logger.log_error(e, context="BuyingAgent._scrape_with_scrapy")
            return ""

    # ======================================================
    # 🔹 Scroll + capture
    # ======================================================
    async def _scroll_and_capture_full_page(self, page, base_path: str) -> str:
        stitched_path = f"{base_path}_stitched.png"
        try:
            await page.evaluate("""
                (async () => {
                    let lastHeight = 0;
                    while (true) {
                        window.scrollBy(0, window.innerHeight);
                        await new Promise(r => setTimeout(r, 500));
                        let newHeight = document.body.scrollHeight;
                        if (newHeight === lastHeight) break;
                        lastHeight = newHeight;
                    }
                })();
            """)
            await page.screenshot(path=stitched_path, full_page=True)
            print(f"✅ [DEBUG] Full-page screenshot saved: {stitched_path}")
            return stitched_path
        except Exception as e:
            logger.log_error(e, context="_scroll_and_capture_full_page")
            return ""

    # ======================================================
    # 🔹 Playwright scraper (safe shared profile)
    # ======================================================
    async def _scrape_with_playwright(self, url: str, capture: bool = False):
        await self._ensure_playwright()
        screenshot_path = None
        page = None
        try:
            page = await self._pw_context.new_page()
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(800)

            if capture:
                base_dir = "/Users/shrey24/Desktop/mira_screens/buying"
                os.makedirs(base_dir, exist_ok=True)
                base_path = os.path.join(base_dir, f"buying_{int(time())}")
                screenshot_path = await self._scroll_and_capture_full_page(page, base_path)

            html = await page.content()
            print(f"✅ [DEBUG] Scraped successfully: {url}")
            return html or "", screenshot_path
        except Exception as e:
            logger.log_error(e, context=f"BuyingAgent._scrape_with_playwright ({url})")
            return "", None
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    # ======================================================
    # 🔹 Unified scrape orchestrator
    # ======================================================
    async def _scrape_page(self, url: str, stateful: bool = False, intent: Optional[str] = None, capture: bool = False):
        try:
            if stateful:
                await self.browser_use_get(url)
                await self.browser_session.stop()
                self._started = False

            html, screenshot_path = await self._scrape_with_playwright(url, capture=capture)
            if html and len(html) > 1000:
                return html, screenshot_path

            html = self._scrape_with_requests(url)
            if html and len(html) > 500:
                return html, None

            html = self._scrape_with_scrapy(url)
            if html and len(html) > 500:
                return html, None

            return html, screenshot_path
        except Exception as e:
            logger.log_error(e, context="BuyingAgent._scrape_page")
            return "", None


    # ======================================================
    # 🔹 Smart extract — product / stock / deal focused
    # ======================================================
    async def smart_extract(self, query: str, url: str, stateful: bool = False) -> dict:
        """
        Scrape a URL and extract meaningful commerce or finance info:
        - products, deals, reviews, or live stock data.
        Returns cleaned text + optional screenshot.
        """
        intent = domain_trust.intent_from_query(query)
        html, screenshot_path = await self._scrape_page(url, stateful=stateful, capture=True)
        if not html or len(html) < 1000:
            return {"text": "", "screenshot_path": screenshot_path}

        try:
            soup = BeautifulSoup(html, "html.parser")
            title = (soup.title.string.strip() if soup.title else "").strip()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

            # --- intent-specific extraction ---
            if intent in ("electronics", "gadgets", "shopping", "products", "deals"):
                # Focus on reviews, specs, and prices
                paras = [
                    p.get_text(" ", strip=True)
                    for p in soup.find_all(["p", "li", "span"])
                    if any(k in p.get_text().lower() for k in (
                        "price", "deal", "offer", "discount", "spec", "review", "rating", "sale"
                    ))
                ][:6]
                extracted = " ".join(paras) if paras else text[:800]

            elif intent in ("fashion", "clothing", "style"):
                # Highlight product details and fit
                paras = [
                    p.get_text(" ", strip=True)
                    for p in soup.find_all(["p", "li", "span"])
                    if any(k in p.get_text().lower() for k in (
                        "fabric", "fit", "style", "design", "collection", "look", "trend"
                    ))
                ][:6]
                extracted = " ".join(paras) if paras else text[:800]

            elif intent in ("finance", "stock", "stocks", "markets", "investment"):
                # ✅ Capture stock price, change %, and context
                price_el = soup.select_one(
                    'fin-streamer[data-field="regularMarketPrice"], '
                    '[class*="price"], .last-price'
                )
                change_el = soup.select_one(
                    '[data-field="regularMarketChangePercent"], [class*="change"]'
                )
                price_text = price_el.text.strip() if price_el else ""
                change_text = change_el.text.strip() if change_el else ""

                # Regex fallback (e.g. $123.45)
                if not price_text:
                    m = re.search(r"\$?\s?\d{2,4}(?:\.\d{1,2})?", text)
                    if m:
                        price_text = m.group(0)

                # Extract summary around stock move
                summary_paras = [
                    p.get_text(" ", strip=True)
                    for p in soup.find_all(["p", "li"])
                    if len(p.get_text(strip=True)) > 60
                    and not re.search(r"cookie|advert|consent", p.get_text(), re.I)
                ][:5]
                summary = " ".join(summary_paras)
                extracted = f"Current Price: {price_text or 'N/A'} | Change: {change_text or 'N/A'}. {summary}"

            else:
                # Generic fallback
                paras = [
                    p.get_text(" ", strip=True)
                    for p in soup.find_all(["p", "li"])
                    if len(p.get_text(strip=True)) > 40
                ][:6]
                extracted = " ".join(paras) if paras else text[:700]

            # --- cleanup ---
            prices = re.findall(r"[\$₹€£]\s?\d+[,.]?\d*", extracted)
            if prices and "finance" not in intent:
                extracted += f" | Prices detected: {', '.join(prices[:5])}"

            extracted = re.sub(r"\s{2,}", " ", extracted).strip()
            final_text = f"{title}: {extracted}" if title else extracted

            return {"text": final_text.strip(), "screenshot_path": screenshot_path}

        except Exception as e:
            logger.log_error(e, context="BuyingAgent.smart_extract.html_parse")
            return {"text": "", "screenshot_path": screenshot_path}

    # ======================================================
    # 🔹 Search
    # ======================================================
    async def search(self, query: str, max_sites: int = 8) -> Dict[str, Any]:
        today = datetime.now().strftime("%b %d, %Y")
        primed_q = f"{query} {today}"
        engines = {
            "brave": f"https://search.brave.com/search?q={quote(primed_q)}",
            "bing": f"https://www.bing.com/search?q={quote(primed_q)}",
            "google": f"https://www.google.com/search?q={quote(primed_q)}",
        }
        all_links, seen = [], set()
        for _, url in engines.items():
            html = self._scrape_with_requests(url) or self._scrape_with_scrapy(url)
            if not html:
                html, _ = await self._scrape_with_playwright(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            raw_links = [(a.get("href", ""), a.get_text(strip=True)) for a in soup.select("a")]
            links = [
                {"title": text or href, "url": href}
                for href, text in raw_links
                if href.startswith("http")
                and not any(bad in href for bad in ["accounts.google.com", "support.google.com", "policies.google.com"])
            ]
            for link in links[:max_sites]:
                if link["url"] not in seen:
                    seen.add(link["url"])
                    all_links.append(link)
        return {"query": query, "links": all_links[:max_sites]}
    
    # ======================================================
    # 🔹 Rank and filter links (intent-aware)
    # ======================================================
    def _select_best_links(self, results: dict, intent: str, query: str, top_k: int = 4):
        ranked = sorted(
            results.get("links", []),
            key=lambda l: domain_trust.score_link(l["title"], l["url"], None, intent),
            reverse=True
        )
        trusted = [
            l for l in ranked
            if domain_trust.intent_trust_weight(domain_trust.host(l["url"]), intent, query) > 0
        ]
        return trusted or ranked[:top_k]

    # ======================================================
    # 🔹 Concurrent gather
    # ======================================================
    async def gather_sites_concurrently(self, query: str, urls: list[str]):
        sem = asyncio.Semaphore(2)
        async def fetch(url):
            async with sem:
                data = await self.smart_extract(query, url)
                return {"url": url, **data}
        return await asyncio.gather(*[fetch(u) for u in urls])

    # ======================================================
    # 🔹 GPT-4o Vision Summarization (Commerce + Finance Tuned)
    # ======================================================
    async def _multi_vision_summarize(self, query: str, site_results: list[dict]) -> str:
        """
        Summarize extracted product / stock / deal info across sites with optional screenshots.
        Uses GPT-4o for multimodal synthesis.
        """
        if not self.llm_facts:
            return "LLM unavailable."

        def _clean_snippet(text: str) -> str:
            text = re.sub(r"[*•\-\n\t\r]+", " ", text)
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"\s{2,}", " ", text)
            return text.strip()

        system_prompt = (
            "You are Mira, a calm, trustworthy commerce assistant.\n"
            "- Extract and restate only useful insights about products, prices, deals, or stocks.\n"
            "- Summarize findings naturally in 2–3 sentences, no lists or markdown.\n"
            "- If multiple sources agree, say so collectively.\n"
            "- Mention brands, tickers, or stores naturally if clear (e.g., 'On Amazon...', 'CNBC notes...').\n"
            "- Ignore ads, cookie popups, or newsletter prompts.\n"
            "- Keep tone conversational yet precise."
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"User asked: {query}\nReview all extracts and screenshots for key facts.")
        ]

        for s in site_results:
            try:
                clean_text = _clean_snippet(s.get("text", "")) or "(no readable text)"
                url = s.get("url", "")
                domain = url.split("/")[2] if "://" in url else url

                # 🖼️ Include screenshot if present
                ss_path = s.get("screenshot_path")
                if ss_path and os.path.exists(ss_path):
                    try:
                        with open(ss_path, "rb") as f:
                            img_b64 = base64.b64encode(f.read()).decode("utf-8")
                        messages.append(
                            HumanMessage(content=[
                                {"type": "text", "text": f"Screenshot from {domain} — analyze visible product details or charts."},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
                            ])
                        )
                    except Exception as e:
                        logger.log_error(e, context=f"BuyingAgent._multi_vision_summarize.imgload {domain}")

                messages.append(HumanMessage(content=f"Extracted text from {domain}:\n{clean_text}"))
            except Exception as e:
                logger.log_error(e, context=f"BuyingAgent._multi_vision_summarize.cleanloop {s.get('url')}")

        try:
            resp = self.llm_facts.invoke(messages)
            text = (resp.content or "").strip()

            # --- Cleanup
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"[\*\•\_\#\-\=\~\>\|`]+", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[\u200B-\u200D\uFEFF\u2022\u2023\u25AA\u25CF]", " ", text)
            text = re.sub(r"\s{2,}", " ", text).strip()

            return text.encode("ascii", "ignore").decode()[:900]

        except Exception as e:
            logger.log_error(e, context="BuyingAgent._multi_vision_summarize.invoke_fail")
            return "I couldn’t summarize the results right now."


    # ======================================================
    # 🔹 Discover products / deals (trust-ranked)
    # ======================================================
    async def discover(self, query: str):
        # 🔍 Determine search intent + expanded keywords
        intent = domain_trust.intent_from_query(query)
        expanded = domain_trust.expand_query(intent)
        search_q = f"{query} {expanded}"

        print(f"[BuyingAgent] Intent={intent} | Query={search_q}")

        # 🧠 1️⃣ Perform multi-engine search
        search_results = await self.search(search_q)

        # 🧮 2️⃣ Rank + filter using domain trust
        chosen_links = self._select_best_links(search_results, intent, query, top_k=4)
        urls = [l["url"] for l in chosen_links]

        print(f"[BuyingAgent] Selected {len(urls)} trusted URLs for intent '{intent}'")

        # ⚡ 3️⃣ Extract concurrently with screenshots
        site_results = await self.gather_sites_concurrently(query, urls)

        # 🧾 4️⃣ Summarize all results via GPT-4o
        summary = await self._multi_vision_summarize(query, site_results)

        # 🧹 5️⃣ Cleanup: delete screenshots after summarization
        for s in site_results:
            try:
                if s.get("screenshot_path") and os.path.exists(s["screenshot_path"]):
                    os.remove(s["screenshot_path"])
                    print(f"🧹 [DEBUG] Deleted screenshot: {s['screenshot_path']}")
            except Exception as e:
                logger.log_error(e, context="BuyingAgent.discover.cleanup")

        # ✅ 6️⃣ Return final structured response
        return {
            "intent": intent,
            "summary": summary,
            "sources": urls
        }


    # ======================================================
    # 🔹 Unified Payload Handler (commerce / finance only)
    # ======================================================
    async def handle(self, payload: dict):
        """
        Unified entrypoint for BuyingAgent.
        Handles product discovery, deal comparisons, or stock/finance lookups.

        Expected payload:
          {
            "fn": "discover",
            "query": "best laptops under $1000"
          }
        """
        fn = payload.get("fn")
        query = (payload.get("query") or "").strip()

        if not query:
            return {"ok": False, "error": "Query is required."}

        try:
            #  Product / deal / finance discovery
            if fn == "discover":
                result = await self.discover(query)
                return {"ok": True, "result": result}

            #  Unknown function — this agent only supports commerce/finance discovery
            return {
                "ok": False,
                "error": f"Unsupported fn '{fn}' for BuyingAgent. "
                         "Use 'discover' for product or stock lookups."
            }

        except Exception as e:
            logger.log_error(e, context="BuyingAgent.handle")
            return {"ok": False, "error": f"Internal error: {e}"}
