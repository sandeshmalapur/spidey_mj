"""Comprehensive tests for assessment and submission reminder refinements.

Verifies:
1. Duration parsing (positive, negative, edge cases: 45 min, 1.5 hours, 1h 30m, range 5-600)
2. Latest-start calculation (deadline minus duration)
3. Heads-up reminder (latest_start minus ASSESSMENT_HEADSUP_MINUTES)
4. Past latest_start with time remaining (sends ONE immediate "latest start has passed, N minutes remain")
5. Past deadline -> nothing scheduled
6. Missing duration fallback (falls back to standard assignment offsets)
7. First-seen-inside-window sends ONLY the single nearest remaining reminder immediately (no backlog)
8. TURNED_IN -> suppresses/cancels all deadline reminders
9. Acknowledge keeps latest_start and final submission nudge (only stops urgent escalation)
10. Completed cancels all pending reminders
11. Quiet hours and frequency cap do not defer latest_start (time-critical bypass)
12. Deadline change replaces the schedule
13. Catch-up within grace is marked late ("(late, N min)") and logs delay_seconds
14. Beyond grace is skipped with 'catchup_grace_exceeded'
15. One-shot jobs rebuilt at startup in scheduler
16. Duration extraction table (table-driven test for varied phrasing)
17. Duration never becomes a deadline
18. Re-analyze preserves user-edited duration
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.config
from app.db import get_connection, init_db
from app.extraction import RulesProvider, normalize_text_for_parsing
from app.main import app, _reanalyze_item_logic
from app.models import DeadlineStatus, ItemKind, ItemState, ItemType, Priority
from app.reminders import (
    BaseNtfyClient,
    dispatch_due_reminders,
    format_utc_iso,
    parse_utc_iso,
    plan_or_replan_item_reminders,
    run_reminder_tick,
)
from app.scheduler import SyncSchedulerManager

FIXED_T0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start_dt: datetime) -> None:
        self.current_dt = start_dt

    def now(self) -> datetime:
        return self.current_dt

    def advance(self, minutes: int = 0, seconds: int = 0, hours: int = 0, days: int = 0) -> datetime:
        self.current_dt += timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return self.current_dt


class MockNtfyClient(BaseNtfyClient):
    def __init__(self, should_succeed: bool = True) -> None:
        self.should_succeed = should_succeed
        self.sent_messages: List[Dict[str, Any]] = []

    def send(
        self,
        title: str,
        body: str,
        priority: str = "default",
        tags: str = "bell",
    ) -> Tuple[bool, Optional[str]]:
        if not self.should_succeed:
            return False, "Simulated network failure"
        self.sent_messages.append({"title": title, "body": body, "priority": priority, "tags": tags})
        return True, None


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_assessments.db")
    init_db(db_file)
    conn = get_connection(db_file)
    yield conn
    conn.close()


def _insert_item(
    conn,
    title="Math Quiz",
    item_type="task",
    kind="assessment",
    priority="normal",
    deadline_status="confirmed",
    deadline_at=None,
    duration_minutes=None,
    state="needs_review",
    source_status=None,
    is_demo=0,
    evidence="Take quiz by due date",
    sender="Prof. Gauss",
    user_edited_fields="[]",
) -> int:
    now_iso = format_utc_iso(FIXED_T0)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO items (
            title, item_type, kind, priority, extraction_confidence, state,
            deadline_status, deadline_at, duration_minutes, source_status, is_demo, evidence, sender,
            user_edited_fields, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, 1.0, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?
        );
        """,
        (
            title, item_type, kind, priority, state,
            deadline_status, deadline_at, duration_minutes, source_status, is_demo, evidence, sender,
            user_edited_fields, now_iso, now_iso,
        ),
    )
    conn.commit()
    return cur.lastrowid


