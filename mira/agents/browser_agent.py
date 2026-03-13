# ======================================
# mira/agents/browser_agent.py  (enhanced full-page + persistent Playwright reuse)
# ======================================
import asyncio, re, os, base64, zlib, inflect, requests
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
from mira.core.config import cfg
from mira.utils import logger
from mira.core import domain_trust
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
# ======================================================
# 🔹 Scroll + capture
# ======================================================
from mira.core.domain_trust import TRUSTED_SPORTS, TRUSTED_NEWS, host
import shutil
_engine = inflect.engine()

# ---------------- Helpers ----------------
def _normalize_scores(text: str) -> str:
    if not text:
        return text
    def repl(m):
        n1, n2 = int(m.group(1)), int(m.group(2))
        return f"{_engine.number_to_words(n1)} to {_engine.number_to_words(n2)}"
    pattern = re.compile(r"\b(\d{1,3})[,–-](\d{1,3})\b(?!\s*(?:yards?|pts?|reb|ast|blk|mins?|turnovers?))", re.I)
    return pattern.sub(repl, text)

# ======================================================
# 🔹 GLOBALS
# ======================================================
PROFILE_DIR = "/Users/shrey24/Desktop/mira-browser-profiles/browser_agent"
PLAYWRIGHT_LOCK = asyncio.Lock()

