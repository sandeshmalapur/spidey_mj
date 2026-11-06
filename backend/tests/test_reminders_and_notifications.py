"""Tests for deterministic reminders and notification system in ClassMail J.

Covers:
1. Schedule creation (urgent, important, confirmed deadline T-24h/T-3h, ambiguous confirmation)
2. Replan on deadline change (cancels obsolete, schedules new)
3. No send after completion or acknowledge (terminal cancellation and escalation stop)
4. Snooze deferral (reschedules to snoozed_until)
5. Quiet-hours deferral and morning summary collapse
6. Frequency-cap collapse (excess items over MAX_NOTIFICATIONS_PER_HOUR collapsed into one summary)
7. Deduplication across repeated ticks (unique dedupe_key prevents duplication)
8. Bounded escalation cap (URGENT_MAX_REPEATS)
9. Ntfy failure falls back to a local log without crashing scheduler
10. Minimal text contains no evidence or sender
11. Demo items are never sent to ntfy (logged as channel 'local')
12. Past event kinds (interview/meeting/event) get no reminders
13. Time-based urgency crossing triggers once during reminder tick
14. API endpoints flow (/api/notifications, /api/notifications/status, /api/notifications/settings, /api/notifications/test)

NOTE: Unit tests use a FakeClock and a MockNtfyClient clearly documented as mocks;
unit tests do NOT prove real ntfy delivery.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

import app.config
from app.db import get_connection, init_db
from app.main import app
from app.models import DeadlineStatus, ItemKind, ItemState, Priority
from app.reminders import (
    BaseNtfyClient,
    dispatch_due_reminders,
    format_utc_iso,
    is_in_quiet_hours,
    parse_utc_iso,
    plan_or_replan_item_reminders,
    run_reminder_tick,
    send_test_notification,
)
from app.scheduler import SyncSchedulerManager

FIXED_T0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """Mock clock for deterministic scheduler time injection."""

    def __init__(self, start_dt: datetime) -> None:
        self.current_dt = start_dt

    def now(self) -> datetime:
        return self.current_dt

    def advance(self, minutes: int = 0, seconds: int = 0, hours: int = 0, days: int = 0) -> datetime:
        self.current_dt += timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return self.current_dt


class MockNtfyClient(BaseNtfyClient):
    """Documented mock ntfy client for unit testing.

    Unit tests do NOT prove real ntfy delivery.
    """

    def __init__(self, fail: bool = False, fail_message: str = "Mock connection timeout") -> None:
        self.fail = fail
        self.fail_message = fail_message
        self.sent_messages: List[Dict[str, Any]] = []

    def send(
        self,
        title: str,
        body: str,
        priority: str = "default",
        tags: str = "bell",
    ) -> Tuple[bool, Optional[str]]:
        if self.fail:
            return False, self.fail_message
        self.sent_messages.append({
            "title": title,
            "body": body,
            "priority": priority,
            "tags": tags,
        })
        return True, None


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Provides a fresh isolated SQLite database for each test."""
    db_file = str(tmp_path / "test_reminders.db")
    init_db(db_file)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_file)
    monkeypatch.setattr("app.config.NOTIFY_DEMO_ITEMS", False)
    monkeypatch.setattr("app.config.NTFY_TOPIC", "test_mock_topic")
    monkeypatch.setattr("app.config.NTFY_INCLUDE_TITLE", False)
    conn = get_connection(db_file)
    yield conn
    conn.close()


