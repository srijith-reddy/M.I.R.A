"""
Gmail Agent for MIRA:
- search_emails(query, max_results=5)
- send_email(to, subject, body)  # auto-resolves names via Google Contacts
First run will open a browser for OAuth and save a token at:
~/.config/mira/google_token.json
"""

import os
import base64
from email.mime.text import MIMEText
from typing import List, Dict, Any, Optional

from mira.core.config import cfg
from mira.utils import logger


# -------------------------------------------------
# OAuth setup
# -------------------------------------------------
def _require_google_oauthlib():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
        return True
    except Exception:
        logger.log_event("GmailAuth", "google-auth-oauthlib not installed")
        return False


TOKEN_PATH = os.path.expanduser("~/.config/mira/google_token.json")


def _load_scopes() -> List[str]:
    """Load Gmail + People API scopes (send + compose + contacts)."""
    scopes = cfg.GOOGLE_SCOPES
    if not scopes:
        env_scopes = os.getenv(
            "GOOGLE_API_SCOPES",
            "https://www.googleapis.com/auth/gmail.readonly,"
            "https://www.googleapis.com/auth/gmail.send,"
            "https://www.googleapis.com/auth/gmail.compose,"
            "https://www.googleapis.com/auth/gmail.modify,"
            "https://www.googleapis.com/auth/contacts.readonly"
        )
        scopes = [s.strip() for s in env_scopes.split(",") if s.strip()]
    return scopes


def _get_gmail_service():
    """Authorize and build Gmail API client; return (service, scopes, creds)."""
    if not _require_google_oauthlib():
        raise RuntimeError("google-auth-oauthlib missing")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = _load_scopes()
    creds: Optional[Credentials] = None

    # Load existing token
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, scopes)

    # Refresh or create new token if needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.log_error(e, context="GmailAuth.refresh")
                creds = None
        if not creds:
            if not os.path.exists(cfg.GOOGLE_CLIENT_SECRET_FILE):
                raise RuntimeError(
                    f"Missing client_secret.json at {cfg.GOOGLE_CLIENT_SECRET_FILE}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                cfg.GOOGLE_CLIENT_SECRET_FILE, scopes=scopes
            )
            creds = flow.run_local_server(port=cfg.GOOGLE_OAUTH_PORT)

        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    # Disable discovery cache (avoids file_cache warnings)
    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return service, scopes, creds


# -------------------------------------------------
# Contacts resolver (People API)
# -------------------------------------------------
def _get_people_service(creds):
    from googleapiclient.discovery import build
    return build("people", "v1", credentials=creds, cache_discovery=False)


def _resolve_contact_email(name_query: str) -> Optional[str]:
    """Search user's Google Contacts for a name and return the first email."""
    try:
        _, _, creds = _get_gmail_service()
        people = _get_people_service(creds)
        resp = people.people().searchContacts(
            query=name_query,
            readMask="names,emailAddresses",
            sources=["READ_SOURCE_TYPE_CONTACT"],
            pageSize=3
        ).execute()
        results = resp.get("results", [])
        if not results:
            logger.log_event("ContactResolver", f"No match for {name_query}")
            return None

        person = results[0].get("person", {})
        emails = person.get("emailAddresses", [])
        if emails and "value" in emails[0]:
            email = emails[0]["value"]
            logger.log_event("ContactResolver", f"{name_query} → {email}")
            return email
    except Exception as e:
        logger.log_error(e, context="ContactResolver")
    return None


# -------------------------------------------------
# GmailAgent
# -------------------------------------------------
def _parse_headers(payload: Dict[str, Any]) -> Dict[str, str]:
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    return {
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
    }


class GmailAgent:
    def search_emails(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """Search Gmail messages."""
        try:
            service, _, _ = _get_gmail_service()
            resp = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=max_results,
                labelIds=["CATEGORY_PERSONAL"]
            ).execute()

            ids = [m["id"] for m in resp.get("messages", [])]
            results = []
            for mid in ids:
                msg = service.users().messages().get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"]
                ).execute()
                info = _parse_headers(msg.get("payload", {}))
                snippet = msg.get("snippet", "")
                results.append({**info, "id": mid, "snippet": snippet})

            logger.log_event("GmailSearch", f"'{query}' → {len(results)} results")
            return results

        except Exception as e:
            logger.log_error(e, context="GmailAgent.search_emails")
            return []

    def send_email(self, to: str, subject: str, body: str) -> Dict[str, Any]:
        """Send an email. Auto-resolves contact names via Google Contacts."""
        try:
            service, scopes, creds = _get_gmail_service()

            if "https://www.googleapis.com/auth/gmail.send" not in scopes:
                return {"status": "error", "error": "Missing gmail.send scope in config."}

            # 🔍 resolve if name instead of address
            if to and "@" not in to:
                resolved = _resolve_contact_email(to)
                if resolved:
                    to = resolved
                else:
                    return {"status": "error", "error": f"No email found for '{to}'"}

            if not to:
                return {"status": "error", "error": "Recipient address required"}

            # build message
            mime = MIMEText(body or "", "plain", "utf-8")
            mime["to"] = to
            mime["subject"] = subject or "(no subject)"
            mime["from"] = "me"   # required to ensure Gmail sends it

            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")

            # send email
            res = service.users().messages().send(userId="me", body={"raw": raw}).execute()

            # detailed log
            logger.log_event("GmailSend", f"to={to} subject={subject}")
            logger.log_event("GmailSend.Debug", f"API response: {res}")

            # verify sent status
            if "DRAFT" in res.get("labelIds", []):
                return {"status": "error", "error": "Message saved as draft, not sent."}

            return {"status": "sent", "id": res.get("id")}

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.log_error(e, context="GmailAgent.send_email")
            return {"status": "error", "error": str(e)}

    def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Unified entry for search/send operations."""
        if not isinstance(payload, dict):
            return {"ok": False, "error": f"Expected dict, got {type(payload).__name__}"}

        fn = (payload.get("fn") or "").lower()
        if fn == "search":
            q = payload.get("query", "")
            k = int(payload.get("max_results", 5))
            return {"ok": True, "results": self.search_emails(q, max_results=k)}

        if fn == "send":
            return {
                "ok": True,
                **self.send_email(
                    payload.get("to", ""),
                    payload.get("subject", ""),
                    payload.get("body", "")
                )
            }

        return {"ok": False, "error": f"Unknown fn: {fn}"}
