"""Gmail API Client wrapper for ClassMail J.

Extracts message headers and plain text bodies (or converts HTML by stripping tags).
Never renders HTML, never fetches links, never downloads attachments.
"""

import base64
import html
import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.config import GMAIL_MAX_MESSAGES, GMAIL_QUERY, GMAIL_TOKEN_FILE


class GmailAuthError(Exception):
    """Raised when Gmail OAuth token is missing, revoked, or expired."""
    pass


class GmailMessage(BaseModel):
    id: str
    thread_id: str
    subject: str = "No Subject"
    sender: str = "Unknown Sender"
    date_str: str = ""
    snippet: str = ""
    body_text: str = ""
    attachments: List[str] = Field(default_factory=list)


class HTMLTextStripper(HTMLParser):
    """Safely extracts visible text from HTML by discarding tags, scripts, and styles."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: List[str] = []
        self._suppress: bool = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in ("script", "style", "head", "meta", "link", "noscript", "svg"):
            self._suppress = True
        elif tag_lower in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in ("script", "style", "head", "meta", "link", "noscript", "svg"):
            self._suppress = False
        elif tag_lower in ("p", "div", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        decoded = html.unescape(raw)
        lines = [line.strip() for line in decoded.splitlines()]
        return "\n".join(l for l in lines if l)


def html_to_plain_text(html_content: str) -> str:
    """Convert HTML content into sanitized plain text."""
    stripper = HTMLTextStripper()
    try:
        stripper.feed(html_content)
        return stripper.get_text()
    except Exception:
        # Fallback regex strip
        text = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", "", html_content, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return html.unescape(text).strip()


class BaseGmailClient(ABC):
    @abstractmethod
    def list_message_ids(self, query: str = GMAIL_QUERY, max_results: int = GMAIL_MAX_MESSAGES) -> List[str]:
        pass

    @abstractmethod
    def get_message(self, message_id: str) -> GmailMessage:
        pass


class GmailClient(BaseGmailClient):
    """Production Gmail API client using authorized user token."""

    def __init__(self, token_path: Optional[str] = None) -> None:
        self.token_path = Path(token_path or GMAIL_TOKEN_FILE)
        self._service: Any = None

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service

        if not self.token_path.exists():
            raise GmailAuthError(
                f"Gmail token file not found at {self.token_path}. "
                "Please run `python -m app.integrations.google_auth gmail` to authenticate."
            )

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(self.token_path))
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self.token_path.write_text(creds.to_json(), encoding="utf-8")

            if not creds.valid:
                raise GmailAuthError(
                    "Gmail credentials are invalid or expired. "
                    "Please run `python -m app.integrations.google_auth gmail` to re-authenticate."
                )

            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            return self._service
        except GmailAuthError:
            raise
        except Exception as e:
            raise GmailAuthError(f"Failed to initialize Gmail API client: {e}")

    def list_message_ids(self, query: str = GMAIL_QUERY, max_results: int = GMAIL_MAX_MESSAGES) -> List[str]:
        service = self._get_service()
        try:
            res = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
            messages = res.get("messages", [])
            return [m["id"] for m in messages if "id" in m]
        except Exception as e:
            raise RuntimeError(f"Error querying Gmail messages: {e}")

    def get_message(self, message_id: str) -> GmailMessage:
        service = self._get_service()
        try:
            raw_msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            return self._parse_message_payload(raw_msg)
        except Exception as e:
            raise RuntimeError(f"Error fetching message {message_id}: {e}")

    def _parse_message_payload(self, msg_dict: Dict[str, Any]) -> GmailMessage:
        msg_id = msg_dict.get("id", "")
        thread_id = msg_dict.get("threadId", msg_id)
        snippet = msg_dict.get("snippet", "")

        payload = msg_dict.get("payload", {})
        headers_list = payload.get("headers", [])
        headers_map = {h["name"].lower(): h["value"] for h in headers_list if "name" in h and "value" in h}

        subject = headers_map.get("subject", "No Subject")
        sender = headers_map.get("from", "Unknown Sender")
        date_str = headers_map.get("date", "")

        # Extract body text and attachment names
        plain_bodies: List[str] = []
        html_bodies: List[str] = []
        attachment_names: List[str] = []

        def _traverse_parts(part: Dict[str, Any]) -> None:
            filename = part.get("filename")
            if filename:
                attachment_names.append(filename)

            mime_type = part.get("mimeType", "").lower()
            body = part.get("body", {})
            data_b64 = body.get("data")

            if data_b64:
                try:
                    # Base64url decode
                    decoded = base64.urlsafe_b64decode(data_b64.encode("ASCII")).decode("utf-8", errors="replace")
                    if mime_type == "text/plain":
                        plain_bodies.append(decoded)
                    elif mime_type == "text/html":
                        html_bodies.append(decoded)
                except Exception:
                    pass

            for subpart in part.get("parts", []):
                _traverse_parts(subpart)

        _traverse_parts(payload)

        # Decide final body text
        if plain_bodies:
            body_text = "\n\n".join(plain_bodies)
        elif html_bodies:
            raw_html = "\n\n".join(html_bodies)
            body_text = html_to_plain_text(raw_html)
        else:
            body_text = snippet

        # Note attachment names without downloading
        if attachment_names:
            body_text += f"\n\n[Attachments: {', '.join(attachment_names)}]"

        # Cap at 20,000 characters
        body_text = body_text[:20000].strip()

        return GmailMessage(
            id=msg_id,
            thread_id=thread_id,
            subject=subject,
            sender=sender,
            date_str=date_str,
            snippet=snippet,
            body_text=body_text,
            attachments=attachment_names,
        )