def _insert_test_item(
    conn,
    title="Lab Report",
    priority="normal",
    deadline_status="none",
    deadline_at=None,
    kind="assignment",
    state="needs_review",
    is_demo=0,
    evidence="Class details",
    sender="Prof. Smith",
    acknowledged_at=None,
    snoozed_until=None,
) -> int:
    now_iso = format_utc_iso(FIXED_T0)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO items (
            title, item_type, kind, priority, extraction_confidence, state,
            deadline_status, deadline_at, is_demo, evidence, sender,
            acknowledged_at, snoozed_until, created_at, updated_at
        ) VALUES (
            ?, 'task', ?, ?, 1.0, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        );
        """,
        (
            title, kind, priority, state,
            deadline_status, deadline_at, is_demo, evidence, sender,
            acknowledged_at, snoozed_until, now_iso, now_iso,
        ),
    )
    conn.commit()
    return cur.lastrowid


def test_schedule_creation(test_db):
    """Test deterministic schedule creation for urgent, important, confirmed, and ambiguous items."""
    conn = test_db
    now = FIXED_T0

    # 1. Urgent item -> urgent_first scheduled immediately
    id_urg = _insert_test_item(conn, title="Urgent Task", priority="urgent")
    item_urg = dict(conn.execute("SELECT * FROM items WHERE id = ?", (id_urg,)).fetchone())
    plan_or_replan_item_reminders(item_urg, conn, now)

    rem_urg = conn.execute("SELECT * FROM reminders WHERE item_id = ?", (id_urg,)).fetchall()
    assert len(rem_urg) == 1
    assert rem_urg[0]["kind"] == "urgent_first"
    assert rem_urg[0]["status"] == "pending"
    assert rem_urg[0]["scheduled_for"] == format_utc_iso(now)

    # 2. Confirmed deadline 48 hours away -> T-24h and T-3h scheduled
    dl_48h = now + timedelta(hours=48)
    id_dl = _insert_test_item(
        conn,
        title="Due Assignment",
        priority="normal",
        deadline_status="confirmed",
        deadline_at=format_utc_iso(dl_48h),
    )
    item_dl = dict(conn.execute("SELECT * FROM items WHERE id = ?", (id_dl,)).fetchone())
    plan_or_replan_item_reminders(item_dl, conn, now)

    rem_dl = conn.execute("SELECT * FROM reminders WHERE item_id = ? ORDER BY scheduled_for ASC", (id_dl,)).fetchall()
    assert len(rem_dl) == 2
    assert rem_dl[0]["kind"] == "deadline_24h"
    assert rem_dl[0]["scheduled_for"] == format_utc_iso(dl_48h - timedelta(hours=24))
    assert rem_dl[1]["kind"] == "deadline_3h"
    assert rem_dl[1]["scheduled_for"] == format_utc_iso(dl_48h - timedelta(hours=3))

    # 3. Important item -> important_first scheduled immediately
    id_imp = _insert_test_item(conn, title="Important Task", priority="important")
    item_imp = dict(conn.execute("SELECT * FROM items WHERE id = ?", (id_imp,)).fetchone())
    plan_or_replan_item_reminders(item_imp, conn, now)

    rem_imp = conn.execute("SELECT * FROM reminders WHERE item_id = ?", (id_imp,)).fetchall()
    assert len(rem_imp) == 1
    assert rem_imp[0]["kind"] == "important_first"

    # 4. Ambiguous deadline item -> ambiguous_confirmation scheduled
    id_amb = _insert_test_item(conn, title="Ambiguous Task", deadline_status="ambiguous")
    item_amb = dict(conn.execute("SELECT * FROM items WHERE id = ?", (id_amb,)).fetchone())
    plan_or_replan_item_reminders(item_amb, conn, now)

    rem_amb = conn.execute("SELECT * FROM reminders WHERE item_id = ?", (id_amb,)).fetchall()
    assert len(rem_amb) == 1
    assert rem_amb[0]["kind"] == "ambiguous_confirmation"


def test_replan_on_deadline_change(test_db):
    """Test that a deadline change cancels obsolete reminders and creates replacements."""
    conn = test_db
    now = FIXED_T0
    dl_1 = now + timedelta(hours=30)
    dl_1_iso = format_utc_iso(dl_1)

    item_id = _insert_test_item(
        conn,
        title="Project Draft",
        deadline_status="confirmed",
        deadline_at=dl_1_iso,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    rems_1 = conn.execute("SELECT * FROM reminders WHERE item_id = ? AND status = 'pending'", (item_id,)).fetchall()
    assert len(rems_1) >= 1

    # Deadline is extended to 72 hours away
    dl_2 = now + timedelta(hours=72)
    dl_2_iso = format_utc_iso(dl_2)
    conn.execute("UPDATE items SET deadline_at = ? WHERE id = ?", (dl_2_iso, item_id))
    item["deadline_at"] = dl_2_iso

    plan_or_replan_item_reminders(item, conn, now)

    # Check that previous reminders were cancelled
    cancelled = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'cancelled' AND reason = 'deadline_changed'",
        (item_id,),
    ).fetchall()
    assert len(cancelled) >= 1

    # Check new pending reminders are set for dl_2
    new_pending = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending' ORDER BY scheduled_for ASC",
        (item_id,),
    ).fetchall()
    assert len(new_pending) == 2
    assert new_pending[0]["scheduled_for"] == format_utc_iso(dl_2 - timedelta(hours=24))
    assert new_pending[1]["scheduled_for"] == format_utc_iso(dl_2 - timedelta(hours=3))


def test_no_send_after_completion_or_acknowledge(test_db):
    """Test that completing an item or acknowledging stops reminders from sending."""
    conn = test_db
    now = FIXED_T0

    # Test Completion
    item_id = _insert_test_item(conn, title="Urgent Fix", priority="urgent")
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    # Mark complete
    conn.execute("UPDATE items SET state = 'completed', completed_at = ? WHERE id = ?", (format_utc_iso(now), item_id))
    item["state"] = "completed"
    item["completed_at"] = format_utc_iso(now)

    # Replan cancels pending
    plan_or_replan_item_reminders(item, conn, now)
    rem = conn.execute("SELECT * FROM reminders WHERE item_id = ?", (item_id,)).fetchone()
    assert rem["status"] == "cancelled"

    # Dispatch confirms nothing sent to ntfy
    mock_ntfy = MockNtfyClient()
    dispatched = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)
    assert dispatched == 0
    assert len(mock_ntfy.sent_messages) == 0


def test_snooze_deferral(test_db):
    """Test that snoozing an item reschedules pending reminders to snoozed_until."""
    conn = test_db
    now = FIXED_T0
    item_id = _insert_test_item(conn, title="Snoozeable Task", priority="urgent")
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    # Snooze until 2 hours in the future
    snooze_until = now + timedelta(hours=2)
    snooze_iso = format_utc_iso(snooze_until)
    conn.execute("UPDATE items SET snoozed_until = ? WHERE id = ?", (snooze_iso, item_id))

    mock_ntfy = MockNtfyClient()
    # At now, dispatch should defer due to snooze
    dispatched = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)
    assert dispatched == 0
    assert len(mock_ntfy.sent_messages) == 0

    # Reminder should be rescheduled to snooze_iso
    rem = conn.execute("SELECT * FROM reminders WHERE item_id = ?", (item_id,)).fetchone()
    assert rem["scheduled_for"] == snooze_iso
    assert rem["reason"] == "deferred_due_to_snooze"


def test_quiet_hours_deferral_and_summary(test_db, monkeypatch):
    """Test that reminders occurring in quiet hours are deferred and collapsed into morning summary."""
    conn = test_db
    monkeypatch.setattr("app.config.QUIET_HOURS_START", "22:00")
    monkeypatch.setattr("app.config.QUIET_HOURS_END", "07:00")
    monkeypatch.setattr("app.config.TIMEZONE", "UTC")
    monkeypatch.setattr("app.config.URGENT_BYPASS_QUIET", False)

    # Time is 23:30 UTC (inside quiet hours)
    quiet_now = datetime(2026, 11, 6, 23, 30, 0, tzinfo=timezone.utc)
    morning_end = datetime(2026, 11, 7, 7, 0, 0, tzinfo=timezone.utc)

    # Insert 2 urgent tasks during quiet hours
    i1 = _insert_test_item(conn, title="Task 1", priority="urgent")
    i2 = _insert_test_item(conn, title="Task 2", priority="urgent")
    plan_or_replan_item_reminders(dict(conn.execute("SELECT * FROM items WHERE id = ?", (i1,)).fetchone()), conn, quiet_now)
    plan_or_replan_item_reminders(dict(conn.execute("SELECT * FROM items WHERE id = ?", (i2,)).fetchone()), conn, quiet_now)

    mock_ntfy = MockNtfyClient()

    # 1. Dispatch at quiet_now -> deferred to 07:00
    dispatched_night = dispatch_due_reminders(conn, quiet_now, ntfy_client=mock_ntfy)
    assert dispatched_night == 0
    assert len(mock_ntfy.sent_messages) == 0

    rems = conn.execute("SELECT * FROM reminders WHERE status = 'pending'").fetchall()
    assert len(rems) == 2
    for r in rems:
        assert r["scheduled_for"] == format_utc_iso(morning_end)
        assert r["reason"] == "deferred_quiet_hours"

    # 2. Advance time to morning_end -> both deferred items collapsed into ONE morning summary
    dispatched_morning = dispatch_due_reminders(conn, morning_end, ntfy_client=mock_ntfy)
    assert dispatched_morning == 2
    # Only 1 summary sent to ntfy
    assert len(mock_ntfy.sent_messages) == 1
    assert mock_ntfy.sent_messages[0]["title"] == "ClassMail J: Morning Summary"
    assert "2 items need attention" in mock_ntfy.sent_messages[0]["body"]


def test_frequency_cap_collapse(test_db, monkeypatch):
    """Test that more than MAX_NOTIFICATIONS_PER_HOUR notifications collapse the excess into one summary."""
    conn = test_db
    monkeypatch.setattr("app.config.MAX_NOTIFICATIONS_PER_HOUR", 2)
    monkeypatch.setattr("app.config.QUIET_HOURS_START", "00:00")
    monkeypatch.setattr("app.config.QUIET_HOURS_END", "00:00")  # quiet hours disabled for test

    now = FIXED_T0
    # Create 4 urgent items
    item_ids = [_insert_test_item(conn, title=f"Urgent Task {i}", priority="urgent") for i in range(4)]
    for iid in item_ids:
        plan_or_replan_item_reminders(dict(conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()), conn, now)

    mock_ntfy = MockNtfyClient()
    dispatched = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)
    assert dispatched == 4

    # 2 sent individually + 1 collapsed summary for the excess 2 = 3 messages sent to ntfy
    assert len(mock_ntfy.sent_messages) == 3
    assert mock_ntfy.sent_messages[0]["title"] == "ClassMail J Alert"
    assert mock_ntfy.sent_messages[1]["title"] == "ClassMail J Alert"
    assert mock_ntfy.sent_messages[2]["title"] == "ClassMail J: Notification Summary"
    assert "2 additional notifications deferred" in mock_ntfy.sent_messages[2]["body"]


def test_dedupe_across_repeated_ticks(test_db):
    """Test that repeated ticks at the same time do not create duplicate reminders or resend."""
    conn = test_db
    now = FIXED_T0
    iid = _insert_test_item(conn, title="Single Task", priority="urgent")
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone())

    # Call plan twice
    plan_or_replan_item_reminders(item, conn, now)
    plan_or_replan_item_reminders(item, conn, now)

    rems = conn.execute("SELECT * FROM reminders WHERE item_id = ?", (iid,)).fetchall()
    assert len(rems) == 1

    # Dispatch twice
    mock_ntfy = MockNtfyClient()
    d1 = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)
    assert d1 == 1
    assert len(mock_ntfy.sent_messages) == 1

    # Second dispatch right away -> 0 sent
    d2 = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)
    assert d2 == 0
    assert len(mock_ntfy.sent_messages) == 1


def test_escalation_cap(test_db, monkeypatch):
    """Test that urgent escalation caps at URGENT_MAX_REPEATS."""
    conn = test_db
    monkeypatch.setattr("app.config.URGENT_MAX_REPEATS", 2)
    monkeypatch.setattr("app.config.URGENT_REPEAT_MINUTES", 30)

    clock = FakeClock(FIXED_T0)
    iid = _insert_test_item(conn, title="Escalating Issue", priority="urgent")
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone())

    mock_ntfy = MockNtfyClient()

    # Send 1: initial urgent notification (at T0)
    plan_or_replan_item_reminders(item, conn, clock.now())
    d1 = dispatch_due_reminders(conn, clock.now(), ntfy_client=mock_ntfy)
    assert d1 == 1
    assert len(mock_ntfy.sent_messages) == 1

    # Advance 30 mins -> escalation #1
    clock.advance(minutes=30)
    plan_or_replan_item_reminders(item, conn, clock.now())
    d2 = dispatch_due_reminders(conn, clock.now(), ntfy_client=mock_ntfy)
    assert d2 == 1
    assert len(mock_ntfy.sent_messages) == 2

    # Advance 30 mins -> escalation #2 (last allowed repeat for max_repeats=2)
    clock.advance(minutes=30)
    plan_or_replan_item_reminders(item, conn, clock.now())
    d3 = dispatch_due_reminders(conn, clock.now(), ntfy_client=mock_ntfy)
    assert d3 == 1
    assert len(mock_ntfy.sent_messages) == 3

    # Advance 30 mins -> cap reached (1 initial + 2 repeats = 3 total sends)
    clock.advance(minutes=30)
    plan_or_replan_item_reminders(item, conn, clock.now())
    d4 = dispatch_due_reminders(conn, clock.now(), ntfy_client=mock_ntfy)
    assert d4 == 0
    assert len(mock_ntfy.sent_messages) == 3  # No 4th send


def test_ntfy_failure_falls_back_to_local_log(test_db):
    """Test that ntfy network failure records in notification_log without crashing."""
    conn = test_db
    now = FIXED_T0
    iid = _insert_test_item(conn, title="Failing Ntfy Task", priority="urgent")
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    failing_ntfy = MockNtfyClient(fail=True, fail_message="503 Service Unavailable")
    dispatched = dispatch_due_reminders(conn, now, ntfy_client=failing_ntfy)
    assert dispatched == 1

    # Check notification_log
    log_entry = conn.execute("SELECT * FROM notification_log WHERE item_id = ?", (iid,)).fetchone()
    assert log_entry is not None
    assert log_entry["channel"] == "local"
    assert log_entry["outcome"] == "failed"
    assert "503 Service Unavailable" in log_entry["error"]


def test_minimal_text_contains_no_evidence_or_sender(test_db, monkeypatch):
    """Test that push notification body never contains email bodies, evidence, or sender names."""
    conn = test_db
    monkeypatch.setattr("app.config.NTFY_INCLUDE_TITLE", False)
    now = FIXED_T0

    sensitive_evidence = "SECRET_PASSWORD_12345: Please do not leak this content!"
    sensitive_sender = "confidential.hr@secretcompany.com"

    iid = _insert_test_item(
        conn,
        title="Sensitive Job Offer",
        priority="urgent",
        evidence=sensitive_evidence,
        sender=sensitive_sender,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    mock_ntfy = MockNtfyClient()
    dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)

    assert len(mock_ntfy.sent_messages) == 1
    msg = mock_ntfy.sent_messages[0]
    # Check title and body
    assert "SECRET" not in msg["title"]
    assert "SECRET" not in msg["body"]
    assert "secretcompany" not in msg["body"]
    assert "secretcompany" not in msg["title"]
    assert msg["title"] == "ClassMail J Alert"
    assert msg["body"] == "ClassMail J: 1 urgent item needs attention"


def test_demo_items_not_sent_to_ntfy(test_db, monkeypatch):
    """Test that items with is_demo=1 are recorded locally and not sent to ntfy."""
    conn = test_db
    monkeypatch.setattr("app.config.NOTIFY_DEMO_ITEMS", False)
    now = FIXED_T0

    iid = _insert_test_item(conn, title="Demo Item", priority="urgent", is_demo=1)
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    mock_ntfy = MockNtfyClient()
    dispatched = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)
    assert dispatched == 1
    # Never pushed to ntfy
    assert len(mock_ntfy.sent_messages) == 0

    # Logged to notification_log with channel 'local'
    log_entry = conn.execute("SELECT * FROM notification_log WHERE item_id = ?", (iid,)).fetchone()
    assert log_entry["channel"] == "local"
    assert log_entry["outcome"] == "logged"


def test_event_passed_gets_no_reminders(test_db):
    """Test that interview/meeting/event items whose start has passed receive no reminders."""
    conn = test_db
    now = FIXED_T0
    past_event_start = now - timedelta(hours=2)

    iid = _insert_test_item(
        conn,
        title="Passed Interview",
        kind=ItemKind.INTERVIEW.value,
        deadline_status="confirmed",
        deadline_at=format_utc_iso(past_event_start),
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    rems = conn.execute("SELECT * FROM reminders WHERE item_id = ?", (iid,)).fetchall()
    assert len(rems) == 0


def test_time_based_urgency_crossing_triggers_once(test_db, monkeypatch):
    """Test that crossing the 48h boundary during a tick recomputes priority and schedules urgent reminder."""
    conn = test_db
    monkeypatch.setattr("app.config.TIMEZONE", "UTC")
    monkeypatch.setattr("app.config.URGENT_BYPASS_QUIET", True)
    clock = FakeClock(FIXED_T0)

    # Deadline is 50 hours away -> initial priority is important (within 14 days, outside 48 hours)
    dl = clock.now() + timedelta(hours=50)
    iid = _insert_test_item(
        conn,
        title="Important Lab",
        priority="important",
        deadline_status="confirmed",
        deadline_at=format_utc_iso(dl),
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone())
    plan_or_replan_item_reminders(item, conn, clock.now())

    rems_init = conn.execute("SELECT * FROM reminders WHERE item_id = ? AND kind LIKE 'urgent%'", (iid,)).fetchall()
    assert len(rems_init) == 0

    # Advance clock by 5 hours: deadline is now 45 hours away (within 48 hours -> urgent!)
    clock.advance(hours=5)

    mock_ntfy = MockNtfyClient()
    tick_res = run_reminder_tick(conn, clock.now(), ntfy_client=mock_ntfy)
    assert tick_res["status"] == "success"
    assert tick_res["recomputed"] == 1

    # Check that priority became urgent in database
    updated_item = conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()
    assert updated_item["priority"] == Priority.URGENT.value

    # Check urgent reminder was dispatched
    urgent_rem = conn.execute("SELECT * FROM reminders WHERE item_id = ? AND kind = 'urgent_first'", (iid,)).fetchone()
    assert urgent_rem is not None
    assert urgent_rem["status"] == "sent"


def test_notification_api_endpoints_flow(test_db):
    """Test GET /api/notifications, POST /api/notifications/test, and status endpoints."""
    with TestClient(app) as client:
        # 1. Status check
        status_res = client.get("/api/notifications/status")
        assert status_res.status_code == 200
        st_data = status_res.json()
        assert st_data["ntfy_configured"] is True
        assert st_data["notifications_enabled"] is True
        assert "test_mock_topic" not in str(st_data)  # Never reveals topic!

        # 2. Trigger test notification
        test_res = client.post("/api/notifications/test")
        assert test_res.status_code == 200
        t_data = test_res.json()
        assert t_data["status"] in ("delivered", "failed")

        # 3. Notification log
        logs_res = client.get("/api/notifications")
        assert logs_res.status_code == 200
        logs = logs_res.json()
        assert len(logs) >= 1
        assert logs[0]["title"] == "ClassMail J Test"

        # 4. Settings toggle
        toggle_res = client.post("/api/notifications/settings", json={"notifications_enabled": False})
        assert toggle_res.status_code == 200
        assert toggle_res.json()["notifications_enabled"] is False

        # Status reflects toggle
        assert client.get("/api/notifications/status").json()["notifications_enabled"] is False
