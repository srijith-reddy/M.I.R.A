# ======================================
# mira/agents/browser_agent.py  (enhanced full-page + persistent Playwright reuse)
# ======================================
import asyncio, re, os, base64, brotli, zlib, inflect, requests
from time import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from urllib.parse import quote
from bs4 import BeautifulSoup
from scrapy.http import HtmlResponse
from scrapy.selector import Selector
from playwright.async_api import async_playwright
from openai import OpenAI
from browser_use import BrowserSession
from mira.core.config import cfg
from mira.utils import logger
from mira.core import domain_trust
from PIL import Image
import aiofiles
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

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

# ======================================================================
# MAIN AGENT
# ======================================================================
class BrowserAgent:
    def __init__(self, headless: bool = False, max_tabs: int = 5):
        self.headless = headless
        self.max_tabs = max_tabs
        self.browser_session: Optional[BrowserSession] = None
        self._started = False

        # persistent Playwright vars
        self._pw = None
        self._pw_context = None

        try:
            self.llm_facts = ChatOpenAI(
                model="gpt-4o",
                temperature=0.0,
                max_tokens=800,
                streaming=False
            )
            print("✅ [DEBUG] ChatOpenAI (llm_facts) initialized for BrowserAgent.")
        except Exception as e:
            logger.log_error(e, context="BrowserAgent.llm_facts_init")
            self.llm_facts = None

    # ======================================================
    # 🔹 Playwright persistent context management
    # ======================================================
    async def _ensure_playwright(self):
        if self._pw_context:
            return
        self._pw = await async_playwright().start()
        self._pw_context = await self._pw.chromium.launch_persistent_context(
            user_data_dir="/tmp/mira_pw_shared",
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        print("✅ [DEBUG] Persistent Playwright context ready.")

    async def close_playwright(self):
        if self._pw_context:
            await self._pw_context.close()
            print("🧹 [DEBUG] Closed persistent Playwright context.")
            self._pw_context = None
        if self._pw:
            await self._pw.stop()
            print("🧹 [DEBUG] Stopped Playwright engine.")
            self._pw = None

    # ======================================================
    # 🔹 Browser-use session (optional)
    # ======================================================
    async def _ensure_browser_use(self):
        if not self.browser_session or not self._started:
            try:
                self.browser_session = BrowserSession(
                    headless=False,
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116 Safari/537.36")
                )
                await self.browser_session.start()
                self._started = True
                logger.log_info("Browser session initialized", context="BrowserAgent")

                try:
                    tabs = await self.browser_session.list_tabs()
                    for t in tabs:
                        current_url = (await self.browser_session.get_tab_url(t)) or ""
                        if current_url.strip() in ("", "about:blank"):
                            await self.browser_session.close_tab(t)
                            print("🧹 [DEBUG] Closed BrowserUse blank starter tab.")
                except Exception as te:
                    logger.log_error(te, context="BrowserAgent._ensure_browser_use.cleanup_tabs")
            except Exception as e:
                logger.log_error(e, context="BrowserAgent._ensure_browser_use")

    async def browser_use_get(self, url: str) -> str:
        await self._ensure_browser_use()
        try:
            await self.browser_session.navigate_to(url)
            html = await self.browser_session.get_current_page_url()
            title = await self.browser_session.get_current_page_title()
            return f"<title>{title}</title> {html}"
        except Exception as e:
            logger.log_error(e, context="BrowserAgent.browser_use_get (retry)")
            try:
                await self.browser_session.stop()
                self._started = False
                await self._ensure_browser_use()
                await self.browser_session.navigate_to(url)
                html = await self.browser_session.get_current_page_url()
                title = await self.browser_session.get_current_page_title()
                return f"<title>{title}</title> {html}"
            except Exception as e2:
                logger.log_error(e2, context="BrowserAgent.browser_use_get.final_fail")
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
            resp = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/116 Safari/537.36",
                    "Accept-Encoding": "gzip, deflate, br",
                },
                timeout=12,
            )
            if not resp.ok:
                return ""
            return self._decode_body(resp)
        except Exception as e:
            logger.log_error(e, context="BrowserAgent._scrape_with_requests")
            return ""

    def _scrape_with_scrapy(self, url: str) -> str:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/116 Safari/537.36",
                "Accept-Encoding": "gzip, deflate, br",
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

    # ======================================================
    # 🔹 Scroll + full-page capture
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
    # 🔹 Playwright scrape (persistent reuse)
    # ======================================================
    async def _scrape_with_playwright(self, url: str, intent: Optional[str] = None, capture: bool = False):
        """Scrape a page using a shared persistent Playwright context."""
        await self._ensure_playwright()  # ensures persistent browser exists
        screenshot_path = None
        page = None

        try:
            # 🧩 Create a new tab in the shared context
            page = await self._pw_context.new_page()
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")

            # 🕐 Wait depending on site type
            extra_wait = 10000 if intent in ("sports", "finance") else 6000
            await page.wait_for_timeout(extra_wait)

            # 📜 Scroll to trigger lazy loading
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(1000)

            # 📸 Capture full page if needed
            if capture:
                base_dir = "/Volumes/HDD-1/mira_screens"
                os.makedirs(base_dir, exist_ok=True)
                base_path = os.path.join(base_dir, f"mira_{int(time())}")
                screenshot_path = await self._scroll_and_capture_full_page(page, base_path)
                print(f"✅ [DEBUG] Full-page screenshot captured: {screenshot_path}")

            html = await page.content()
            print(f"✅ [DEBUG] Scraped successfully: {url}")
            return html or "", screenshot_path

        except Exception as e:
            logger.log_error(e, context=f"BrowserAgent._scrape_with_playwright ({url})")
            return "", None

        finally:
            # 🧹 Only close the tab, not the entire context
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

            html, screenshot_path = await self._scrape_with_playwright(url, intent=intent, capture=capture)
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
    # 🔹 Smart extract + summarization
    # ======================================================
    async def smart_extract(self, query: str, url: str, stateful: bool = False) -> dict:
        intent = domain_trust.intent_from_query(query)
        html, screenshot_path = await self._scrape_page(url, stateful=stateful, intent=intent, capture=True)
        if not html or len(html) < 1000:
            return {"text": "", "screenshot_path": screenshot_path}
        try:
            soup = BeautifulSoup(html, "html.parser")
            title = (soup.title.string.strip() if soup.title else "").strip()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            if intent == "sports":
                match = re.search(r"(\d{1,3})\s*[-–:]\s*(\d{1,3})", text)
                extracted = f"Final score: {match.group(1)} to {match.group(2)}." if match else text[:600]
            elif intent == "news":
                paras = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 60][:4]
                extracted = " ".join(paras) if paras else text[:600]
            else:
                extracted = text[:600]
            extracted = _normalize_scores(extracted)
            final_text = f"{title}: {extracted}".strip() if title else extracted.strip()
            return {"text": final_text, "screenshot_path": screenshot_path}
        except Exception as e:
            logger.log_error(e, context="BrowserAgent.smart_extract.html_parse")
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

    async def gather_sites_concurrently(self, query: str, urls: list[str]):
        sem = asyncio.Semaphore(2)
        async def fetch(url):
            async with sem:
                data = await self.smart_extract(query, url, stateful=True)
                return {"url": url, **data}
        return await asyncio.gather(*[fetch(u) for u in urls])

    # ======================================================
    # 🔹 Vision summarizer + orchestrator
    # ======================================================
    async def _multi_vision_summarize(self, query: str, site_results: list[dict]):
        def _clean_snippet(text: str) -> str:
            text = re.sub(r"[*•\-\n]+", " ", text)
            return re.sub(r"\s+", " ", text).strip()

        system_prompt = (
            "You are Mira, a warm but precise research assistant.\n"
            "- Repeat extracted facts exactly when present.\n"
            "- Focus on relevant info only.\n"
            "- Ignore ads or sign-ups.\n"
            "- Speak naturally in one or two sentences, no markdown.\n"
            "- Mention source casually if clear (e.g., 'CNBC reports...')."
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"User asked: {query}\nNow review all extracts and screenshots.")
        ]

        for s in site_results:
            try:
                clean_text = _clean_snippet(s["text"]) if s["text"] else "(no readable text)"
                if s.get("screenshot_path") and os.path.exists(s["screenshot_path"]):
                    with open(s["screenshot_path"], "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                    messages.append(
                        HumanMessage(content=[
                            {"type": "text", "text": f"Screenshot from {s['url']} — analyze for relevant text."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
                        ])
                    )
                domain = s["url"].split("/")[2] if "://" in s["url"] else s["url"]
                messages.append(HumanMessage(content=f"Extracted content from {domain}:\n{clean_text}"))
            except Exception as e:
                logger.log_error(e, context=f"BrowserAgent._multi_vision_summarize.cleanloop {s.get('url')}")

        try:
            resp = self.llm_facts.invoke(messages)
            text = (resp.content or "").strip()
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"[\*\•\_\#\-\=\~\>\|`]+", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[\u200B-\u200D\uFEFF\u2022\u2023\u25AA\u25CF]", " ", text)
            text = re.sub(r"\s{2,}", " ", text).strip()
            return text.encode("ascii", "ignore").decode()[:800]
        except Exception as e:
            logger.log_error(e, context="BrowserAgent._multi_vision_summarize.invoke_fail")
            return "I couldn’t process the screenshots right now."

    async def multi_site_answer(self, query: str, urls: List[str]) -> str:
        site_results = await self.gather_sites_concurrently(query, urls)
        summary = await self._multi_vision_summarize(query, site_results)
        for s in site_results:
            try:
                if s["screenshot_path"] and os.path.exists(s["screenshot_path"]):
                    os.remove(s["screenshot_path"])
            except Exception:
                pass
        # await self.close_playwright()
        return summary or "I couldn’t summarize these sources."
