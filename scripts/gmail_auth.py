"""One-time Gmail OAuth consent flow.

Reads the downloaded client credentials from paths.gmail_credentials,
opens a browser for you to grant access, and saves the resulting refresh
token to paths.gmail_token. After this runs once, MIRA's Gmail tools
refresh the access token automatically forever (unless you revoke it in
Google account settings).

    PYTHONPATH=src .venv/bin/python scripts/gmail_auth.py
"""

from __future__ import annotations

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from mira.config.paths import paths

# gmail.modify covers read + send + label + delete. If you want read-only,
# switch to ["https://www.googleapis.com/auth/gmail.readonly"].
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def main() -> int:
    paths.ensure()
    if not paths.gmail_credentials.exists():
        print(
            f"missing {paths.gmail_credentials}\n"
            "download OAuth client JSON from Google Cloud Console "
            "(Desktop app credentials) and save it there.",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(
        str(paths.gmail_credentials), SCOPES,
    )
    creds = flow.run_local_server(port=0)
    paths.gmail_token.write_text(creds.to_json())
    print(f"saved token to {paths.gmail_token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
