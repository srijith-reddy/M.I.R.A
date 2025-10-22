# mira/agents/whatsapp_agent.py
from typing import Any, Dict
import json
import re
import time
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

CONTACTS_FILE = Path("contacts.json")


def resolve_contact(name: str) -> str | None:
    """Look up a contact in contacts.json (name → phone)."""
    try:
        if CONTACTS_FILE.exists():
            with open(CONTACTS_FILE) as f:
                contacts = json.load(f)
            return contacts.get(name.lower())
    except Exception as e:
        print(f"[WhatsAppAgent] Contact resolution failed: {e}")
    return None


def parse_whatsapp_command(text: str) -> Dict[str, Any]:
    """Parse WhatsApp-style commands into structured payloads."""
    text = (text or "").strip()

    # --- Pattern 1: "text mom good morning" / "message dad I'll be late"
    m = re.match(
        r"^(?:can you|could you|please)?\s*(?:text|message|msg)\s+([\w\s]+?)[,\.:\-]?\s+(.+)$",
        text,
        re.I,
    )
    if m:
        return {"fn": "send", "to": m.group(1).strip(), "body": m.group(2).strip()}

    # --- Pattern 2: "send whatsapp to John saying hello"
    m = re.match(
        r"^send\s+(?:a\s+)?(?:whatsapp\s+)?to\s+([\w\s]+?)(?:\s+saying\s+|\s+)(.+)$",
        text,
        re.I,
    )
    if m:
        return {"fn": "send", "to": m.group(1).strip(), "body": m.group(2).strip()}

    # --- Fallback with comma: "message mom, love you"
    if "," in text:
        parts = text.split(",", 1)
        return {
            "fn": "send",
            "to": re.sub(r"^(?:text|message|msg)\s+", "", parts[0], flags=re.I).strip(),
            "body": parts[1].strip(),
        }

    # --- Generic fallback: first word = recipient, rest = body
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        return {
            "fn": "send",
            "to": re.sub(r"^(?:text|message|msg)\s+", "", parts[0], flags=re.I).strip(),
            "body": parts[1].strip(),
        }

    # --- Last resort: treat all as body
    return {"fn": "send", "to": "", "body": text}



class WhatsAppAgent:
    def __init__(self, profile: str = "~/mira-browser-profiles/whatsapp", headless: bool = False, idle_timeout: int = 60):
        self.profile = profile
        self.headless = headless
        self.idle_timeout = idle_timeout  # seconds
        self.playwright = None
        self.context = None
        self.page = None
        self._last_used = 0
        self._idle_task = None

    async def _ensure_context(self):
        if not self.context:
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile,
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--window-size=1280,800",
                    "--window-position=-2000,0",  # off-screen instead of minimized
                ],
            )
            print("[WhatsAppAgent] Persistent context launched")

        if not self.page or self.page.is_closed():
            self.page = await self.context.new_page()
            print("[WhatsAppAgent] New page created")

        # update last used + kick off idle watcher
        self._last_used = time.time()
        if not self._idle_task:
            self._idle_task = asyncio.create_task(self._idle_watcher())

        return self.page

    async def _idle_watcher(self):
        while True:
            await asyncio.sleep(10)  # check every 10s
            if self._last_used and (time.time() - self._last_used) > self.idle_timeout:
                print("[WhatsAppAgent] Idle timeout reached — closing browser")
                await self.stop()
                break

    async def send(self, to: str, body: str) -> Dict[str, Any]:
        if not body:
            return {"ok": False, "error": "Message body is required."}

        page = await self._ensure_context()

        # --- Navigation ---
        if to:
            # Resolve contact → number if needed
            if not to.strip().isdigit():
                resolved = resolve_contact(to.strip())
                if resolved:
                    to = resolved
                else:
                    return {"ok": False, "error": f"Couldn’t find {to} in contacts.json"}
            to = to.strip().replace(" ", "").replace("-", "").lstrip("+")
            url = f"https://web.whatsapp.com/send?phone={to}"
            print(f"[DEBUG] Opening chat directly: {url}")
            await page.goto(url)
        else:
            await page.goto("https://web.whatsapp.com/")
            print("[DEBUG] Opened WhatsApp Web for search")

        # --- Wait for WhatsApp UI ---
        await page.wait_for_selector("div[contenteditable='true']", timeout=60000)
        print("[DEBUG] WhatsApp UI ready")

        if not to:
            # Extract name from first word
            parts = body.split(maxsplit=1)
            if len(parts) < 2:
                return {"ok": False, "error": "Couldn’t parse recipient from message."}
            contact_name, body = parts[0], parts[1]
            print(f"[DEBUG] Searching for contact: {contact_name}")

            search_box = await page.wait_for_selector(
                "header input[title='Search or start new chat']",
                timeout=10000,
            )
            await search_box.click()
            await search_box.fill(contact_name)
            await page.keyboard.press("Enter")

        # --- Input box (no conversation-panel wait) ---
        input_box = await page.wait_for_selector("footer div[contenteditable='true']", timeout=30000)
        print("[DEBUG] Input box located")
        await input_box.click()
        await page.wait_for_timeout(300)

        # --- Inject text with React-compatible events ---
        # --- Inject text with React-compatible events ---
        await input_box.evaluate(
            """
            (el, value) => {
                el.focus();
                el.textContent = value;  // overwrite instead of appending

                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    cancelable: true,
                    inputType: 'insertText',
                    data: value
                }));
            }
            """,
            body,
        )



        typed = await input_box.inner_text()
        print(f"[DEBUG] After injection, input contains: {typed!r}")

        # --- Send button or fallback Enter ---
        try:
            send_btn = await page.wait_for_selector("button[data-testid='compose-btn-send']", timeout=5000)
            await send_btn.click()
            print("[DEBUG] Clicked send button")
        except:
            await page.keyboard.press("Enter")
            print("[DEBUG] Fallback: pressed Enter")

        print(f"[WhatsAppAgent] ✅ Message sent to {to or 'searched contact'}: {body}")
        return {"ok": True, "to": to or "searched contact", "body": body}




    async def handle(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, str):
            payload = parse_whatsapp_command(payload)
        elif not isinstance(payload, dict):
            return {"ok": False, "error": "Expected dict payload"}

        fn = (payload.get("fn") or "").strip().lower()
        if not fn:
            fn = "send"
            payload["fn"] = fn

        print("[WhatsAppAgent] Handle received payload:", payload)

        if fn == "send":
            return await self.send(payload.get("to", ""), payload.get("body", ""))

        return {"ok": False, "error": f"Unknown fn: {fn}"}

    async def stop(self):
        """Stop persistent browser context cleanly."""
        try:
            if self.context:
                await self.context.close()
        except Exception as e:
            print("[WhatsAppAgent] Context close failed:", e)
        finally:
            self.context = None

        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            print("[WhatsAppAgent] Playwright stop failed:", e)
        finally:
            self.playwright = None

        self.page = None
        self._idle_task = None
