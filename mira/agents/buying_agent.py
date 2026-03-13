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
import os
from PIL import Image, ImageEnhance
from mira.core.domain_trust import TRUSTED_SHOPS, TRUSTED_TECH_NEWS
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
    # 🔹 Improved Screenshot Capture (GPT-4o + Domain Trust)
    # ======================================================
    async def _scroll_and_capture_full_page(self, page, base_path: str) -> str:
        """
        Captures a high-resolution, contrast-enhanced screenshot optimized for GPT-4o Vision.
        • Uses TRUSTED_SHOPS / TRUSTED_TECH from domain_trust instead of hard-coded domains.
        • Handles reload-heavy or ad-injected e-commerce pages gracefully.
        • Scrolls through lazy-loaded sections and enhances readability for OCR.
        """
        stitched_path = f"{base_path}_stitched.png"
        try:
            domain = page.url.split("/")[2] if "://" in page.url else page.url

            # ======================================================
            # ⚙️ 1️⃣ Stabilize reload-heavy / commerce-heavy domains
            # ======================================================
            reload_heavy = set(TRUSTED_SHOPS.keys()) | set(TRUSTED_TECH_NEWS.keys())

            if any(d.split(".")[0] in domain for d in reload_heavy):
                print(f"⚙️ [DEBUG] Stabilizing reload-prone / e-commerce page: {domain}")
                try:
                    await page.wait_for_timeout(3000)
                    await page.evaluate("window.stop();")
                    await page.wait_for_timeout(700)
                except Exception:
                    pass

            # ======================================================
            # 🖱️ 2️⃣ Smooth scroll to trigger lazy loads
            # ======================================================
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

            # ======================================================
            # 🔍 3️⃣ Adaptive Zoom (domain trust–based)
            # ======================================================
            trust_score = max(
                TRUSTED_SHOPS.get(domain, 0),
                TRUSTED_TECH_NEWS.get(domain, 0)
            )
            zoom_factor = 1.25 if trust_score >= 5 else 1.15
            try:
                await page.evaluate(f"""
                    (function() {{
                        const z = {zoom_factor};
                        document.body.style.zoom = z;
                        document.body.style.transform = `scale(${zoom_factor})`;
                        document.body.style.transformOrigin = '0 0';
                        document.documentElement.style.scrollBehavior = 'auto';
                        window.scrollTo(0, 0);
                    }})();
                """)
                await page.wait_for_timeout(500)
                print(f"🔍 [DEBUG] Applied zoom factor {zoom_factor}")
            except Exception as e:
                print(f"⚠️ [DEBUG] Zoom adjustment failed: {e}")

            # ======================================================
            # 🧩 4️⃣ Focused capture (deal / price / review sections)
            # ======================================================
            print(f"🔍 [DEBUG] Attempting focused capture for {domain}")
            
            focus_selectors = [
            # --- Deal / Price keywords ---
            "section:has-text('deal')", "section:has-text('deals')",
            "section:has-text('price')", "section:has-text('discount')",
            "section:has-text('offer')", "section:has-text('save')",
            "div:has-text('Deal')", "div:has-text('Deals')",
            "div:has-text('Price')", "div:has-text('Discount')",
            "div:has-text('Offer')", "div:has-text('Savings')",
            "div[class*='deal']", "div[class*='deals']",
            "div[class*='discount']", "div[class*='offer']",
            "div[class*='price']", "div[id*='price']",

            # --- E-commerce product / listing containers ---
            "div#centerCol", "div#ppd", "div[data-component-type='s-search-result']",
            "div[class*='product']", "div[class*='grid']", "div[class*='tile']",
            "div[class*='sku']", "div[class*='item']", "div[class*='listing']",
            "div[class*='buybox']", "div[class*='card']", "li[class*='result']",

            # --- Editorial / Recommendations ---
            "section:has-text('Recommended')", "section:has-text('Top Picks')",
            "section:has-text('Best')", "section:has-text('Editor')",
            "section:has-text('Staff Pick')", "section:has-text('Our Choice')",
            "section[class*='list']", "article[class*='recommendation']",

            # --- Specs / Comparison tables ---
            "table[class*='spec']", "table[class*='comparison']",
            "div[class*='spec']", "section[class*='spec']",
        ]

            for sel in focus_selectors:
                try:
                    element = await page.query_selector(sel)
                    if element:
                        await element.screenshot(path=stitched_path, type="png")
                        print(f"✅ [DEBUG] Focused region captured ({sel}): {stitched_path}")
                        break
                except Exception as e:
                    print(f"⚠️ [DEBUG] Selector {sel} failed: {e}")

            # ======================================================
            # 🖼️ 5️⃣ Fallback full-page capture
            # ======================================================
            if not os.path.exists(stitched_path):
                await page.screenshot(path=stitched_path, full_page=True, type="png")
                print(f"✅ [DEBUG] Full-page screenshot saved: {stitched_path}")

            # ======================================================
            # ✨ 6️⃣ Enhance sharpness + contrast for OCR clarity
            # ======================================================
            try:
                img = Image.open(stitched_path)
                img = ImageEnhance.Contrast(img).enhance(1.25)
                img = ImageEnhance.Sharpness(img).enhance(1.8)
                img.save(stitched_path)
                print("🎨 [DEBUG] Enhanced screenshot for OCR clarity.")
            except Exception as e:
                print(f"⚠️ [DEBUG] Pillow enhancement failed: {e}")

            return stitched_path

        except Exception as e:
            print(f"⚠️ [WARN] Screenshot failed for {page.url}: {e}")
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
            # 🧠 1. Create and navigate page safely
            page = await self._pw_context.new_page()
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")

            # ⏳ 2. Light buffer for dynamic load completion
            await page.wait_for_timeout(3000)

            # 🖱️ 3. Initial light scroll (triggers JS/lazy-loads before capture)
            for _ in range(3):
                try:
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await page.wait_for_timeout(800)
                except Exception:
                    break  # If page navigates mid-scroll, skip remaining scrolls

            # 📸 4. Full-page screenshot if capture requested
            if capture:
                base_dir = "/Users/shrey24/Desktop/mira_screens/buying"
                os.makedirs(base_dir, exist_ok=True)
                base_path = os.path.join(base_dir, f"buying_{int(time())}")
                screenshot_path = await self._scroll_and_capture_full_page(page, base_path)

            # 🧾 5. Extract HTML
            html = await page.content()
            print(f"✅ [DEBUG] Scraped successfully: {url}")
            return html or "", screenshot_path

        except Exception as e:
            logger.log_error(e, context=f"BuyingAgent._scrape_with_playwright ({url})")
            return "", None

        finally:
            # 🧹 6. Always close the tab cleanly
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
    # 🔹 Variant-Aware Product Filter (Generic)
    # ======================================================
    def _extract_tokens(self, s: str) -> list[str]:
        """Normalize and split string into clean alphanumeric tokens."""
        s = re.sub(r"[^a-zA-Z0-9]+", " ", s.lower())
        return [t for t in s.split() if len(t) > 1]

    def _semantic_overlap(self, a: list[str], b: list[str]) -> float:
        """Compute semantic similarity between token sets."""
        return len(set(a) & set(b)) / max(1, len(set(a) | set(b)))

    def _variant_tokens(self, query_tokens: list[str]) -> set[str]:
        """
        Infer differentiator / model-variant tokens from the query itself.
        Works for all product types — e.g.:
        'm4 macbook pro' → {'m4', 'pro'}
        'rtx 4070 ti super' → {'4070', 'ti', 'super'}
        'air jordan 4 retro' → {'4', 'retro'}
        """
        variant_pattern = r"\d|pro|max|ultra|plus|mini|super|ti|edition|gen|series|model|mark|ver|pack|set|volume|kit"
        return {t for t in query_tokens if re.search(variant_pattern, t)}

    def _is_variant_conflict(self, query: str, text: str) -> bool:
        """
        Check whether a result text likely refers to a conflicting variant/model.
        e.g. query='m4 macbook pro' → drop 'm4 pro macbook pro'
        """
        q_toks = self._extract_tokens(query)
        x_toks = self._extract_tokens(text)
        query_variants = self._variant_tokens(q_toks)
        if not query_variants:
            return False

        for tok in x_toks:
            for qv in query_variants:
                # Disallow variant prefixes/suffixes that alter the base model
                if tok.startswith(qv) and tok != qv:
                    return True
        return False

    def _filter_model_relevance(self, query: str, site_results: list[dict]) -> list[dict]:
        """
        Filter site results semantically — keep only those matching the user’s
        model and variant intent. Generic across all product categories.
        """
        q_toks = self._extract_tokens(query)
        kept = []
        for s in site_results:
            snippet = (s.get("text", "") + " " + s.get("url", "")).lower()

            # skip obviously irrelevant content
            if self._semantic_overlap(q_toks, self._extract_tokens(snippet)) < 0.4:
                continue

            # skip conflicting variant models (e.g. M4 Pro vs M4 base)
            if self._is_variant_conflict(query, snippet):
                continue

            kept.append(s)

        if not kept:
            print("⚠️ [DEBUG] No strict matches found; using all results as fallback.")
            kept = site_results

        return kept


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
        Summarize and fuse multi-source product insights (including prices, specs, and rankings).
        Prioritizes accurate price detection using both text and screenshots.
        Uses GPT-4o for multimodal synthesis.
        """
        if not self.llm_facts:
            return "LLM unavailable."
        
        # 🧩 Filter out irrelevant or conflicting model variants before summarization
        site_results = self._filter_model_relevance(query, site_results)

        def _clean_snippet(text: str) -> str:
            text = re.sub(r"[*•\-\n\t\r]+", " ", text)
            text = re.sub(r"https?://\S+", "", text)
            #remove 'Save $200' / 'Discount $61' / '$300 off'
            text = re.sub(r"\b(save|discount|off|savings|reduced)\s*\$?\s?\d+[0-9,]*", "", text, flags=re.I)
            text = re.sub(r"\s{2,}", " ", text)
            return text.strip()
        
        # --- Enhanced system prompt ---
        system_prompt = (
        "You are Mira, a sharp and insightful product analyst.\n"
        "- Base every statement strictly on the provided extracts and screenshots — do not rely on prior or external knowledge.\n"
        "- Identify each product and extract the *final selling price* currently visible.\n"
        "- Ignore text like 'Save $200', 'Discount $50', or 'Deal price' — those are reductions, not the actual price.\n"
        "- Prioritize what you see in screenshots for price accuracy, and read nearby text for reviewer sentiment or short evaluations.\n"
        "- Summarize not just prices, but also *why* these products are praised or criticized — mention aspects like design, performance, battery life, portability, value, or ecosystem benefits when clearly mentioned.\n"
        "- If multiple models appear (e.g., M4 vs M5 MacBook), highlight the differences or improvements reviewers emphasize — never speculate.\n"
        "- Mention trustworthy sources naturally (e.g., 'CNET highlights...', 'Amazon reviewers praise...').\n"
        "- Stay objective and concise — about 4–6 compact sentences, no markdown, no lists, no filler."
    )



        messages = [SystemMessage(content=system_prompt)]
        consolidated_content = [
            {"type": "text", "text": f"User asked: {query}\nHere are extracts and screenshots from product/review sites:"}
        ]

        for s in site_results:
            try:
                clean_text = _clean_snippet(s.get("text", "")) or "(no readable text)"
                url = s.get("url", "")
                domain = url.split("/")[2] if "://" in url else url

                # 🧩 Extract numeric price snippets (case-insensitive for $, US$, USD)
                price_snippet = re.findall(r"(?:US?\$|USD\s*)[0-9][0-9,]*(?:\.\d{2})?", clean_text, flags=re.I)
                if price_snippet:
                    consolidated_content.append({
                        "type": "text",
                        "text": f"Detected visible prices on {domain}: {', '.join(price_snippet)}"
                    })

                # 🖼️ Include screenshot (with visual price guidance)
                ss_path = s.get("screenshot_path")
                if ss_path and os.path.exists(ss_path):
                    try:
                        with open(ss_path, "rb") as f:
                            img_b64 = base64.b64encode(f.read()).decode("utf-8")

                        # If no prices found in text, guide model to inspect screenshot visually
                        if not price_snippet:
                            consolidated_content.append({
                                "type": "text",
                                "text": f"No explicit price text detected on {domain}, but check screenshot visually for price tags."
                            })

                        consolidated_content.append({
                            "type": "text",
                            "text": f"Screenshot from {domain} — visually identify laptop names and price tags. "
                                    "If the screenshot and text disagree, trust the image for the price."
                        })
                        consolidated_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"}
                        })
                    except FileNotFoundError:
                        print(f"⚠️ [DEBUG] Screenshot missing at {ss_path}, skipping image for {domain}")
                    except Exception as e:
                        logger.log_error(e, context=f"BuyingAgent._multi_vision_summarize.imgload {domain}")

                # 📋 Include text content
                consolidated_content.append({
                    "type": "text",
                    "text": f"Extracted from {domain}:\n{clean_text}\n"
                })

            except Exception as e:
                logger.log_error(e, context=f"BuyingAgent._multi_vision_summarize.cleanloop {s.get('url')}")

        messages.append(HumanMessage(content=consolidated_content))

        # --- GPT-4o Summarization ---
        try:
            resp = self.llm_facts.invoke(messages)
            text = (resp.content or "").strip()

            # Clean residual formatting artifacts
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"[\*\•\_\#\-\=\~\>\|`]+", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[\u200B-\u200D\uFEFF\u2022\u2023\u25AA\u25CF]", " ", text)
            text = re.sub(r"\s{2,}", " ", text).strip()

            return text.encode("ascii", "ignore").decode()[:900]

        except Exception as e:
            logger.log_error(e, context="BuyingAgent._multi_vision_summarize.invoke_fail")
            return "I couldn’t summarize the pricing details right now."



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
