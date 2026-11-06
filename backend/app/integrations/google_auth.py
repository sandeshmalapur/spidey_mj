"""Google OAuth loopback authentication CLI for ClassMail J.

Usage:
    python -m app.integrations.google_auth gmail
"""

import sys
from pathlib import Path
from typing import Optional

from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import GMAIL_TOKEN_FILE, GOOGLE_CLIENT_FILE

GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def authenticate_gmail(
    client_secrets_path: Optional[str] = None,
    token_save_path: Optional[str] = None,
) -> str:
    """Run loopback OAuth flow on 127.0.0.1 with ONLY gmail.readonly scope.

    Saves credentials to token_save_path. Never logs or outputs token contents.
    """
    client_file = Path(client_secrets_path or GOOGLE_CLIENT_FILE)
    token_file = Path(token_save_path or GMAIL_TOKEN_FILE)

    if not client_file.exists():
        msg = (
            f"OAuth client configuration file not found at: {client_file}\n"
            "Please ensure you placed your Google OAuth Desktop client JSON file there "
            "(or set the GOOGLE_CLIENT_FILE environment variable)."
        )
        print(f"[ERROR] {msg}", file=sys.stderr)
        raise FileNotFoundError(msg)

    print(f"Initiating Google OAuth flow for Gmail read-only access...")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_file),
        scopes=GMAIL_READONLY_SCOPES,
    )

    # Runs loopback web server on 127.0.0.1 as required for Desktop app OAuth
    creds = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        authorization_prompt_message="ClassMail J is opening your browser for Gmail read-only authorization...",
        success_message="Authentication successful! You can now close this window and return to ClassMail J.",
        open_browser=True,
    )

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    print(f"[SUCCESS] Gmail read-only authentication completed.")
    print(f"Token saved successfully to {token_file}")
    return str(token_file)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1].lower() != "gmail":
        print("Usage: python -m app.integrations.google_auth gmail")
        sys.exit(1)

    try:
        authenticate_gmail()
    except Exception as e:
        print(f"[ERROR] OAuth authentication failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

