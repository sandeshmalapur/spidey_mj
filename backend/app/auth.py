"""Authentication, session management, and PKCE OpenID Connect for ClassMail J."""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from google.oauth2.credentials import Credentials

import app.config
from app.db import get_connection

logger = logging.getLogger("classmail.auth")

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

CLASSROOM_READONLY_SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
]

OIDC_SCOPES = ["openid", "email", "profile"]

ALL_AUTH_SCOPES = OIDC_SCOPES + [GMAIL_READONLY_SCOPE] + CLASSROOM_READONLY_SCOPES


def format_iso(dt: datetime) -> str:
    """Format datetime as UTC ISO 8601 string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# In-Memory Modest Rate Limiter
# ---------------------------------------------------------------------------
class SimpleRateLimiter:
    """Modest in-memory sliding window rate limiter."""

    def __init__(self, max_requests: int = 30, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: Dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        timestamps = self._history.get(key, [])
        # Filter older timestamps
        active = [t for t in timestamps if t > cutoff]
        if len(active) >= self.max_requests:
            self._history[key] = active
            return False
        active.append(now)
        self._history[key] = active
        return True

    def reset(self) -> None:
        self._history.clear()


auth_rate_limiter = SimpleRateLimiter(max_requests=30, window_seconds=60)


# ---------------------------------------------------------------------------
# Web Client Config Loader
# ---------------------------------------------------------------------------
def load_web_client_config(client_file_path: Optional[str] = None) -> Dict[str, str]:
    """Load OAuth client configuration for web flows from GOOGLE_WEB_CLIENT_FILE.

    Never logs or exposes client_secret.
    """
    path = Path(client_file_path or app.config.GOOGLE_WEB_CLIENT_FILE)
    if not path.is_file():
        raise FileNotFoundError(
            f"Google Web Client configuration file not found at: {path}. "
            "Please ensure you placed your Google OAuth Web client JSON file there "
            "(or set the GOOGLE_WEB_CLIENT_FILE environment variable)."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse OAuth client JSON file: {e}")

    cfg = data.get("web") or data.get("installed") or data
    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")

    if not client_id or not client_secret:
        raise ValueError("OAuth client JSON must contain 'client_id' and 'client_secret'.")

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": cfg.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth"),
        "token_uri": cfg.get("token_uri", "https://oauth2.googleapis.com/token"),
    }


# ---------------------------------------------------------------------------
# PKCE Helpers
# ---------------------------------------------------------------------------
def generate_pkce_pair() -> Tuple[str, str]:
    """Generate RFC 7636 PKCE code_verifier and S256 code_challenge."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# Cookie Signing & Verification
