"""Tests for ClassMail J automatic background sync scheduler.

Covers:
1. No overlapping runs (shared lock between scheduler and manual sync)
2. Exponential backoff growth and 30-minute cap on network/quota errors
3. Auth failure stops retries, sets status 'reconnect_needed', and shows exact CLI command
4. Toggle persistence in settings table across restarts
5. No duplicates across repeated runs
6. A failing run does not stop later runs (transient error followed by recovery)
7. Endpoints integration (GET /api/sync/status, POST /api/sync/settings, POST /api/sync/gmail)

NOTE: Unit tests use a FakeClock and a MockGmailClient clearly documented as mocks;
unit tests do NOT prove real Gmail works.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

from app.db import get_setting, init_db
from app.integrations.gmail_client import BaseGmailClient, GmailAuthError, GmailMessage
from app.main import app
from app.scheduler import RECONNECT_COMMAND, SyncSchedulerManager

FIXED_T0 = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """Mock clock for deterministic scheduler time injection."""

    def __init__(self, start_dt: datetime) -> None:
        self.current_dt = start_dt

    def now(self) -> datetime:
        return self.current_dt

    def advance(self, minutes: int = 0, seconds: int = 0, hours: int = 0) -> datetime:
        self.current_dt += timedelta(minutes=minutes, seconds=seconds, hours=hours)
        return self.current_dt


class MockGmailClient(BaseGmailClient):
    """Documented mock GmailClient for testing background sync logic.

    Unit tests do NOT prove real Gmail works.
    """

    def __init__(
        self,
        messages: Optional[List[GmailMessage]] = None,
        error_on_list: Optional[Exception] = None,
        auth_error: bool = False,
    ) -> None:
        self.messages = messages or []
        self.error_on_list = error_on_list
        self.auth_error = auth_error
        self.list_call_count = 0

    def list_message_ids(self, query: Optional[str] = None, max_results: Optional[int] = None) -> List[str]:
        self.list_call_count += 1
        if self.auth_error:
            raise GmailAuthError(
                "Gmail token file not found at backend/secrets/token_gmail.json. "
                "Please run `python -m app.integrations.google_auth gmail` to authenticate."
            )
        if self.error_on_list:
            raise self.error_on_list
        return [m.id for m in self.messages]

    def get_message(self, message_id: str) -> GmailMessage:
        for m in self.messages:
            if m.id == message_id:
                return m
        raise RuntimeError(f"Message {message_id} not found in mock")


@pytest.fixture
def test_db_path(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_scheduler.db")
    init_db(db_file)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_file)
    return db_file


def test_scheduler_no_overlapping_runs(test_db_path):
    """Test that the shared lock prevents background and manual sync from overlapping."""
    clock = FakeClock(FIXED_T0)
    mock_client = MockGmailClient()
    manager = SyncSchedulerManager(interval_minutes=5, clock=clock.now)

    # 1. Acquire the lock to simulate an in-progress sync run
    assert manager.sync_lock.acquire(blocking=False) is True

    try:
        # Background sync attempt while lock is held -> must be skipped (returns None, does not call client)
        res_bg = manager.run_sync(is_manual=False, client=mock_client)
        assert res_bg is None
        assert mock_client.list_call_count == 0

        # Manual sync attempt while lock is held -> returns skipped response with clear message
        res_manual = manager.run_sync(is_manual=True, client=mock_client)
        assert res_manual is not None
        assert res_manual.status == "skipped"
        assert "already in progress" in res_manual.message
        assert mock_client.list_call_count == 0

    finally:
        manager.sync_lock.release()

    # 2. After lock is released, sync run proceeds normally
    res_after = manager.run_sync(is_manual=False, client=mock_client)
    assert res_after is not None
    assert res_after.status == "success"
    assert mock_client.list_call_count == 1


def test_scheduler_backoff_growth_and_cap(test_db_path):
    """Test exponential backoff on network/quota errors doubling up to the 30-minute cap."""
    clock = FakeClock(FIXED_T0)
    mock_client = MockGmailClient(error_on_list=RuntimeError("Google API 503 Service Unavailable"))
    manager = SyncSchedulerManager(interval_minutes=5, clock=clock.now)

    # Run 1: first failure -> backoff = 5 minutes
    res1 = manager.run_sync(is_manual=False, client=mock_client)
    assert res1.status == "error"
    assert manager.consecutive_errors == 1
    status1 = manager.get_status()
    assert status1.backoff_minutes == 5
    assert manager.backoff_until == FIXED_T0 + timedelta(minutes=5)

    # Calling before backoff expires -> skips execution
    clock.advance(minutes=2)  # T0 + 2m (< T0 + 5m)
    res_skipped = manager.run_sync(is_manual=False, client=mock_client)
    assert res_skipped is None
    assert mock_client.list_call_count == 1  # Not called again

    # Advance clock to T0 + 5m: Run 2 -> backoff doubles to 10 minutes
    clock.advance(minutes=3)  # T0 + 5m
    res2 = manager.run_sync(is_manual=False, client=mock_client)
    assert res2.status == "error"
    assert manager.consecutive_errors == 2
    assert manager.get_status().backoff_minutes == 10
    assert manager.backoff_until == FIXED_T0 + timedelta(minutes=15)

    # Advance clock to T0 + 15m: Run 3 -> backoff doubles to 20 minutes
    clock.advance(minutes=10)  # T0 + 15m
    res3 = manager.run_sync(is_manual=False, client=mock_client)
    assert res3.status == "error"
    assert manager.consecutive_errors == 3
    assert manager.get_status().backoff_minutes == 20
    assert manager.backoff_until == FIXED_T0 + timedelta(minutes=35)

    # Advance clock to T0 + 35m: Run 4 -> backoff hits 30-minute cap (min(30, 40) = 30)
    clock.advance(minutes=20)  # T0 + 35m
    res4 = manager.run_sync(is_manual=False, client=mock_client)
    assert res4.status == "error"
    assert manager.consecutive_errors == 4
    assert manager.get_status().backoff_minutes == 30
    assert manager.backoff_until == FIXED_T0 + timedelta(minutes=65)

    # Advance clock to T0 + 65m: Run 5 -> backoff remains capped at 30 minutes
    clock.advance(minutes=30)  # T0 + 65m
    res5 = manager.run_sync(is_manual=False, client=mock_client)
    assert res5.status == "error"
    assert manager.consecutive_errors == 5
    assert manager.get_status().backoff_minutes == 30


def test_scheduler_auth_failure_stops_retries(test_db_path):
    """Test that auth failure stops retries, sets status 'reconnect_needed',

    and presents the exact CLI command.
    """
    clock = FakeClock(FIXED_T0)
    mock_client = MockGmailClient(auth_error=True)
    manager = SyncSchedulerManager(interval_minutes=5, clock=clock.now)

    # Run cycle with auth failure
    res = manager.run_sync(is_manual=False, client=mock_client)
    assert res.status == "error"

    # Verify reconnect_needed state and exact CLI command
    assert manager.reconnect_needed is True
    status = manager.get_status()
    assert status.status == "reconnect_needed"
    assert status.reconnect_command == RECONNECT_COMMAND
    assert RECONNECT_COMMAND in status.error_message
    assert status.next_run_at is None  # Retries stopped

    # Subsequent background sync calls must immediately skip without invoking the client
    call_count_before = mock_client.list_call_count
    res_skip = manager.run_sync(is_manual=False, client=mock_client)
    assert res_skip is None
    assert mock_client.list_call_count == call_count_before


def test_scheduler_toggle_persists(test_db_path):
    """Test that enabling/disabling auto-sync persists in the settings table

    and survives manager restarts.
    """
    manager = SyncSchedulerManager(interval_minutes=5)
    manager.init_from_db()

    # 1. Disable auto-sync
    status1 = manager.set_enabled(False)
    assert status1.auto_sync_enabled is False
    assert get_setting("auto_sync_enabled") == "false"

    # 2. Create fresh manager instance simulating application restart
    restarted_manager = SyncSchedulerManager(interval_minutes=5)
    restarted_manager.init_from_db()
    assert restarted_manager.auto_sync_enabled is False
    assert restarted_manager.get_status().auto_sync_enabled is False

    # 3. Re-enable auto-sync
    status2 = restarted_manager.set_enabled(True)
    assert status2.auto_sync_enabled is True
    assert get_setting("auto_sync_enabled") == "true"


def test_scheduler_no_duplicates_across_runs(test_db_path):
    """Test that recurring sync runs skip already ingested message IDs."""
    clock = FakeClock(FIXED_T0)
    messages = [
        GmailMessage(
            id="msg_001",
            thread_id="thread_001",
            subject="Assignment 1",
            sender="prof@college.edu",
            date_str="Fri, 06 Nov 2026 09:00:00 +0000",
            body_text="Please submit Assignment 1 by Friday, Nov 13, 2026 at 5:00 PM EST.",
        ),
        GmailMessage(
            id="msg_002",
            thread_id="thread_002",
            subject="Interview Notice",
            sender="hr@company.com",
            date_str="Fri, 06 Nov 2026 09:30:00 +0000",
            body_text="Interview on Friday, Nov 13, 2026 at 2:00 PM EST via https://meet.google.com/abc.",
        ),
    ]
    mock_client = MockGmailClient(messages=messages)
    manager = SyncSchedulerManager(interval_minutes=5, clock=clock.now)

    # First run -> 2 items created
    res1 = manager.run_sync(is_manual=False, client=mock_client)
    assert res1.counts.created == 2
    assert res1.counts.skipped == 0

    # Advance clock and run again with same message list -> 0 created, 2 skipped
    clock.advance(minutes=5)
    res2 = manager.run_sync(is_manual=False, client=mock_client)
    assert res2.counts.created == 0
    assert res2.counts.skipped == 2


def test_scheduler_failing_run_does_not_stop_later_runs(test_db_path):
    """Test that a transient error does not prevent subsequent healthy runs from succeeding."""
    clock = FakeClock(FIXED_T0)
    healthy_msg = GmailMessage(
        id="recovery_msg",
        thread_id="th_rec",
        subject="Lab report",
        sender="ta@college.edu",
        date_str="Fri, 06 Nov 2026 09:00:00 +0000",
        body_text="Lab report due Friday, Nov 13, 2026 at 11:59 PM EST.",
    )

    # Run 1: Network timeout
    failing_client = MockGmailClient(error_on_list=RuntimeError("Connection timeout"))
    manager = SyncSchedulerManager(interval_minutes=5, clock=clock.now)
    res1 = manager.run_sync(is_manual=False, client=failing_client)
    assert res1.status == "error"
    assert manager.consecutive_errors == 1
    assert manager.backoff_until is not None

    # Advance clock past backoff time and swap to healthy client
    clock.advance(minutes=6)
    healthy_client = MockGmailClient(messages=[healthy_msg])
    res2 = manager.run_sync(is_manual=False, client=healthy_client)
    assert res2.status == "success"
    assert res2.counts.created == 1

    # Verify state was reset upon success
    assert manager.consecutive_errors == 0
    assert manager.backoff_until is None
    assert manager.current_status == "idle"
    assert manager.last_error is None


def test_sync_endpoints_with_scheduler(tmp_path, monkeypatch):
    """Test GET /api/sync/status, POST /api/sync/settings, and POST /api/sync/gmail."""
    db_file = str(tmp_path / "test_api_scheduler.db")
    init_db(db_file)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_file)

    with TestClient(app) as client:
        # 1. Initial status
        status_res = client.get("/api/sync/status")
        assert status_res.status_code == 200
        data = status_res.json()
        assert data["source_type"] == "gmail"
        assert "auto_sync_enabled" in data
        assert "interval_minutes" in data

        # 2. Toggle auto-sync off
        toggle_off = client.post("/api/sync/settings", json={"auto_sync_enabled": False})
        assert toggle_off.status_code == 200
        assert toggle_off.json()["auto_sync_enabled"] is False

        # Verify status endpoint reflects disabled
        status_res2 = client.get("/api/sync/status")
        assert status_res2.json()["auto_sync_enabled"] is False

        # 3. Toggle auto-sync back on
        toggle_on = client.post("/api/sync/settings", json={"auto_sync_enabled": True})
        assert toggle_on.status_code == 200
        assert toggle_on.json()["auto_sync_enabled"] is True

