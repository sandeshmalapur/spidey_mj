"""Unit tests for Gmail integration and synchronization.

Uses a FakeGmailClient (mock) to verify business logic offline.
NOTE: Unit tests do NOT prove that real Gmail API connectivity works.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
import pytest

from app.db import get_connection, init_db
from app.integrations.gmail_client import (
    BaseGmailClient,
    GmailClient,
    GmailMessage,
    html_to_plain_text,
)
from app.integrations.sync_service import get_sync_status, sync_gmail
from app.models import DeadlineStatus, ItemState


class FakeGmailClient(BaseGmailClient):
    """Clearly named mock Gmail client for offline unit testing.

    Unit tests using this mock do NOT prove real Gmail works.
    """

    def __init__(self, messages: Optional[List[GmailMessage]] = None) -> None:
        self.messages: Dict[str, GmailMessage] = {}
        if messages:
            for m in messages:
                self.messages[m.id] = m

    def list_message_ids(self, query: str = "", max_results: int = 25) -> List[str]:
        return list(self.messages.keys())[:max_results]

    def get_message(self, message_id: str) -> GmailMessage:
        if message_id not in self.messages:
            raise KeyError(f"Message {message_id} not found in mock.")
        return self.messages[message_id]


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_gmail_sync.db")
    init_db(db_file)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_file)
    conn = get_connection(db_file)
    try:
        yield conn
    finally:
        conn.close()


def test_gmail_sync_dedupe(test_db):
    """Test deduplication: syncing the same message ID twice skips on the second run."""
    conn = test_db

    msg1 = GmailMessage(
        id="msg-001",
        thread_id="thread-001",
        subject="Physics Homework 1",
        sender="prof@university.edu",
        date_str="Fri, 06 Nov 2026 10:00:00 +0000",
        snippet="Submit homework 1 by next week",
        body_text="Please submit by November 13, 2026 at 5:00 PM EST via portal.",
    )
    msg2 = GmailMessage(
        id="msg-002",
        thread_id="thread-002",
        subject="Chemistry Lab Notes",
        sender="ta@university.edu",
        date_str="Fri, 06 Nov 2026 11:00:00 +0000",
        snippet="Lab notes for review",
        body_text="Read pages 10-20. No action required.",
    )

    fake_client = FakeGmailClient([msg1, msg2])

    # First sync run
    res1 = sync_gmail(client=fake_client, conn=conn)
    assert res1.status == "success"
    assert res1.counts.fetched == 2
    assert res1.counts.created == 2
    assert res1.counts.skipped == 0
    assert res1.counts.errors == 0

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items WHERE source_type = 'gmail';")
    assert cur.fetchone()[0] == 2

    # Second sync run with identical messages
    res2 = sync_gmail(client=fake_client, conn=conn)
    assert res2.status == "success"
    assert res2.counts.fetched == 2
    assert res2.counts.created == 0
    assert res2.counts.skipped == 2
    assert res2.counts.errors == 0

    # Ensure no duplicates inserted
    cur.execute("SELECT COUNT(*) FROM items WHERE source_type = 'gmail';")
    assert cur.fetchone()[0] == 2


def test_gmail_sync_thread_linked_deadline_update_and_events(test_db):
    """Test thread-linked update: a later message in same thread with different confirmed deadline

    updates the existing item's deadline and writes a row in item_events.
    """
    conn = test_db

    msg_initial = GmailMessage(
        id="msg-proj-1",
        thread_id="thread-project-42",
        subject="CS 200 Project Specification",
        sender="instructor@university.edu",
        date_str="Wed, 04 Nov 2026 12:00:00 +0000",
        snippet="Initial deadline announced",
        body_text="Please submit Project 1 by November 15, 2026 at 5:00 PM EST to Gradescope.",
    )

    client1 = FakeGmailClient([msg_initial])
    res1 = sync_gmail(client=client1, conn=conn)
    assert res1.counts.created == 1

    cur = conn.cursor()
    cur.execute("SELECT * FROM items WHERE source_type = 'gmail' AND thread_id = 'thread-project-42';")
    initial_item = dict(cur.fetchone())
    initial_id = initial_item["id"]
    assert initial_item["deadline_at"] == "2026-11-15T22:00:00Z"
    assert initial_item["deadline_status"] == "confirmed"

    # Later message in the SAME thread with EXTENDED deadline
    msg_extended = GmailMessage(
        id="msg-proj-2",
        thread_id="thread-project-42",
        subject="Re: CS 200 Project Specification - Deadline Extended",
        sender="instructor@university.edu",
        date_str="Fri, 06 Nov 2026 09:00:00 +0000",
        snippet="Deadline extended",
        body_text="Good news! Project 1 deadline is extended. Submit by November 20, 2026 at 5:00 PM EST.",
    )

    client2 = FakeGmailClient([msg_extended])
    res2 = sync_gmail(client=client2, conn=conn)
    assert res2.counts.updated == 1
    assert res2.counts.created == 0

    # Verify existing item was updated rather than duplicated
    cur.execute("SELECT COUNT(*) FROM items WHERE source_type = 'gmail' AND thread_id = 'thread-project-42';")
    assert cur.fetchone()[0] == 1

    cur.execute("SELECT * FROM items WHERE id = ?;", (initial_id,))
    updated_item = dict(cur.fetchone())
    assert updated_item["deadline_at"] == "2026-11-20T22:00:00Z"

    # Verify item_events recorded the change
    cur.execute("SELECT * FROM item_events WHERE item_id = ?;", (initial_id,))
    events = [dict(r) for r in cur.fetchall()]
    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "deadline_updated"
    assert event["old_value"] == "2026-11-15T22:00:00Z"
    assert event["new_value"] == "2026-11-20T22:00:00Z"
    assert event["source_id"] == "msg-proj-2"


def test_gmail_sync_completed_item_not_reopened(test_db):
    """Test that if an item is already completed, an incoming thread deadline update does not reopen it."""
    conn = test_db

    msg1 = GmailMessage(
        id="msg-task-1",
        thread_id="thread-done-99",
        subject="Math Quiz Preparation",
        sender="math@university.edu",
        date_str="Mon, 02 Nov 2026 08:00:00 +0000",
        snippet="Quiz on Friday",
        body_text="Quiz submission due by November 06, 2026 at 5:00 PM EST.",
    )

    client1 = FakeGmailClient([msg1])
    sync_gmail(client=client1, conn=conn)

    cur = conn.cursor()
    cur.execute("SELECT id FROM items WHERE source_id = 'msg-task-1';")
    item_id = cur.fetchone()[0]

    # User marks the item completed
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE items SET state = ?, completed_at = ? WHERE id = ?;",
        (ItemState.COMPLETED.value, now_iso, item_id),
    )
    conn.commit()

    # Later message arrives with a revised deadline in the same thread
    msg2 = GmailMessage(
        id="msg-task-2",
        thread_id="thread-done-99",
        subject="Re: Math Quiz Preparation - Makeup date",
        sender="math@university.edu",
        date_str="Thu, 05 Nov 2026 10:00:00 +0000",
        snippet="Makeup date available",
        body_text="Makeup quiz due by November 10, 2026 at 5:00 PM EST.",
    )

    client2 = FakeGmailClient([msg2])
    res2 = sync_gmail(client=client2, conn=conn)
    assert res2.counts.updated == 1

    # Verify item is STILL COMPLETED (not reopened)
    cur.execute("SELECT state, completed_at, deadline_at FROM items WHERE id = ?;", (item_id,))
    row = dict(cur.fetchone())
    assert row["state"] == ItemState.COMPLETED.value
    assert row["completed_at"] is not None
    assert row["deadline_at"] == "2026-11-10T22:00:00Z"

    # Event recorded
    cur.execute("SELECT COUNT(*) FROM item_events WHERE item_id = ?;", (item_id,))
    assert cur.fetchone()[0] == 1


def test_gmail_sync_missing_token_message(tmp_path, monkeypatch, test_db):
    """Test that a missing token file returns a clear message instructing the user

    to run the auth CLI without crashing.
    """
    conn = test_db
    non_existent_token = str(tmp_path / "secrets" / "missing_token.json")
    monkeypatch.setattr("app.config.GMAIL_TOKEN_FILE", non_existent_token)

    # Sync using production client loader (None -> GmailClient())
    res = sync_gmail(client=None, conn=conn)
    assert res.status == "error"
    assert res.counts.errors == 1
    assert "python -m app.integrations.google_auth gmail" in res.message

    # Verify status endpoint also reflects the error
    status = get_sync_status(conn=conn)
    assert status.status == "error"
    assert status.error_message is not None
    assert "python -m app.integrations.google_auth gmail" in status.error_message


def test_gmail_sync_html_stripped():
    """Test that HTML emails have scripts, styles, and markup stripped cleanly."""
    raw_html = """
    <html>
      <head>
        <title>Ignored</title>
        <script>alert("Malicious script execution attempt");</script>
        <style>body { background-color: red; }</style>
      </head>
      <body>
        <h2>Important Course Update</h2>
        <p>Please submit your homework assignment by <strong>November 16, 2026 at 11:59 PM EST</strong>.</p>
        <div>For more details, visit <a href="https://example.com/course">the portal</a>.</div>
      </body>
    </html>
    """

    stripped = html_to_plain_text(raw_html)
    assert "<script>" not in stripped
    assert "alert" not in stripped
    assert "<style>" not in stripped
    assert "background-color" not in stripped
    assert "<p>" not in stripped
    assert "Important Course Update" in stripped
    assert "November 16, 2026 at 11:59 PM EST" in stripped

    # Test extraction on the stripped body
    from app.extraction import RulesProvider
    provider = RulesProvider()
    res = provider.extract(
        text=stripped,
        subject="Course Update",
        received_at=datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert res.deadline_status == DeadlineStatus.CONFIRMED
    assert res.deadline_text == "November 16, 2026 at 11:59 PM EST"
    assert res.deadline_at == "2026-11-17T04:59:00Z"


def test_gmail_sync_endpoints_flow(tmp_path, monkeypatch):
    """Test /api/sync/status and /api/sync/gmail endpoints."""
    from fastapi.testclient import TestClient
    from app.main import app

    db_file = str(tmp_path / "test_api_sync.db")
    init_db(db_file)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_file)

    missing_token = str(tmp_path / "secrets" / "missing.json")
    monkeypatch.setattr("app.config.GMAIL_TOKEN_FILE", missing_token)

    with TestClient(app) as client:
        # Check initial sync status
        status_res = client.get("/api/sync/status")
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["source_type"] == "gmail"
        assert status_data["status"] == "idle"

        # Trigger sync (should report missing token error gracefully without 500)
        sync_res = client.post("/api/sync/gmail")
        assert sync_res.status_code == 200
        sync_data = sync_res.json()
        assert sync_data["status"] == "error"
        assert "python -m app.integrations.google_auth gmail" in sync_data["message"]
        assert sync_data["counts"]["errors"] == 1