# ---------------------------------------------------------------------------
def sign_session_id(session_id: str, secret: str) -> str:
    """Sign a session ID using HMAC-SHA256."""
    sig = hmac.new(secret.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{session_id}.{sig}"


def verify_signed_session_id(cookie_val: Optional[str], secret: str) -> Optional[str]:
    """Verify HMAC-SHA256 signed session cookie with constant-time comparison.

    Returns the session_id if valid, or None if invalid or tampered with.
    """
    if not cookie_val or "." not in cookie_val:
        return None
    session_id, sig = cookie_val.split(".", 1)
    if not session_id or not sig:
        return None
    expected_sig = hmac.new(secret.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected_sig):
        return session_id
    return None


# ---------------------------------------------------------------------------
# SQLite Session Store
# ---------------------------------------------------------------------------
def create_session(
    conn,
    email: str,
    display_name: Optional[str] = None,
    ttl_hours: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, str, str]:
    """Create a new server-side session in the database.

    Returns (session_id, signed_cookie_value, expires_at_iso).
    """
    now_dt = now or datetime.now(timezone.utc)
    ttl = ttl_hours if ttl_hours is not None else getattr(app.config, "SESSION_TTL_HOURS", 12)
    expires_dt = now_dt + timedelta(hours=ttl)

    session_id = secrets.token_urlsafe(32)
    created_at_iso = format_iso(now_dt)
    expires_at_iso = format_iso(expires_dt)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sessions (id, email, display_name, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?);
        """,
        (session_id, email.lower(), display_name, created_at_iso, expires_at_iso),
    )
    conn.commit()

    secret = getattr(app.config, "SESSION_SECRET", "")
    signed_val = sign_session_id(session_id, secret)
    return session_id, signed_val, expires_at_iso


def get_session(conn, session_id: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Retrieve and validate a session by ID.

    If the session is expired, it is deleted and None is returned.
    """
    now_dt = now or datetime.now(timezone.utc)
    now_iso = format_iso(now_dt)

    cur = conn.cursor()
    cur.execute("SELECT id, email, display_name, created_at, expires_at FROM sessions WHERE id = ?;", (session_id,))
    row = cur.fetchone()
    if not row:
        return None

    sess = dict(row)
    if sess["expires_at"] < now_iso:
        cur.execute("DELETE FROM sessions WHERE id = ?;", (session_id,))
        conn.commit()
        return None

    return sess


def delete_session(conn, session_id: str) -> None:
    """Delete a session from the database on logout."""
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE id = ?;", (session_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Auth States (PKCE & CSRF)
# ---------------------------------------------------------------------------
def save_auth_state(
    conn,
    state: str,
    flow_type: str,
    code_verifier: str,
    nonce: Optional[str] = None,
    ttl_minutes: int = 15,
    now: Optional[datetime] = None,
) -> None:
    """Save pending OAuth state and PKCE verifier."""
    now_dt = now or datetime.now(timezone.utc)
    expires_dt = now_dt + timedelta(minutes=ttl_minutes)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO auth_states (state, flow_type, code_verifier, nonce, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (state, flow_type, code_verifier, nonce, format_iso(now_dt), format_iso(expires_dt)),
    )
    conn.commit()


def consume_auth_state(
    conn,
    state: str,
    flow_type: str,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Validate and atomically consume an auth state (single use)."""
    now_dt = now or datetime.now(timezone.utc)
    now_iso = format_iso(now_dt)

    cur = conn.cursor()
    cur.execute("SELECT * FROM auth_states WHERE state = ? AND flow_type = ?;", (state, flow_type))
    row = cur.fetchone()
    if not row:
        return None

    # Always delete to ensure single-use
    cur.execute("DELETE FROM auth_states WHERE state = ?;", (state,))
    conn.commit()

    state_dict = dict(row)
    if state_dict["expires_at"] < now_iso:
        return None

    return state_dict


# ---------------------------------------------------------------------------
# Pending Connections (Account Mismatch Confirmation)
# ---------------------------------------------------------------------------
def save_pending_connection(
    conn,
    source_type: str,
    granted_email: str,
    token_json: str,
    ttl_minutes: int = 10,
    now: Optional[datetime] = None,
) -> str:
    """Save a pending connection when the granted account differs from OWNER_EMAIL."""
    now_dt = now or datetime.now(timezone.utc)
    expires_dt = now_dt + timedelta(minutes=ttl_minutes)
    confirmation_id = secrets.token_urlsafe(32)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pending_connections (id, source_type, granted_email, token_json, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (confirmation_id, source_type, granted_email.lower(), token_json, format_iso(now_dt), format_iso(expires_dt)),
    )
    conn.commit()
    return confirmation_id


def consume_pending_connection(
    conn,
    confirmation_id: str,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve and delete a pending connection for confirmation."""
    now_dt = now or datetime.now(timezone.utc)
    now_iso = format_iso(now_dt)

    cur = conn.cursor()
    cur.execute("SELECT * FROM pending_connections WHERE id = ?;", (confirmation_id,))
    row = cur.fetchone()
    if not row:
        return None

    cur.execute("DELETE FROM pending_connections WHERE id = ?;", (confirmation_id,))
    conn.commit()

    conn_dict = dict(row)
    if conn_dict["expires_at"] < now_iso:
        return None

    return conn_dict


def delete_pending_connection(conn, confirmation_id: str) -> None:
    """Delete a pending connection when cancelled."""
    cur = conn.cursor()
    cur.execute("DELETE FROM pending_connections WHERE id = ?;", (confirmation_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Token Exchange & ID Token Verification
# ---------------------------------------------------------------------------
def exchange_code_for_tokens(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_config: Dict[str, str],
) -> Dict[str, Any]:
    """Exchange authorization code for tokens at Google token endpoint."""
    token_uri = client_config.get("token_uri", "https://oauth2.googleapis.com/token")
    data = {
        "code": code,
        "client_id": client_config["client_id"],
        "client_secret": client_config["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }

    try:
        response = httpx.post(token_uri, data=data, timeout=10.0)
        if response.status_code != 200:
            logger.warning(f"Google token exchange failed with status {response.status_code}")
            raise RuntimeError(f"Google token exchange returned status {response.status_code}")
        return response.json()
    except Exception as e:
        logger.warning(f"Error during Google token exchange: {e}")
        raise


def verify_google_id_token(
    id_token_jwt: str,
    client_id: str,
    expected_nonce: Optional[str] = None,
    request_adapter: Optional[Any] = None,
) -> Dict[str, Any]:
    """Verify Google OpenID Connect ID token signature, issuer, audience, expiry, and nonce."""
    adapter = request_adapter or google_requests.Request()
    try:
        claims = google_id_token.verify_oauth2_token(id_token_jwt, adapter, audience=client_id)
    except Exception as e:
        logger.warning(f"Google ID token signature/claims verification failed: {e}")
        raise ValueError("Invalid Google ID token")

    # Issuer verification
    issuer = claims.get("iss", "")
    if issuer not in ("https://accounts.google.com", "accounts.google.com"):
        raise ValueError(f"Invalid ID token issuer: {issuer}")

    # Nonce verification
    if expected_nonce:
        token_nonce = claims.get("nonce")
        if not token_nonce or not hmac.compare_digest(str(token_nonce), str(expected_nonce)):
            raise ValueError("ID token nonce does not match expected nonce")

    # Email verification
    if not claims.get("email_verified"):
        raise ValueError("Google account email is not verified")

    return claims


# ---------------------------------------------------------------------------
# Token Persistence & Revocation Helpers
# ---------------------------------------------------------------------------
def save_service_token_file(
    token_path_str: str,
    access_token: str,
    refresh_token: Optional[str],
    scopes: list[str],
    client_config: Dict[str, str],
) -> str:
    """Save token to file in the exact authorized user format read by Credentials.from_authorized_user_file."""
    token_path = Path(token_path_str)
    existing_refresh: Optional[str] = None
    if token_path.exists():
        try:
            old_data = json.loads(token_path.read_text(encoding="utf-8"))
            existing_refresh = old_data.get("refresh_token")
        except Exception:
            pass

    final_refresh = refresh_token or existing_refresh
    creds = Credentials(
        token=access_token,
        refresh_token=final_refresh,
        token_uri=client_config.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=client_config["client_id"],
        client_secret=client_config["client_secret"],
        scopes=scopes,
    )
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return str(token_path)


def revoke_google_token(token: str) -> bool:
    """Revoke an access or refresh token with Google's official revocation endpoint.

    Verified endpoint: https://oauth2.googleapis.com/revoke
    """
    try:
        resp = httpx.post(
            "https://oauth2.googleapis.com/revoke",
            data={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Google token revocation failed or timed out: {e}")
        return False


def get_session_from_cookie(request, conn) -> Optional[Dict[str, Any]]:
    """Retrieve validated server session from signed cookie."""
    cookie_val = request.cookies.get("classmail_session")
    if not cookie_val:
        return None
    secret = getattr(app.config, "SESSION_SECRET", "")
    session_id = verify_signed_session_id(cookie_val, secret)
    if not session_id:
        return None
    return get_session(conn, session_id)