# -------------------------------------------------------------
# Test 1 & 16: Duration parsing and extraction table
# -------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected_duration",
    [
        ("The exam has a time limit of 45 minutes.", 45),
        ("Please complete the 45-minute quiz.", 45),
        ("Exam duration: 45 mins.", 45),
        ("This is a 1 hour exam.", 60),
        ("Estimated duration: 1.5 hours.", 90),
        ("Time limit: 1h 30m.", 90),
        ("duration: 45 min", 45),
        ("time limit 30 min", 30),
        # Edge cases & bounds
        ("takes 3 minutes", None),  # Below sanity lower bound 5
        ("takes 700 minutes", None),  # Above sanity upper bound 600
        ("no mention of time", None),
    ],
)
def test_duration_parsing(text, expected_duration):
    provider = RulesProvider()
    dur = provider._extract_duration(text)
    assert dur == expected_duration


# -------------------------------------------------------------
# Test 17: Duration never becomes a deadline
# -------------------------------------------------------------
def test_duration_never_becomes_a_deadline():
    provider = RulesProvider()
    res = provider.extract("Math Quiz. Duration: 45 min. Please study chapters 1-3.")
    assert res.duration_minutes == 45
    assert res.deadline_status == DeadlineStatus.NONE
    assert res.deadline_at is None
    assert res.deadline_text is None
    assert res.kind == ItemKind.ASSESSMENT
    assert res.item_type == ItemType.TASK


# -------------------------------------------------------------
# Test 2 & 3: Latest-start calculation and Heads-up
# -------------------------------------------------------------
def test_latest_start_and_headsup_calculation(test_db):
    conn = test_db
    now = FIXED_T0  # 12:00:00
    # Deadline at 14:00 (2:00 PM), Duration = 45 min -> latest_start at 13:15 (1:15 PM)
    # With default heads-up = 30 min -> heads-up at 12:45
    deadline = now + timedelta(hours=2)
    item_id = _insert_item(
        conn,
        title="Physics Exam",
        deadline_at=format_utc_iso(deadline),
        duration_minutes=45,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    rems = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending' ORDER BY scheduled_for ASC",
        (item_id,),
    ).fetchall()

    assert len(rems) == 2
    # First: heads-up at 12:45
    assert rems[0]["kind"] == "assessment_headsup"
    assert rems[0]["scheduled_for"] == format_utc_iso(now + timedelta(minutes=45))  # 12:45

    # Second: latest_start at 13:15
    assert rems[1]["kind"] == "latest_start"
    assert rems[1]["scheduled_for"] == format_utc_iso(now + timedelta(minutes=75))  # 13:15


# -------------------------------------------------------------
# Test 4: Past latest_start with time remaining
# -------------------------------------------------------------
def test_past_latest_start_with_time_remaining(test_db):
    conn = test_db
    now = FIXED_T0  # 12:00:00
    # Deadline is at 12:30 (30 min away). Duration is 45 min.
    # Latest start was at 11:45 (in the past)! But deadline (12:30) is in future.
    deadline = now + timedelta(minutes=30)
    item_id = _insert_item(
        conn,
        title="Timed Quiz",
        deadline_at=format_utc_iso(deadline),
        duration_minutes=45,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    rems = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending'",
        (item_id,),
    ).fetchall()
    assert len(rems) == 1
    assert rems[0]["kind"] == "latest_start_passed"
    assert rems[0]["scheduled_for"] == format_utc_iso(now)
    assert "30 minutes remain" in rems[0]["reason"]

    # Dispatch it
    mock_ntfy = MockNtfyClient()
    dispatched = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)
    assert dispatched == 1
    assert len(mock_ntfy.sent_messages) == 1
    msg = mock_ntfy.sent_messages[0]
    assert msg["priority"] == "urgent"
    assert "latest start has passed, 30 minutes remain" in msg["body"]

    # Repeated tick doesn't resend
    dispatched_again = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)
    assert dispatched_again == 0
    assert len(mock_ntfy.sent_messages) == 1


