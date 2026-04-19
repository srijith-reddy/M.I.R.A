"""Gmail tools backed by the Gmail REST API.

First-run requires `scripts/gmail_auth.py` to run once and drop a
`gmail_token.json` at paths.gmail_token. After that, access tokens
refresh automatically from the saved refresh token.

These tools register themselves only when the token file is present —
otherwise the communication agent falls through to the browser path for
email. Import is still safe without a token; a missing file just skips
registration and logs once so the reason is visible.
"""

from __future__ import annotations

import asyncio
import base64
import re
from email.message import EmailMessage
from typing import Any

from pydantic import BaseModel, Field

from mira.config.paths import paths
from mira.obs.logging import log_event
from mira.runtime.registry import tool


_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# The Gmail client is expensive to build (TLS + discovery doc fetch on
# first call) and fine to keep cached for the daemon's lifetime — the
# underlying credentials object handles its own token refresh.
_service: Any | None = None
_service_lock = asyncio.Lock()


async def _get_service() -> Any | None:
    global _service
    if _service is not None:
        return _service
    async with _service_lock:
        if _service is not None:
            return _service
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as e:
            log_event("gmail.import_error", error=repr(e))
            return None

        if not paths.gmail_token.exists():
            log_event("gmail.token_missing", path=str(paths.gmail_token))
            return None

        def _load() -> Any:
            creds = Credentials.from_authorized_user_file(
                str(paths.gmail_token), _SCOPES,
            )
            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    paths.gmail_token.write_text(creds.to_json())
                else:
                    return None
            # cache_discovery=False — the on-disk cache shape differs
            # between google-api-python-client versions and sometimes
            # warns loudly; we don't need it.
            return build("gmail", "v1", credentials=creds, cache_discovery=False)

        _service = await asyncio.to_thread(_load)
        return _service


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_body(payload: dict) -> str:
    """Best-effort plain-text body. Walks multipart, decodes base64url.
    Falls back to snippet if nothing useful is found."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}
    data = body.get("data")
    if mime == "text/plain" and data:
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        except Exception:
            return ""
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text:
            return text
    return ""


# ---- search ----------------------------------------------------------------

class SearchArgs(BaseModel):
    query: str = Field(
        default="",
        description=(
            "Gmail search query. Supports all Gmail operators: "
            "from:, to:, subject:, is:unread, newer_than:7d, has:attachment, "
            "label:starred. Empty string returns latest inbox messages."
        ),
    )
    max_results: int = Field(default=10, ge=1, le=50)


def _summarize_search(data: Any) -> str:
    if not isinstance(data, dict) or not data.get("ok"):
        return f"gmail error: {data.get('error') if isinstance(data, dict) else data}"
    items = data.get("messages") or []
    if not items:
        return "no matches."
    parts = [f"{len(items)} result{'s' if len(items) != 1 else ''}:"]
    for m in items[:5]:
        who = (m.get("from") or "").split("<")[0].strip() or "(unknown)"
        subj = m.get("subject") or "(no subject)"
        parts.append(f"{who} — {subj[:60]}")
    return " | ".join(parts)


async def _search_impl(args: SearchArgs) -> dict[str, Any]:
    svc = await _get_service()
    if svc is None:
        return {"ok": False, "error": "gmail not authorized. run scripts/gmail_auth.py"}

    def _run() -> dict[str, Any]:
        q = args.query or "in:inbox"
        resp = svc.users().messages().list(
            userId="me", q=q, maxResults=args.max_results,
        ).execute()
        ids = [m["id"] for m in resp.get("messages", []) or []]
        msgs: list[dict[str, Any]] = []
        for mid in ids:
            m = svc.users().messages().get(
                userId="me", id=mid,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            msgs.append({
                "id": mid,
                "thread_id": m.get("threadId"),
                "from": _header(m, "From"),
                "subject": _header(m, "Subject"),
                "date": _header(m, "Date"),
                "snippet": m.get("snippet", ""),
                "unread": "UNREAD" in (m.get("labelIds") or []),
            })
        return {"ok": True, "count": len(msgs), "messages": msgs}

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return {"ok": False, "error": repr(e)}


# ---- read ------------------------------------------------------------------

class ReadArgs(BaseModel):
    message_id: str = Field(..., description="Gmail message id from gmail.search.")


async def _read_impl(args: ReadArgs) -> dict[str, Any]:
    svc = await _get_service()
    if svc is None:
        return {"ok": False, "error": "gmail not authorized. run scripts/gmail_auth.py"}

    def _run() -> dict[str, Any]:
        m = svc.users().messages().get(
            userId="me", id=args.message_id, format="full",
        ).execute()
        body = _extract_body(m.get("payload") or {}) or m.get("snippet", "")
        return {
            "ok": True,
            "id": m.get("id"),
            "thread_id": m.get("threadId"),
            "from": _header(m, "From"),
            "to": _header(m, "To"),
            "subject": _header(m, "Subject"),
            "date": _header(m, "Date"),
            "body": body[:8000],
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return {"ok": False, "error": repr(e)}


# ---- unread count ----------------------------------------------------------

class UnreadArgs(BaseModel):
    pass


async def _unread_impl(_: UnreadArgs) -> dict[str, Any]:
    svc = await _get_service()
    if svc is None:
        return {"ok": False, "error": "gmail not authorized. run scripts/gmail_auth.py"}

    def _run() -> dict[str, Any]:
        res = svc.users().labels().get(userId="me", id="INBOX").execute()
        return {
            "ok": True,
            "unread": int(res.get("messagesUnread", 0)),
            "total": int(res.get("messagesTotal", 0)),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return {"ok": False, "error": repr(e)}


# ---- send ------------------------------------------------------------------

class SendArgs(BaseModel):
    to: str = Field(..., description="Recipient email. Resolve names via contacts.lookup first.")
    subject: str = Field(..., description="Email subject line.")
    body: str = Field(..., description="Plain-text body.")
    cc: str | None = Field(default=None)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _send_impl(args: SendArgs) -> dict[str, Any]:
    svc = await _get_service()
    if svc is None:
        return {"ok": False, "error": "gmail not authorized. run scripts/gmail_auth.py"}
    if not _EMAIL_RE.match(args.to.strip()):
        return {"ok": False, "error": f"invalid recipient email: {args.to!r}"}

    def _run() -> dict[str, Any]:
        msg = EmailMessage()
        msg["To"] = args.to
        msg["Subject"] = args.subject
        if args.cc:
            msg["Cc"] = args.cc
        msg.set_content(args.body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = svc.users().messages().send(
            userId="me", body={"raw": raw},
        ).execute()
        return {"ok": True, "id": sent.get("id"), "thread_id": sent.get("threadId")}

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        return {"ok": False, "error": repr(e)}


# ---- registration ----------------------------------------------------------
#
# We register tools unconditionally so the agent can surface a clean
# "not authorized" error via the normal tool-result path if the token is
# missing. Previously we guarded registration on token presence; that
# left the communication agent silently falling back to browser email,
# which was more confusing than a crisp "run gmail_auth".

tool(
    "gmail.search",
    description=(
        "Search the user's Gmail inbox using Gmail search syntax. "
        "Returns from/subject/date/snippet for each match. Prefer this "
        "over opening mail.google.com in a browser — it's ~10x faster "
        "and gives structured data."
    ),
    params=SearchArgs,
    tags=("gmail",),
    summarizer=_summarize_search,
)(_search_impl)

tool(
    "gmail.read",
    description="Fetch the full body of one Gmail message by id.",
    params=ReadArgs,
    tags=("gmail",),
)(_read_impl)

tool(
    "gmail.unread_count",
    description="Count of unread + total messages in the inbox.",
    params=UnreadArgs,
    tags=("gmail",),
)(_unread_impl)

tool(
    "gmail.send",
    description=(
        "Send an email from the user's Gmail account. Destructive — "
        "requires explicit user confirmation. Resolve name→email via "
        "contacts.lookup first; never guess an address."
    ),
    params=SendArgs,
    requires_confirmation=True,
    tags=("gmail",),
)(_send_impl)