# ======================================================
# 🔹 BrowserAgent (generic research / news / sports)
# ======================================================
class BrowserAgent:
    def __init__(self, headless: bool = False, max_tabs: int = 5):
        self.headless = headless
        self.max_tabs = max_tabs
        self.browser_session: Optional[BrowserSession] = None
        self._started = False
        self._pw = None
        self._pw_context = None

        try:
            self.llm_facts = ChatOpenAI(model="gpt-4o", temperature=0.0, max_tokens=800)
            print("✅ [DEBUG] LLM initialized for BrowserAgent.")
        except Exception as e:
            logger.log_error(e, context="BrowserAgent.llm_init")
            self.llm_facts = None

    # ======================================================
    # 🔹 Safe Playwright management (match Buying/Planner)
    # ======================================================
    async def _ensure_playwright(self):
        async with PLAYWRIGHT_LOCK:
            # 🧠 Close BrowserUse first if active
            if self._started:
                print("🕐 [BrowserAgent] Waiting for BrowserUse to close before Playwright starts...")
                try:
                    await self.browser_session.stop()
                except Exception:
                    pass
                self._started = False

            # ✅ Remove stale lock if present
            lock_file = os.path.join(PROFILE_DIR, "SingletonLock")
            if os.path.exists(lock_file):
                print("🧹 [BrowserAgent] Removing stale Chrome lock...")
                try:
                    os.remove(lock_file)
                except Exception:
                    pass

            # ♻️ Reuse context if already launched
            if self._pw_context:
                print("♻️ [BrowserAgent] Reusing existing Playwright context.")
                return

            try:
                # 🚀 Launch persistent Chromium with visible window (not headless)
                self._pw = await async_playwright().start()
                self._pw_context = await self._pw.chromium.launch_persistent_context(
                    user_data_dir=PROFILE_DIR,
                    headless=False,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--start-maximized",
                        "--disable-dev-shm-usage",
                    ],
                )
                print(f"✅ [DEBUG] Playwright context launched using shared profile: {PROFILE_DIR}")

            except Exception as e:
                # 🧩 Handle locked profile (rare crash recovery)
                if "ProcessSingleton" in str(e):
                    print("⚠️ [BrowserAgent] Chrome profile locked — cleaning and retrying...")
                    shutil.rmtree(PROFILE_DIR, ignore_errors=True)
                    self._pw_context = await self._pw.chromium.launch_persistent_context(
                        user_data_dir=PROFILE_DIR,
                        headless=False,
                        args=[
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-blink-features=AutomationControlled",
                            "--disable-infobars",
                            "--start-maximized",
                            "--disable-dev-shm-usage",
                        ],
                    )
                    print("✅ [DEBUG] Relaunched clean persistent Playwright context.")
                else:
                    logger.log_error(e, context="BrowserAgent._ensure_playwright")

    async def close_playwright(self):
        if self._pw_context:
            await self._pw_context.close()
            self._pw_context = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        print("🧹 [DEBUG] Closed Playwright context for BrowserAgent.")

    # ======================================================
    # 🔹 BrowserUse (shared cookies)
    # ======================================================
    async def _ensure_browser_use(self):
        if not self.browser_session or not self._started:
            self.browser_session = BrowserSession(
                headless=self.headless,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116 Safari/537.36"),
                user_data_dir=PROFILE_DIR,
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
            logger.log_error(e, context="BrowserAgent.browser_use_get")
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
            logger.log_error(e, context="BrowserAgent._scrape_with_requests")
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
            logger.log_error(e, context="BrowserAgent._scrape_with_scrapy")
            return ""

    # ======================================================
    # 🔹 Scroll + capture
    # ======================================================
    from mira.core.domain_trust import TRUSTED_SPORTS, TRUSTED_NEWS, host

    # ======================================================
    # 🔹 Scroll + capture (for news/sports)
    # ======================================================
    async def _scroll_and_capture_full_page(self, page, base_path: str) -> str:
        """
        Captures a clean, legible screenshot optimized for GPT-4o vision.
        Tailored for sports and news pages — e.g., ESPN, Reuters, Bloomberg, BBC.
        - Stabilizes reload-heavy pages
        - Focuses on main article or scoreboard region if found
        - Falls back to full-page screenshot for long reads
        """
        stitched_path = f"{base_path}_stitched.png"
        try:
            domain = host(page.url)

            # ======================================================
            # ⚙️ 1️⃣ Stabilize reload-heavy / dynamic domains
            # ======================================================
            reload_heavy = {
                d for d in TRUSTED_SPORTS.keys() | TRUSTED_NEWS.keys()
                if any(k in d for k in ("espn", "reuters", "bloomberg", "bbc", "wsj", "cbssports", "foxsports"))
            }
            if any(d in domain for d in reload_heavy):
                print(f"⚙️ [DEBUG] Stabilizing reload-prone page: {domain}")
                try:
                    await page.wait_for_timeout(3500)
                    await page.evaluate("window.stop();")
                    await page.wait_for_timeout(600)
                except Exception:
                    pass

            # ======================================================
            # 🖱️ 2️⃣ Smooth scroll for lazy-loaded articles
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
            await page.wait_for_timeout(800)
            await page.emulate_media(media="screen")
            await page.set_viewport_size({"width": 1400, "height": 2200})

            # ======================================================
            # 📰 3️⃣ Focused region capture (article body or scoreboard)
            # ======================================================
            print(f"🔍 [DEBUG] Attempting focused capture for {domain}")
            selectors = [
                "main article",                 # core article element
                "div.article-body",             # typical newsroom markup
                "div.story-body",               # BBC style
                "section.article-content",
                "div.live-update",              # live news feeds
                "div.game-details",             # ESPN, CBS Sports
                "section.scoreboard",           # scoreboard
                "table.stats",                  # match stats tables
                "div.match-summary",            # recap summary
            ]
            for sel in selectors:
                try:
                    element = await page.query_selector(sel)
                    if element:
                        await element.screenshot(path=stitched_path, type="png")
                        print(f"✅ [DEBUG] Focused article/scoreboard captured ({sel}): {stitched_path}")
                        return stitched_path
                except Exception as e:
                    print(f"⚠️ [DEBUG] Selector {sel} failed: {e}")

            # ======================================================
            # 🖼️ 4️⃣ Fallback: Full-page capture
            # ======================================================
            await page.screenshot(path=stitched_path, full_page=True, type="png")
            print(f"✅ [DEBUG] Full-page screenshot saved: {stitched_path}")
            return stitched_path

        except Exception as e:
            print(f"⚠️ [WARN] Screenshot failed for {page.url}: {e}")
            logger.log_error(e, context="BrowserAgent._scroll_and_capture_full_page")
            return ""


# ======================================================
# 🔹 Playwright scraper (for news/sports)
# ======================================================
    async def _scrape_with_playwright(self, url: str, capture: bool = False):
        await self._ensure_playwright()
        screenshot_path = None
        page = None

        try:
            # 🧠 1️⃣ Navigate safely
            page = await self._pw_context.new_page()
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")

            # ⏳ 2️⃣ Allow scripts and ads to settle
            await page.wait_for_timeout(3000)

            # 🖱️ 3️⃣ Light scroll to warm up dynamic DOM
            for _ in range(3):
                try:
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await page.wait_for_timeout(800)
                except Exception:
                    break

            # 📸 4️⃣ Capture screenshot if requested
            if capture:
                base_dir = "/Users/shrey24/Desktop/mira_screens/browser_agent"
                os.makedirs(base_dir, exist_ok=True)
                base_path = os.path.join(base_dir, f"browser_{int(time())}")
                screenshot_path = await self._scroll_and_capture_full_page(page, base_path)

            # 🧾 5️⃣ Extract HTML
            html = await page.content()
            print(f"✅ [DEBUG] Scraped successfully: {url}")
            return html or "", screenshot_path

        except Exception as e:
            logger.log_error(e, context=f"BrowserAgent._scrape_with_playwright ({url})")
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
    async def _scrape_page(self, url: str, stateful: bool = False, capture: bool = False):
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
            logger.log_error(e, context="BrowserAgent._scrape_page")
            return "", None

    # ======================================================
    # 🔹 Smart extract (news / sports / general)
    # ======================================================
    async def smart_extract(self, query: str, url: str, stateful: bool = False) -> dict:
        intent = domain_trust.intent_from_query(query)
        html, screenshot_path = await self._scrape_page(url, stateful=stateful, capture=True)
        if not html or len(html) < 1000:
            return {"text": "", "screenshot_path": screenshot_path}

        try:
            soup = BeautifulSoup(html, "html.parser")
            title = (soup.title.string.strip() if soup.title else "").strip()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

            # --- sports or news focused ---
            if intent in ("sports", "game"):
                match = re.search(r"(\d{1,3})\s*[-–:]\s*(\d{1,3})", text)
                extracted = f"Final score: {match.group(1)} to {match.group(2)}." if match else text[:600]
            elif intent in ("news", "politics", "headlines"):
                paras = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 60][:5]
                extracted = " ".join(paras) if paras else text[:800]
            else:
                extracted = text[:800]

            final_text = f"{title}: {extracted}" if title else extracted
            return {"text": final_text.strip(), "screenshot_path": screenshot_path}
        except Exception as e:
            logger.log_error(e, context="BrowserAgent.smart_extract.html_parse")
            return {"text": "", "screenshot_path": screenshot_path}

    # ======================================================
    # 🔹 Search & summarization
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

    async def gather_sites_concurrently(self, query: str, urls: list[str]):
        sem = asyncio.Semaphore(2)
        async def fetch(url):
            async with sem:
                data = await self.smart_extract(query, url)
                return {"url": url, **data}
        return await asyncio.gather(*[fetch(u) for u in urls])

    # ======================================================
    # 🔹 GPT-4o Vision Summarizer (news / sports)
    # ======================================================
    async def _multi_vision_summarize(self, query: str, site_results: list[dict]) -> str:
        """
        Fuse multi-source text and screenshots from trusted news or sports sites.
        Prioritizes factual accuracy, verified scores, and event context.
        Uses GPT-4o for multimodal synthesis.
        """
        if not self.llm_facts:
            return "LLM unavailable."

        def _clean_snippet(text: str) -> str:
            text = re.sub(r"[*•\-\n\t\r]+", " ", text)
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"\s{2,}", " ", text)
            return text.strip()

        # --- Enhanced system prompt ---
        system_prompt = (
            "You are Mira, a calm, factual analyst specializing in news and sports.\n"
            "- You will receive extracts and screenshots from multiple reputable sites.\n"
            "- Identify verified scores, key outcomes, and headline details.\n"
            "- If a screenshot shows a scoreboard or headline not in the text, trust the screenshot.\n"
            "- Ignore ads, navigation bars, or speculative commentary.\n"
            "- Attribute insights naturally (e.g., 'Reuters reports...', 'ESPN shows...').\n"
            "- Be concise and neutral — 3–6 sentences, no markdown, no lists."
        )

        messages = [SystemMessage(content=system_prompt)]
        consolidated_content = [
            {
                "type": "text",
                "text": (
                    f"User asked: {query}\n"
                    "Here are extracts and screenshots from verified news and sports sources:"
                ),
            }
        ]

        for s in site_results:
            try:
                clean_text = _clean_snippet(s.get("text", "")) or "(no readable text)"
                url = s.get("url", "")
                domain = url.split("/")[2] if "://" in url else url

                # 🏟️ Detect possible numeric scores (e.g., 3–1, 102–98)
                score_snippet = re.findall(
                    r"\b\d{1,3}\s*[-–:]\s*\d{1,3}\b", clean_text, flags=re.I
                )
                if score_snippet:
                    consolidated_content.append({
                        "type": "text",
                        "text": f"Detected score patterns on {domain}: {', '.join(score_snippet)}"
                    })

                # 🖼️ Include screenshot (prioritize scoreboards or article regions)
                ss_path = s.get("screenshot_path")
                if ss_path and os.path.exists(ss_path):
                    try:
                        with open(ss_path, "rb") as f:
                            img_b64 = base64.b64encode(f.read()).decode("utf-8")

                        if not score_snippet:
                            consolidated_content.append({
                                "type": "text",
                                "text": (
                                    f"No explicit score found in text from {domain}, "
                                    "but visually inspect the screenshot for scoreboard or final score."
                                ),
                            })

                        consolidated_content.append({
                            "type": "text",
                            "text": (
                                f"Screenshot from {domain} — check for visible team names, "
                                "match results, or news headlines. "
                                "If screenshot and text differ, trust the image."
                            ),
                        })
                        consolidated_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"}
                        })
                    except FileNotFoundError:
                        print(f"⚠️ [DEBUG] Screenshot missing at {ss_path}, skipping {domain}")
                    except Exception as e:
                        logger.log_error(e, context=f"BrowserAgent._multi_vision_summarize.imgload {domain}")

                # 📋 Add textual extract
                consolidated_content.append({
                    "type": "text",
                    "text": f"Extracted from {domain}:\n{clean_text}\n"
                })

            except Exception as e:
                logger.log_error(e, context=f"BrowserAgent._multi_vision_summarize.cleanloop {s.get('url')}")

        messages.append(HumanMessage(content=consolidated_content))

        # --- GPT-4o Summarization ---
        try:
            resp = self.llm_facts.invoke(messages)
            text = (resp.content or "").strip()

            # 🧹 Clean any artifacts
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"[\*\•\_\#\-\=\~\>\|`]+", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[\u200B-\u200D\uFEFF\u2022\u2023\u25AA\u25CF]", " ", text)
            text = re.sub(r"\s{2,}", " ", text).strip()

            return text.encode("ascii", "ignore").decode()[:900]

        except Exception as e:
            logger.log_error(e, context="BrowserAgent._multi_vision_summarize.invoke_fail")
            return "I couldn’t summarize the article details right now."


    # ======================================================
    # 🔹 Trust-ranked link selector (used by discover)
    # ======================================================
    def _select_best_links(self, search_results, intent, query, top_k=5):
        target_date = domain_trust.resolve_target_date(query) or datetime.now().date()
        links = search_results.get("links", [])
        for link in links:
            title, url = link.get("title", ""), link.get("url", "")
            link["score"] = domain_trust.score_link(title, url, target_date, intent)
        links.sort(key=lambda x: x.get("score", 0), reverse=True)
        return links[:top_k]

    # ======================================================
    # 🔹 Discover (news / research / deals) — trust ranked
    # ======================================================
    async def discover(self, query: str):
        intent = domain_trust.intent_from_query(query)
        expanded = domain_trust.expand_query(intent)
        search_q = f"{query} {expanded}"

        print(f"[BrowserAgent] Intent={intent} | Query={search_q}")

        search_results = await self.search(search_q)
        chosen_links = self._select_best_links(search_results, intent, query, top_k=5)
        urls = [l["url"] for l in chosen_links]

        print(f"[BrowserAgent] Selected {len(urls)} trusted URLs for intent '{intent}'")

        if not urls:
            return {"intent": intent, "summary": "No trustworthy sources found.", "sources": []}

        site_results = await self.gather_sites_concurrently(query, urls)
        summary = await self._multi_vision_summarize(query, site_results)

        for s in site_results:
            try:
                if s.get("screenshot_path") and os.path.exists(s["screenshot_path"]):
                    os.remove(s["screenshot_path"])
                    print(f"🧹 [DEBUG] Deleted screenshot: {s['screenshot_path']}")
            except Exception as e:
                logger.log_error(e, context="BrowserAgent.discover.cleanup")

        return {"intent": intent, "summary": summary, "sources": urls}

    # ======================================================
    # 🔹 Unified handler 
    # ======================================================
    async def handle(self, payload: dict):
        fn = payload.get("fn")
        text = payload.get("text", "")

        if fn in ("discover", "headlines", "news", "research"):
            return {"ok": True, **(await self.discover(text))}

        return {"ok": False, "error": f"Unknown fn: {fn}"}