# -------------------------------------------------------------
# Test 5: Past deadline -> nothing scheduled
# -------------------------------------------------------------
def test_past_deadline_schedules_nothing(test_db):
    conn = test_db
    now = FIXED_T0
    past_deadline = now - timedelta(minutes=10)
    item_id = _insert_item(
        conn,
        title="Old Exam",
        deadline_at=format_utc_iso(past_deadline),
        duration_minutes=45,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    rems = conn.execute("SELECT * FROM reminders WHERE item_id = ?", (item_id,)).fetchall()
    assert len(rems) == 0


# -------------------------------------------------------------
# Test 6: Missing duration fallback to standard offsets
# -------------------------------------------------------------
def test_missing_duration_fallback(test_db):
    conn = test_db
    now = FIXED_T0
    deadline = now + timedelta(hours=30)
    item_id = _insert_item(
        conn,
        title="Assessment without duration",
        deadline_at=format_utc_iso(deadline),
        duration_minutes=None,  # No duration!
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    rems = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending' ORDER BY scheduled_for ASC",
        (item_id,),
    ).fetchall()
    # Falls back to assignment offsets [1440, 180, 45]
    assert len(rems) == 3
    assert rems[0]["kind"] == "deadline_24h"
    assert rems[1]["kind"] == "deadline_3h"
    assert rems[2]["kind"] == "deadline_45m"


# -------------------------------------------------------------
# Test 7: First seen inside window sends only one immediate reminder (no backlog)
# -------------------------------------------------------------
def test_first_seen_inside_window_sends_only_one(test_db):
    conn = test_db
    now = FIXED_T0  # 12:00:00
    # Deadline is in 2 hours (120 minutes away).
    # Offsets are [1440, 180, 45].
    # 1440m and 180m are in the past. 45m is in future (at 13:15).
    # Expected: ONLY the single nearest past reminder (180m) is scheduled immediately at now.
    # 1440m is skipped. 45m is scheduled at future time.
    deadline = now + timedelta(hours=2)
    item_id = _insert_item(
        conn,
        title="Late Discovered Assignment",
        kind="assignment",
        deadline_at=format_utc_iso(deadline),
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    pending = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending' ORDER BY scheduled_for ASC",
        (item_id,),
    ).fetchall()

    assert len(pending) == 2
    # 1. Single immediate reminder for 180m
    assert pending[0]["kind"] == "deadline_3h"
    assert pending[0]["scheduled_for"] == format_utc_iso(now)

    # 2. Future reminder for 45m
    assert pending[1]["kind"] == "deadline_45m"
    assert pending[1]["scheduled_for"] == format_utc_iso(deadline - timedelta(minutes=45))


# -------------------------------------------------------------
# Test 8: TURNED_IN suppresses all deadline reminders
# -------------------------------------------------------------
def test_turned_in_suppresses_deadline_reminders(test_db):
    conn = test_db
    now = FIXED_T0
    deadline = now + timedelta(hours=5)
    item_id = _insert_item(
        conn,
        title="Classroom Homework",
        kind="assignment",
        deadline_at=format_utc_iso(deadline),
        source_status="TURNED_IN",
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    pending = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending'",
        (item_id,),
    ).fetchall()
    assert len(pending) == 0


# -------------------------------------------------------------
# Test 9: Acknowledge stops escalation but keeps latest_start
# -------------------------------------------------------------
def test_acknowledge_keeps_latest_start(test_db):
    conn = test_db
    now = FIXED_T0
    deadline = now + timedelta(hours=3)
    item_id = _insert_item(
        conn,
        title="Urgent Assessment",
        priority="urgent",
        deadline_at=format_utc_iso(deadline),
        duration_minutes=45,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    # Acknowledge the item
    conn.execute(
        "UPDATE items SET acknowledged_at = ? WHERE id = ?",
        (format_utc_iso(now), item_id),
    )
    item["acknowledged_at"] = format_utc_iso(now)
    plan_or_replan_item_reminders(item, conn, now)

    # Urgent escalation should be cancelled, but latest_start remains pending!
    rems = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending'",
        (item_id,),
    ).fetchall()
    kinds = [r["kind"] for r in rems]
    assert "latest_start" in kinds
    assert not any(k.startswith("urgent_escalation") for k in kinds)


# -------------------------------------------------------------
# Test 10: Completed cancels all pending reminders
# -------------------------------------------------------------
def test_completed_cancels_all(test_db):
    conn = test_db
    now = FIXED_T0
    deadline = now + timedelta(hours=3)
    item_id = _insert_item(
        conn,
        title="Completed Assessment",
        deadline_at=format_utc_iso(deadline),
        duration_minutes=45,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    conn.execute("UPDATE items SET state = 'completed' WHERE id = ?", (item_id,))
    item["state"] = "completed"
    plan_or_replan_item_reminders(item, conn, now)

    pending = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending'",
        (item_id,),
    ).fetchall()
    assert len(pending) == 0


# -------------------------------------------------------------
# Test 11: Quiet hours and frequency cap do not defer latest_start
# -------------------------------------------------------------
def test_quiet_hours_and_frequency_cap_do_not_defer_latest_start(test_db, monkeypatch):
    conn = test_db
    monkeypatch.setattr("app.config.TIMEZONE", "UTC")
    monkeypatch.setattr("app.config.QUIET_HOURS_START", "22:00")
    monkeypatch.setattr("app.config.QUIET_HOURS_END", "07:00")
    monkeypatch.setattr("app.config.MAX_NOTIFICATIONS_PER_HOUR", 1)

    quiet_now = datetime(2026, 11, 6, 23, 0, 0, tzinfo=timezone.utc)
    # Latest start is scheduled at 23:00 (during quiet hours)
    deadline = quiet_now + timedelta(minutes=45)
    item_id = _insert_item(
        conn,
        title="Night Exam",
        deadline_at=format_utc_iso(deadline),
        duration_minutes=45,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, quiet_now)

    # Also log a previous notification to exhaust frequency cap
    conn.execute(
        """
        INSERT INTO notification_log (item_id, at, channel, title, body, outcome, error)
        VALUES (NULL, ?, 'ntfy', 'Previous', 'Already sent', 'delivered', NULL);
        """,
        (format_utc_iso(quiet_now - timedelta(minutes=10)),),
    )

    mock_ntfy = MockNtfyClient()
    dispatched = dispatch_due_reminders(conn, quiet_now, ntfy_client=mock_ntfy)

    # latest_start MUST NOT be deferred by quiet hours or rate limited
    assert dispatched == 1
    assert len(mock_ntfy.sent_messages) == 1
    msg = mock_ntfy.sent_messages[0]
    assert msg["priority"] == "urgent"
    assert "start your assessment now to finish before the deadline" in msg["body"]


# -------------------------------------------------------------
# Test 12: Deadline change replaces the schedule
# -------------------------------------------------------------
def test_deadline_change_replaces_schedule(test_db):
    conn = test_db
    now = FIXED_T0
    dl_1 = now + timedelta(hours=2)
    item_id = _insert_item(
        conn,
        title="Rescheduled Quiz",
        deadline_at=format_utc_iso(dl_1),
        duration_minutes=30,
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    plan_or_replan_item_reminders(item, conn, now)

    # Move deadline to 4 hours away
    dl_2 = now + timedelta(hours=4)
    conn.execute("UPDATE items SET deadline_at = ? WHERE id = ?", (format_utc_iso(dl_2), item_id))
    item["deadline_at"] = format_utc_iso(dl_2)
    plan_or_replan_item_reminders(item, conn, now)

    pending = conn.execute(
        "SELECT * FROM reminders WHERE item_id = ? AND status = 'pending' AND kind = 'latest_start'",
        (item_id,),
    ).fetchall()
    assert len(pending) == 1
    # latest_start is now at dl_2 - 30 min (now + 3.5h)
    assert pending[0]["scheduled_for"] == format_utc_iso(dl_2 - timedelta(minutes=30))


# -------------------------------------------------------------
# Test 13 & 14: Catch-up grace handling
# -------------------------------------------------------------
def test_catchup_grace_handling(test_db, monkeypatch):
    conn = test_db
    monkeypatch.setattr("app.config.CATCHUP_GRACE_MINUTES", 15)
    now = FIXED_T0

    # Item A: Overdue by 5 minutes (within 15 min grace) -> marked late
    item_a = _insert_item(conn, title="Within Grace", kind="assignment")
    sched_a = format_utc_iso(now - timedelta(minutes=5))
    conn.execute(
        """
        INSERT INTO reminders (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
        VALUES (?, 'deadline_45m', ?, 'pending', 'a:key', 'final_submission_nudge', ?);
        """,
        (item_a, sched_a, format_utc_iso(now)),
    )

    # Item B: Overdue by 25 minutes (exceeds 15 min grace) -> skipped
    item_b = _insert_item(conn, title="Exceeds Grace", kind="assignment")
    sched_b = format_utc_iso(now - timedelta(minutes=25))
    conn.execute(
        """
        INSERT INTO reminders (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
        VALUES (?, 'deadline_45m', ?, 'pending', 'b:key', 'final_submission_nudge', ?);
        """,
        (item_b, sched_b, format_utc_iso(now)),
    )

    mock_ntfy = MockNtfyClient()
    dispatched = dispatch_due_reminders(conn, now, ntfy_client=mock_ntfy)

    # Only Item A is sent
    assert dispatched == 1
    assert len(mock_ntfy.sent_messages) == 1
    assert "(late, 5 min)" in mock_ntfy.sent_messages[0]["body"]

    # Item B status is skipped with reason 'catchup_grace_exceeded'
    row_b = conn.execute("SELECT status, reason FROM reminders WHERE item_id = ?", (item_b,)).fetchone()
    assert row_b["status"] == "skipped"
    assert row_b["reason"] == "catchup_grace_exceeded"

    # Notification log recorded delay_seconds and scheduled_for
    log_a = conn.execute("SELECT * FROM notification_log WHERE item_id = ?", (item_a,)).fetchone()
    assert log_a["scheduled_for"] == sched_a
    assert log_a["delay_seconds"] >= 300


# -------------------------------------------------------------
# Test 15: One-shot jobs rebuilt at startup
# -------------------------------------------------------------
def test_oneshot_jobs_rebuilt_at_startup(test_db):
    conn = test_db
    now = FIXED_T0
    clock = FakeClock(now)

    # Insert pending time-critical reminder in future
    future_time = now + timedelta(minutes=20)
    item_id = _insert_item(conn, title="Upcoming Assessment", kind="assessment")
    conn.execute(
        """
        INSERT INTO reminders (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
        VALUES (?, 'latest_start', ?, 'pending', 'item:oneshot:test', 'start assessment', ?);
        """,
        (item_id, format_utc_iso(future_time), format_utc_iso(now)),
    )

    manager = SyncSchedulerManager(clock=clock.now)
    # Rebuild one-shot reminder jobs
    count = manager.rebuild_oneshot_reminder_jobs(conn=conn)
    assert count == 1
    jobs = manager.scheduler.get_jobs()
    assert any(j.id.startswith("reminder_oneshot_") for j in jobs)


# -------------------------------------------------------------
# Test 18: Re-analyze preserves user-edited duration
# -------------------------------------------------------------
def test_reanalyze_preserves_user_edited_duration(test_db):
    conn = test_db
    now = FIXED_T0
    # Create item with original text saying "30 minutes"
    item_id = _insert_item(
        conn,
        title="Edited Quiz",
        evidence="The quiz is 30 minutes long.",
        duration_minutes=60,  # User edited it to 60!
        user_edited_fields=json.dumps(["duration_minutes"]),
    )
    item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
    provider = RulesProvider()

    updated_dict, changes = _reanalyze_item_logic(conn, item, provider, now)
    # The duration_minutes should remain 60 because it's in user_edited_fields!
    assert updated_dict["duration_minutes"] == 60
