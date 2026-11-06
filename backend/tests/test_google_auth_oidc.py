"""Tests for Google OpenID Connect and unified data consent integration."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.auth import (
    ALL_AUTH_SCOPES,
    CLASSROOM_READONLY_SCOPES,
    GMAIL_READONLY_SCOPE,
    create_session,
    generate_pkce_pair,
    save_auth_state,
    sign_session_id,
    verify_signed_session_id,
)
import app.config
from app.db import get_connection, init_db
from app.main import app


@pytest.fixture
def auth_test_env(tmp_path, monkeypatch):
    """Set up temporary database, secret files, and mock auth environment."""
    db_file = str(tmp_path / "test_auth.db")
    init_db(db_file)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_file)

    gmail_token = str(tmp_path / "secrets" / "token_gmail.json")
    classroom_token = str(tmp_path / "secrets" / "token_classroom.json")
    web_client_file = str(tmp_path / "secrets" / "google_web_client.json")

    Path(web_client_file).parent.mkdir(parents=True, exist_ok=True)
    Path(web_client_file).write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "client_secret": "test-secret-value",
                    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.config.GMAIL_TOKEN_FILE", gmail_token)
    monkeypatch.setattr("app.config.CLASSROOM_TOKEN_FILE", classroom_token)
    monkeypatch.setattr("app.config.GOOGLE_WEB_CLIENT_FILE", web_client_file)
    monkeypatch.setattr("app.config.AUTH_ENABLED", True)
    monkeypatch.setattr("app.config.SESSION_SECRET", "super-secret-random-key-at-least-32-chars-long!")
    monkeypatch.setattr("app.config.OWNER_EMAIL", "owner@example.edu")
    monkeypatch.setattr("app.config.AUTH_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/google/callback")

    return {
        "db": db_file,
        "gmail_token": gmail_token,
        "classroom_token": classroom_token,
        "web_client_file": web_client_file,
    }


def test_auth_start_generates_pkce_and_requested_scopes(auth_test_env):
    """Test GET /api/auth/google/start constructs proper OAuth URL with PKCE and exact scopes."""
    with TestClient(app) as client:
        # Request with JSON accept header to inspect auth URL directly
        res = client.get("/api/auth/google/start", headers={"Accept": "application/json"})
        assert res.status_code == 200
        data = res.json()
        assert "auth_url" in data
        auth_url = data["auth_url"]

        # Verify query parameters
        assert "access_type=offline" in auth_url
        assert "prompt=consent" in auth_url
        assert "include_granted_scopes=true" in auth_url
        assert "code_challenge=" in auth_url
        assert "code_challenge_method=S256" in auth_url
        assert "response_type=code" in auth_url
        assert "test-client-id" in auth_url

        # Verify all required scopes requested in one unified flow
        assert "openid" in auth_url
        assert "email" in auth_url
        assert "profile" in auth_url
        assert "gmail.readonly" in auth_url
        assert "classroom.courses.readonly" in auth_url
        assert "classroom.coursework.me.readonly" in auth_url
        assert "classroom.announcements.readonly" in auth_url
        assert "classroom.student-submissions.me.readonly" in auth_url


def test_auth_enforces_401_on_protected_endpoints_when_enabled(auth_test_env):
    """When AUTH_ENABLED=true, unauthenticated calls to /api/* return 401."""
    with TestClient(app) as client:
        # Protected endpoints return 401 without valid session cookie
        res = client.get("/api/items")
        assert res.status_code == 401
        assert res.json() == {"detail": "Unauthorized"}

        res = client.get("/api/connections")
        assert res.status_code == 401

        # Health endpoint remains accessible
        health_res = client.get("/api/health")
        assert health_res.status_code == 200


def test_auth_disabled_allows_unauthenticated_access(auth_test_env, monkeypatch):
    """When AUTH_ENABLED=false, app behaves normally with zero login barrier."""
    monkeypatch.setattr("app.config.AUTH_ENABLED", False)

    with TestClient(app) as client:
        res = client.get("/api/items")
        assert res.status_code == 200

        me_res = client.get("/api/auth/me")
        assert me_res.status_code == 200
        assert me_res.json() == {
            "authenticated": True,
            "auth_enabled": False,
            "email": None,
            "display_name": None,
        }


def test_auth_callback_rejects_non_owner(auth_test_env, monkeypatch):
    """OAuth callback refuses non-owner accounts with a 403 page and saves no tokens."""
    conn = get_connection()
    save_auth_state(conn, state="valid_state_123", flow_type="login", code_verifier="verifier_abc", nonce="nonce_123")
    conn.close()

    # Mock token exchange and ID token verification
    def mock_exchange(*args, **kwargs):
        return {
            "access_token": "ya29.fake_token",
            "id_token": "fake_id_token",
            "scope": f"openid email {GMAIL_READONLY_SCOPE}",
        }

    def mock_verify_id_token(*args, **kwargs):
        return {
            "email": "intruder@stranger.org",
            "email_verified": True,
            "name": "Intruder",
            "iss": "https://accounts.google.com",
            "nonce": "nonce_123",
        }

    monkeypatch.setattr("app.main.exchange_code_for_tokens", mock_exchange)
    monkeypatch.setattr("app.main.verify_google_id_token", mock_verify_id_token)

    with TestClient(app) as client:
        res = client.get("/api/auth/google/callback?code=mock_code&state=valid_state_123")
        assert res.status_code == 403
        assert "Access Not Allowed" in res.text
        # Assert no cookie was issued
        assert "classmail_session" not in res.cookies
        # Assert no tokens were written
        assert not Path(auth_test_env["gmail_token"]).exists()


def test_auth_callback_accepts_owner_and_persists_granted_tokens(auth_test_env, monkeypatch):
    """OAuth callback allows owner, creates session, and saves granted service tokens."""
    conn = get_connection()
    save_auth_state(conn, state="valid_state_owner", flow_type="login", code_verifier="verifier_xyz", nonce="nonce_xyz")
    conn.close()

    # Grant both Gmail and Classroom scopes
    granted_scopes = f"openid email profile {GMAIL_READONLY_SCOPE} {' '.join(CLASSROOM_READONLY_SCOPES)}"

    def mock_exchange(*args, **kwargs):
        return {
            "access_token": "ya29.owner_access_token",
            "refresh_token": "1//owner_refresh_token",
            "id_token": "fake_owner_id_token",
            "scope": granted_scopes,
        }

    def mock_verify_id_token(*args, **kwargs):
        return {
            "email": "OWNER@example.edu",  # Case-insensitive match check
            "email_verified": True,
            "name": "Dr. Owner",
            "iss": "https://accounts.google.com",
            "nonce": "nonce_xyz",
        }

    monkeypatch.setattr("app.main.exchange_code_for_tokens", mock_exchange)
    monkeypatch.setattr("app.main.verify_google_id_token", mock_verify_id_token)

    with TestClient(app) as client:
        res = client.get("/api/auth/google/callback?code=mock_code&state=valid_state_owner", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/"
        assert "classmail_session" in res.cookies

        # Verify token files were saved at the expected paths
        assert Path(auth_test_env["gmail_token"]).is_file()
        assert Path(auth_test_env["classroom_token"]).is_file()

        # Verify saved token file structure
        gmail_content = json.loads(Path(auth_test_env["gmail_token"]).read_text(encoding="utf-8"))
        assert gmail_content["token"] == "ya29.owner_access_token"
        assert gmail_content["refresh_token"] == "1//owner_refresh_token"
        assert GMAIL_READONLY_SCOPE in gmail_content["scopes"]

        # Call /api/auth/me with the session cookie
        me_res = client.get("/api/auth/me")
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["authenticated"] is True
        assert me_data["email"] == "owner@example.edu"
        assert me_data["display_name"] == "Dr. Owner"

        # Call /api/connections
        conn_res = client.get("/api/connections")
        assert conn_res.status_code == 200
        conns = conn_res.json()
        assert conns["gmail"]["connected"] is True
        assert conns["classroom"]["connected"] is True
        # Ensure raw tokens are NEVER present in connection status response
        assert "token" not in str(conns)
        assert "ya29" not in str(conns)


def test_logout_ends_session_without_deleting_tokens(auth_test_env):
    """Logout invalidates the session cookie but preserves saved tokens."""
    Path(auth_test_env["gmail_token"]).write_text(json.dumps({"token": "abc"}), encoding="utf-8")

    conn = get_connection()
    session_id, signed_cookie, _ = create_session(conn, email="owner@example.edu")
    conn.close()

    with TestClient(app) as client:
        client.cookies.set("classmail_session", signed_cookie)

        # Confirm session is authenticated
        me_res = client.get("/api/auth/me")
        assert me_res.status_code == 200
        assert me_res.json()["authenticated"] is True

        # Perform logout
        logout_res = client.post("/api/auth/logout")
        assert logout_res.status_code == 200

        # Subsequent check returns 401
        subseq_res = client.get("/api/items")
        assert subseq_res.status_code == 401

        # Verify token file was NOT deleted
        assert Path(auth_test_env["gmail_token"]).is_file()


def test_disconnect_integration_endpoint(auth_test_env, monkeypatch):
    """Disconnect endpoint deletes token file and calls revocation."""
    Path(auth_test_env["gmail_token"]).write_text(
        json.dumps({"token": "tok_to_revoke", "refresh_token": "ref_to_revoke"}),
        encoding="utf-8",
    )

    revocation_called = []

    def mock_revoke(tok):
        revocation_called.append(tok)
        return True

    monkeypatch.setattr("app.main.revoke_google_token", mock_revoke)

    conn = get_connection()
    session_id, signed_cookie, _ = create_session(conn, email="owner@example.edu")
    conn.close()

    with TestClient(app) as client:
        client.cookies.set("classmail_session", signed_cookie)

        res = client.post("/api/connections/disconnect", json={"source": "gmail"})
        assert res.status_code == 200
        assert res.json() == {"ok": True, "source": "gmail", "revoked": True}

        # Token file must be deleted
        assert not Path(auth_test_env["gmail_token"]).exists()
        # Revocation was called with refresh token
        assert "ref_to_revoke" in revocation_called


def test_startup_validation_refuses_invalid_config(monkeypatch):
    """App refuses to start if AUTH_ENABLED=true with short secret or missing owner."""
    from app.config import validate_auth_config

    monkeypatch.setattr("app.config.AUTH_ENABLED", True)
    monkeypatch.setattr("app.config.SESSION_SECRET", "short")
    monkeypatch.setattr("app.config.OWNER_EMAIL", "owner@example.com")

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        validate_auth_config()

    monkeypatch.setattr("app.config.SESSION_SECRET", "12345678901234567890123456789012345")
    monkeypatch.setattr("app.config.OWNER_EMAIL", "")
    with pytest.raises(RuntimeError, match="OWNER_EMAIL must be set"):
        validate_auth_config()

