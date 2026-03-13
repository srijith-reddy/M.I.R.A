# ======================================
# mira/agents/planner_agent.py  (smart city & trail discovery)
# ======================================
import asyncio, os, re, base64, zlib, inflect, requests
import brotlicffi as brotli
from time import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from urllib.parse import quote
from bs4 import BeautifulSoup
from scrapy.http import HtmlResponse
from scrapy.selector import Selector
from playwright.async_api import async_playwright
from browser_use import BrowserSession
from mira.utils import logger
from mira.core import domain_trust
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import shutil
from PIL import Image, ImageEnhance
import os
_engine = inflect.engine()


# ---------------- Helpers ----------------
def _clean_text(text: str) -> str:
    text = re.sub(r"[\*\•\_\#\-\=\~\>\|`]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# ======================================================
# 🔹 GLOBALS
# ======================================================
PROFILE_DIR = "/Users/shrey24/Desktop/mira-browser-profiles/planner"  # shared persistent cookies
PLAYWRIGHT_LOCK = asyncio.Lock()

# ======================================================================
# MAIN AGENT
# ======================================================================
class PlannerAgent:
    def __init__(self, headless: bool = False, max_tabs: int = 4):
        self.headless = headless
        self.max_tabs = max_tabs
        self.browser_session: Optional[BrowserSession] = None
        self._started = False
        self._pw = None
        self._pw_context = None

        try:
            self.llm_facts = ChatOpenAI(model="gpt-4o", temperature=0.1, max_tokens=800)
            print("✅ [DEBUG] LLM initialized for PlannerAgent.")
        except Exception as e:
            logger.log_error(e, context="PlannerAgent.llm_init")
            self.llm_facts = None

    # ======================================================
    # 🔹 Safe Playwright management (shared profile)
    # ======================================================
    async def _ensure_playwright(self):
        async with PLAYWRIGHT_LOCK:
            # 🧠 Close BrowserUse first if it’s active
            if self._started:
                print("🕐 [PlannerAgent] Closing BrowserUse before Playwright starts...")
                try:
                    await self.browser_session.stop()
                except Exception:
                    pass
                self._started = False

            # 🧹 Remove only stale lock file (avoid process kill)
            lock_file = os.path.join(PROFILE_DIR, "SingletonLock")
            if os.path.exists(lock_file):
                print("🧹 [PlannerAgent] Removing stale Chrome lock...")
                try:
                    os.remove(lock_file)
                except Exception:
                    pass

            if self._pw_context:
                print("♻️ [PlannerAgent] Reusing existing Playwright context.")
                return

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
                # 🧩 Handle stale lock after crash
                if "ProcessSingleton" in str(e):
                    print("⚠️ [PlannerAgent] Chrome profile locked — cleaning and retrying...")
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
                    logger.log_error(e, context="PlannerAgent._ensure_playwright")

    async def close_playwright(self):
        if self._pw_context:
            await self._pw_context.close()
            self._pw_context = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        print("🧹 [DEBUG] Closed Playwright context for PlannerAgent.")

    # ======================================================
    # 🔹 BrowserUse (shared cookie storage)
    # ======================================================
    async def _ensure_browser_use(self):
        if not self.browser_session or not self._started:
            self.browser_session = BrowserSession(
                headless=self.headless,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116 Safari/537.36"),
                user_data_dir=PROFILE_DIR,  # same folder for shared cookies
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
            logger.log_error(e, context="PlannerAgent.browser_use_get")
            return ""

    # ======================================================
    # 🔹 Core scrapers
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
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/116 Safari/537.36",
                "Accept-Encoding": "gzip, deflate, br",
            })
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            # ✅ Let requests handle Brotli internally
            return resp.text
        except Exception as e:
            logger.log_error(e, context="BrowserAgent._scrape_with_requests")
            return ""

    def _scrape_with_scrapy(self, url: str) -> str:
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/116 Safari/537.36",
                "Accept-Encoding": "gzip, deflate, br",
            })
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            # ✅ No manual decoding
            response = HtmlResponse(url=url, body=resp.text, encoding="utf-8")
            return Selector(response).get()
        except Exception as e:
            logger.log_error(e, context="BrowserAgent._scrape_with_scrapy")
            return ""

   
    # ======================================================
    # 🌆 Contextual Screenshot Capture for PlannerAgent
    # ======================================================
    from PIL import Image, ImageEnhance
    import os

    async def _scroll_and_capture_full_page(self, page, base_path: str) -> str:
        """
        Captures high-resolution screenshots optimized for GPT-4o Vision.
        Focuses on contextual and location-rich sections — maps, venues, events.
        """
        stitched_path = f"{base_path}_stitched.png"
        try:
            domain = page.url.split("/")[2] if "://" in page.url else page.url

            # 🖱️ Smooth scroll to load dynamic content
            await page.evaluate("""
                (async () => {
                    let lastHeight = 0;
                    while (true) {
                        window.scrollBy(0, window.innerHeight);
                        await new Promise(r => setTimeout(r, 500));
                        const newHeight = document.body.scrollHeight;
                        if (newHeight === lastHeight) break;
                        lastHeight = newHeight;
                    }
                    window.scrollTo(0, 0);
                })();
            """)
            await page.wait_for_timeout(1000)
            await page.emulate_media(media="screen")
            await page.set_viewport_size({"width": 1400, "height": 2400})

            # 🧭 Focused region capture for maps, venues, events
            selectors = [
                "section:has-text('map')", "div:has-text('Map')",
                "section:has-text('nearby')", "section:has-text('restaurants')",
                "section:has-text('things to do')", "div:has-text('event')",
                "section:has-text('places')", "div[class*='venue']",
                "div[class*='event']", "div[class*='activity']",
                "section[class*='listing']", "div[class*='explore']",
            ]
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.screenshot(path=stitched_path, type="png")
                        print(f"✅ [DEBUG] Focused region captured ({sel}): {stitched_path}")
                        break
                except Exception as e:
                    print(f"⚠️ [DEBUG] Selector {sel} failed: {e}")

            # Fallback to full-page
            if not os.path.exists(stitched_path):
                await page.screenshot(path=stitched_path, full_page=True, type="png")
                print(f"✅ [DEBUG] Full-page screenshot saved: {stitched_path}")

            # ✨ Enhance clarity
            try:
                img = Image.open(stitched_path)
                img = ImageEnhance.Contrast(img).enhance(1.25)
                img = ImageEnhance.Sharpness(img).enhance(1.8)
                img.save(stitched_path)
                print("🎨 [DEBUG] Enhanced screenshot for readability.")
            except Exception as e:
                print(f"⚠️ [DEBUG] Pillow enhancement failed: {e}")

            return stitched_path

        except Exception as e:
            print(f"⚠️ [WARN] Screenshot failed for {page.url}: {e}")
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
            await page.wait_for_timeout(4000)

            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(1000)

            if capture:
                base_dir = "/Users/shrey24/Desktop/mira_screens/planner"
                os.makedirs(base_dir, exist_ok=True)
                base_path = os.path.join(base_dir, f"planner_{int(time())}")
                screenshot_path = await self._scroll_and_capture_full_page(page, base_path)

            html = await page.content()
            print(f"✅ [DEBUG] Scraped successfully: {url}")
            return html or "", screenshot_path
        except Exception as e:
            logger.log_error(e, context=f"PlannerAgent._scrape_with_playwright ({url})")
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
            logger.log_error(e, context="PlannerAgent._scrape_page")
            return "", None
        
    # ======================================================
    # 🔹 Smart extract (PlannerAgent – lifestyle focused)
    # ======================================================
    async def smart_extract(self, query: str, url: str, stateful: bool = False) -> dict:
        """
        Scrape a single URL and extract meaningful lifestyle info:
        - restaurants, trails, events, trending spots, weekend activities.
        Returns a cleaned text summary and optional screenshot.
        """
        intent = domain_trust.intent_from_query(query)
        html, screenshot_path = await self._scrape_page(url, stateful=stateful, intent=intent, capture=True)
        if not html or len(html) < 1000:
            return {"text": "", "screenshot_path": screenshot_path}

        try:
            soup = BeautifulSoup(html, "html.parser")
            title = (soup.title.string.strip() if soup.title else "").strip()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

            # --- intent-specific logic ---
            if intent in ("outdoors", "activities", "weekend", "trending"):
                # Focus on descriptive paragraphs or list items
                paras = [
                    p.get_text(" ", strip=True)
                    for p in soup.find_all(["p", "li"])
                    if len(p.get_text(strip=True)) > 50
                ][:6]
                extracted = " ".join(paras) if paras else text[:800]

            elif intent in ("restaurant", "food", "dining"):
                # Look for content describing dishes, ambiance, or recommendations
                paras = [
                    p.get_text(" ", strip=True)
                    for p in soup.find_all(["p", "span", "li"])
                    if any(k in p.get_text().lower() for k in ("menu", "dish", "cuisine", "restaurant", "chef", "eat", "food"))
                ][:6]
                extracted = " ".join(paras) if paras else text[:800]

            else:
                # General fallback (e.g., nightlife, art, events, etc.)
                paras = [
                    p.get_text(" ", strip=True)
                    for p in soup.find_all(["p", "li"])
                    if len(p.get_text(strip=True)) > 40
                ][:6]
                extracted = " ".join(paras) if paras else text[:700]

            # --- cleanup + normalization ---
            extracted = _clean_text(extracted)
            extracted = re.sub(r"\s{2,}", " ", extracted).strip()

            final_text = f"{title}: {extracted}" if title else extracted
            return {"text": final_text.strip(), "screenshot_path": screenshot_path}

        except Exception as e:
            logger.log_error(e, context="PlannerAgent.smart_extract.html_parse")
            return {"text": "", "screenshot_path": screenshot_path}

    # ======================================================
    # 🔹 Search & concurrent gather
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
    # 🔹 Multi-vision summarization (GPT-4o, deep fusion)
    # ======================================================
    async def _multi_vision_summarize(self, query: str, site_results: list[dict]) -> str:
        """Fuse multi-source extracts + screenshots into a concise, natural summary."""
        if not self.llm_facts:
            return "LLM unavailable."

        # --- Local cleaner for snippets ---
        def _clean_snippet(text: str) -> str:
            text = re.sub(r"[*•\-\n\r\t]+", " ", text)
            text = re.sub(r"\s{2,}", " ", text)
            return text.strip()

        # --- Persona & summarization intent ---
        system_prompt = (
        "You are Mira, a warm but precise city-guide assistant.\n"
        "- Use only the information visible in the provided extracts and screenshots — do not rely on prior or external knowledge.\n"
        "- Combine facts from all extracts naturally to describe what’s happening or trending in the area.\n"
        "- Highlight specific experiences mentioned — such as restaurants, events, outdoor spots, exhibits, or activities — but don’t invent new ones.\n"
        "- If something isn’t shown or stated, do not speculate or add assumptions.\n"
        "- Speak conversationally, as if recommending to a friend — friendly but factual, no markdown or links.\n"
        "- Mention source names casually if they’re clear (e.g., 'Timeout suggests...', 'Yelp highlights...').\n"
        "- Keep it concise — 3–6 sentences max, grounded, vivid, and human."
    )


        messages = [SystemMessage(content=system_prompt)]

        # --- Consolidate all text + screenshots into one unified HumanMessage ---
        consolidated_content = [
            {"type": "text", "text": f"User asked: {query}\nHere are extracts and screenshots from multiple city sources:"}
        ]

        for s in site_results:
            try:
                clean_text = _clean_snippet(s.get("text", "")) or "(no readable text)"
                url = s.get("url", "")
                domain = url.split("/")[2] if "://" in url else url

                # handle screenshot path normalization
                ss_path = s.get("screenshot") or s.get("screenshot_path")
                if ss_path and os.path.exists(ss_path):
                    try:
                        with open(ss_path, "rb") as f:
                            img_b64 = base64.b64encode(f.read()).decode("utf-8")
                        consolidated_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"}
                        })
                    except Exception as e:
                        logger.log_error(e, context=f"PlannerAgent._multi_vision_summarize.imgload {domain}")

                consolidated_content.append({
                    "type": "text",
                    "text": f"Extracted content from {domain}:\n{clean_text}\n"
                })
            except Exception as e:
                logger.log_error(e, context=f"PlannerAgent._multi_vision_summarize.cleanloop {s.get('url')}")

        messages.append(HumanMessage(content=consolidated_content))

        # --- Invoke LLM and post-clean output ---
        try:
            resp = self.llm_facts.invoke(messages)
            text = (getattr(resp, "content", None) or str(resp)).strip()

            # cleanup pipeline
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"[\*\•\_\#\-\=\~\>\|`]+", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[\u200B-\u200D\uFEFF\u2022\u2023\u25AA\u25CF]", " ", text)
            text = re.sub(r"\s{2,}", " ", text).strip()

            # return concise, readable output
            return text.encode("ascii", "ignore").decode()[:900]

        except Exception as e:
            logger.log_error(e, context="PlannerAgent._multi_vision_summarize.invoke_fail")
            return "I couldn’t process the city insights right now."


    # ======================================================
    # 🔹 City / Intent-aware Discoverer (uses smart_extract)
    # ======================================================
    async def discover(self, text: str):
        """Main smart discovery — works for 'near me', 'in <city>', or general queries."""
        intent = domain_trust.intent_from_query(text)
        city = domain_trust.extract_city(text) or "New York"
        expanded = domain_trust.expand_query(intent)
        query = f"{text} {expanded} {city}"

        print(f"[PlannerAgent] Intent={intent} | City={city}")
        print(f"[PlannerAgent] Search Query={query}")

        # Step 1️⃣: Run meta search
        search_results = await self.search(query)
        urls = [r["url"] for r in search_results.get("links", [])]

        # Step 2️⃣: Extract each site concurrently using smart_extract
        sem = asyncio.Semaphore(2)

        async def fetch(url):
            async with sem:
                try:
                    result = await self.smart_extract(query, url)
                    if result and result.get("text"):
                        return {"url": url, **result}
                except Exception as e:
                    logger.log_error(e, context=f"PlannerAgent.discover.fetch {url}")
                return None

        gathered = await asyncio.gather(*[fetch(u) for u in urls])
        data = [g for g in gathered if g]

        # Step 3️⃣: Summarize with GPT-4o (text + screenshots)
        summary = await self._multi_vision_summarize(query, data)

        # Step 4️⃣: Cleanup — delete all screenshots after summarization
        for d in data:
            try:
                if d.get("screenshot_path") and os.path.exists(d["screenshot_path"]):
                    os.remove(d["screenshot_path"])
                    print(f"🧹 [DEBUG] Deleted screenshot: {d['screenshot_path']}")
            except Exception as e:
                logger.log_error(e, context="PlannerAgent.discover.cleanup")

        # Step 5️⃣: Return final structured output
        return {
            "intent": intent,
            "city": city,
            "summary": summary
        }

    # ======================================================
    # 🔹 Unified handler
    # ======================================================
    async def handle(self, payload: dict):
        fn = payload.get("fn")
        text = payload.get("text", "")
        if fn in ("discover", "weekend", "explore"):
            return {"ok": True, **(await self.discover(text))}
        return {"ok": False, "error": f"Unknown fn: {fn}"}
