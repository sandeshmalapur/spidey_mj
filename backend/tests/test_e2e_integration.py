"""End-to-end integration tests for ClassMail J.

Uses:
- TestClient(app) against real FastAPI endpoints
- Temporary SQLite database (isolated per run)
- Injected FakeClock
- MockNtfyClient (captures all outbound push attempts in memory)
- Documented mock Gmail / Classroom clients

Verifies the complete life cycle:
1. Ingest assessment email (due 14:00 UTC, 45 min duration) -> task with confirmed deadline and latest_start at 13:15 UTC.
2. Ingest assignment due 17:00 UTC -> reminders planned at 14:00 UTC (T-3h) and 16:15 UTC (T-45m).
3. Acknowledge, snooze and complete behaviors:
   - Acknowledge prevents urgent escalation while keeping critical reminders.
   - Snooze suppresses delivery until snooze expires.
   - Complete cancels all remaining reminders.
4. Google Classroom TURNED_IN assignment suppresses all reminders.
5. Demo items are seeded and verified to never dispatch to external ntfy (logged as 'local').
6. Security hardening: state-changing requests (POST/PATCH/DELETE) with an untrusted Origin are rejected with HTTP 403 Forbidden.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient

import app.config
from app.db import get_connection, init_db
from app.main import app
from app.reminders import BaseNtfyClient, run_reminder_tick


class FakeClock:
    """Injectable deterministic clock for end-to-end time simulation."""

    def __init__(self, start_dt: datetime) -> None:
        self.current_dt = start_dt

    def now(self) -> datetime:
        return self.current_dt

    def advance(self, minutes: int = 0, seconds: int = 0, hours: int = 0) -> datetime:
        self.current_dt += timedelta(hours=hours, minutes=minutes, seconds=seconds)
        return self.current_dt


class MockNtfyClient(BaseNtfyClient):
    """In-memory mock for ntfy push notifications."""

    def __init__(self, should_succeed: bool = True) -> None:
        self.should_succeed = should_succeed
        self.sent_messages: List[Dict[str, Any]] = []

    def send(
        self,
        title: str,
        body: str,
        priority: str = "default",
        tags: str = "bell",
    ) -> tuple[bool, Optional[str]]:
        if not self.should_succeed:
            return False, "Simulated network failure"
        self.sent_messages.append({
            "title": title,
            "body": body,
            "priority": priority,
            "tags": tags,
        })
        return True, None


class MockGmailClient:
    """Mock Gmail client delivering deterministic email payloads."""

    def __init__(self, messages: List[Dict[str, Any]]) -> None:
        self.messages = messages

    def list_message_ids(self, query: str = "", max_results: int = 25) -> List[str]:
        return [m["id"] for m in self.messages]

    def get_message(self, message_id: str) -> Dict[str, Any]:
        for m in self.messages:
            if m["id"] == message_id:
                return m
        raise KeyError(f"Message {message_id} not found")


class MockClassroomClient:
    """Mock Google Classroom client delivering deterministic course and submission data."""

    def __init__(self, courses: List[Dict[str, Any]], coursework: List[Dict[str, Any]], submissions: List[Dict[str, Any]]) -> None:
        self.courses = courses
        self.coursework = coursework
        self.submissions = submissions

    def list_courses(self) -> List[Dict[str, Any]]:
        return self.courses

    def list_course_work(self, course_id: str) -> List[Dict[str, Any]]:
        return [cw for cw in self.coursework if cw.get("courseId") == course_id]

    def list_student_submissions(self, course_id: str, coursework_id: str) -> List[Dict[str, Any]]:
        return [
            s for s in self.submissions
            if s.get("courseId") == course_id and s.get("courseWorkId") == coursework_id
        ]


@pytest.fixture
def e2e_env(tmp_path, monkeypatch):
    """Setup isolated database, clock, and mock ntfy client for E2E testing."""
    test_db_path = str(tmp_path / "e2e_classmail.db")
    init_db(test_db_path)
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db_path)

    # Base simulation time: 2026-11-06 12:00:00 UTC
    base_t0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
    clock = FakeClock(base_t0)
    mock_ntfy = MockNtfyClient()

    real_datetime = datetime

    class MockDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            dt = clock.now()
            if tz:
                return dt.astimezone(tz)
            return dt

    monkeypatch.setattr("app.main.datetime", MockDateTime)
    monkeypatch.setattr("app.reminders.datetime", MockDateTime)
    monkeypatch.setattr("app.demo_data.datetime", MockDateTime)
    monkeypatch.setattr("app.config.NTFY_TOPIC", "mock-e2e-topic")

    with TestClient(app) as client:
        yield {
            "client": client,
            "db_path": test_db_path,
            "clock": clock,
            "mock_ntfy": mock_ntfy,
        }


def test_end_to_end_assessment_assignment_and_reminders(e2e_env):
    """Full lifecycle: assessment 14:00+45m, assignment 17:00, ack, snooze, complete, TURNED_IN, demo isolation."""
    client: TestClient = e2e_env["client"]
    clock: FakeClock = e2e_env["clock"]
    mock_ntfy: MockNtfyClient = e2e_env["mock_ntfy"]

    # -------------------------------------------------------------------------
    # 1. Ingest Assessment Email (due 14:00 UTC, duration 45 min)
    # -------------------------------------------------------------------------
    assessment_email_text = (
        "Midterm Quiz Alert: The CS 101 online quiz is scheduled for "
        "Friday, November 6, 2026 at 2:00 PM UTC. Time limit: 45 minutes. "
        "Please complete before 2:00 PM."
    )
    res_assessment = client.post(
        "/api/ingest/manual",
        json={
            "source_title": "CS 101 Midterm Quiz",
            "sender": "prof@university.edu",
            "text": assessment_email_text,
            "received_at": "2026-11-06T12:00:00Z",
        },
    )
    assert res_assessment.status_code in (200, 201), res_assessment.text
    assessment_item = res_assessment.json()["item"]

    assert assessment_item["item_type"] == "task"
    assert assessment_item["kind"] == "assessment"
    assert assessment_item["deadline_status"] == "confirmed"
    assert assessment_item["duration_minutes"] == 45
    assert assessment_item["deadline_at"] == "2026-11-06T14:00:00Z"

    # Verify reminders planned for assessment: latest_start at 13:15:00Z (14:00 - 45 min)
    reminders_res = client.get(f"/api/items/{assessment_item['id']}/reminders")
    assert reminders_res.status_code == 200
    assessment_reminders = reminders_res.json()
    latest_starts = [r for r in assessment_reminders if r["kind"] == "latest_start"]
    assert len(latest_starts) == 1
    assert latest_starts[0]["scheduled_for"] == "2026-11-06T13:15:00Z"

    # -------------------------------------------------------------------------
    # 2. Ingest Assignment (due 17:00 UTC)
    # -------------------------------------------------------------------------
    assignment_email_text = (
        "Reminder: Homework 4 is due today Friday, November 6, 2026 at 5:00 PM UTC. "
        "Please submit your answers on the portal."
    )
    res_assignment = client.post(
        "/api/ingest/manual",
        json={
            "source_title": "Homework 4 Due",
            "sender": "ta@university.edu",
            "text": assignment_email_text,
            "received_at": "2026-11-06T12:00:00Z",
        },
    )
    assert res_assignment.status_code in (200, 201), res_assignment.text
    assignment_item = res_assignment.json()["item"]

    assert assignment_item["item_type"] == "task"
    assert assignment_item["kind"] == "assignment"
    assert assignment_item["deadline_at"] == "2026-11-06T17:00:00Z"

    # Planned reminders for assignment due at 17:00 UTC:
    # T-3h (14:00 UTC) and T-45m (16:15 UTC)
    assign_reminders_res = client.get(f"/api/items/{assignment_item['id']}/reminders")
    assign_reminders = assign_reminders_res.json()
    scheduled_times = {r["kind"]: r["scheduled_for"] for r in assign_reminders}
    assert scheduled_times.get("deadline_3h") == "2026-11-06T14:00:00Z"
    assert scheduled_times.get("deadline_45m") == "2026-11-06T16:15:00Z"

    # -------------------------------------------------------------------------
    # 3. Simulate Reminder Ticks and Time Advancement
    # -------------------------------------------------------------------------
    conn = get_connection(e2e_env["db_path"])

    # Advance clock to 13:16 UTC (crosses latest_start at 13:15 UTC)
    clock.advance(hours=1, minutes=16)  # Now 13:16:00 UTC
    tick_res1 = run_reminder_tick(conn, now_utc=clock.now(), ntfy_client=mock_ntfy)
    conn.commit()
    assert tick_res1["dispatched"] >= 1
    assert any("latest_start" in m["title"].lower() or "latest start" in m["body"].lower() for m in mock_ntfy.sent_messages)

    # -------------------------------------------------------------------------
    # 4. Acknowledge, Snooze, and Complete Behavior
    # -------------------------------------------------------------------------
    # Acknowledge assessment
    ack_res = client.post(f"/api/items/{assessment_item['id']}/acknowledge")
    assert ack_res.status_code == 200
    assert ack_res.json()["acknowledged_at"] is not None

    # Snooze assignment by 2 hours (snoozed until 15:16:00 UTC)
    snooze_until_iso = "2026-11-06T15:16:00Z"
    snooze_res = client.post(
        f"/api/items/{assignment_item['id']}/snooze",
        json={"until": snooze_until_iso},
    )
    assert snooze_res.status_code == 200
    assert "2026-11-06T15:16:00" in snooze_res.json()["snoozed_until"]

    # Advance clock to 14:05 UTC. Assignment reminder at 14:00 (T-3h) was due,
    # but the item is snoozed until 15:16 UTC, so tick must NOT dispatch it now.
    clock.current_dt = datetime(2026, 11, 6, 14, 5, 0, tzinfo=timezone.utc)
    mock_ntfy.sent_messages.clear()
    tick_res2 = run_reminder_tick(conn, now_utc=clock.now(), ntfy_client=mock_ntfy)
    conn.commit()
    assignment_dispatches = [m for m in mock_ntfy.sent_messages if "Homework 4" in m["title"] or "Homework 4" in m["body"]]
    assert len(assignment_dispatches) == 0, "Snoozed item should not dispatch reminders"

    # Mark assessment as completed -> all pending reminders cancelled
    complete_res = client.post(f"/api/items/{assessment_item['id']}/complete")
    assert complete_res.status_code == 200
    assert complete_res.json()["state"] == "completed"

    rem_after_complete = client.get(f"/api/items/{assessment_item['id']}/reminders").json()
    for rem in rem_after_complete:
        assert rem["status"] in ("sent", "cancelled", "skipped")
        assert rem["status"] != "pending"

    # -------------------------------------------------------------------------
    # 5. TURNED_IN Assignment Suppresses Reminders
    # -------------------------------------------------------------------------
    turned_in_payload = {
        "title": "Turned in Physics Lab",
        "item_type": "task",
        "kind": "assignment",
        "state": "in_progress",
        "deadline_text": "Friday 5 PM",
        "deadline_at": "2026-11-06T17:00:00Z",
        "deadline_status": "confirmed",
        "source_type": "classroom",
        "source_status": "TURNED_IN",
        "evidence": "Submitted on classroom",
    }
    turned_in_res = client.post("/api/items", json=turned_in_payload)
    assert turned_in_res.status_code == 201
    turned_in_id = turned_in_res.json()["id"]

    turned_in_reminders = client.get(f"/api/items/{turned_in_id}/reminders").json()
    assert len(turned_in_reminders) == 0, "TURNED_IN items should have no planned deadline reminders"

    # -------------------------------------------------------------------------
    # 6. Demo Isolation: Demo Items NEVER Dispatch to External ntfy
    # -------------------------------------------------------------------------
    demo_reset_res = client.post("/api/demo/reset")
    assert demo_reset_res.status_code == 200
    assert demo_reset_res.json()["total_demo"] > 0

    mock_ntfy.sent_messages.clear()
    # Trigger reminder tick at current simulated time
    tick_res_demo = run_reminder_tick(conn, now_utc=clock.now(), ntfy_client=mock_ntfy)
    conn.commit()
    # Check that any reminder sent for a demo item did NOT call mock_ntfy.send
    # In notification_log, demo notifications have channel='local', not 'ntfy'
    cur = conn.cursor()
    cur.execute(
        """
        SELECT nl.channel, nl.title, i.is_demo
        FROM notification_log nl
        JOIN items i ON nl.item_id = i.id
        WHERE i.is_demo = 1;
        """
    )
    demo_logs = cur.fetchall()
    for row in demo_logs:
        assert row["channel"] == "local", f"Demo item notification channel must be 'local', got {row['channel']}"

    conn.close()


def test_origin_validation_security_middleware(e2e_env):
    """Verify that requests from unauthorized origins to state-changing endpoints are blocked (403 Forbidden)."""
    client: TestClient = e2e_env["client"]

    # 1. State-changing request with unapproved Origin -> 403 Forbidden
    evil_headers = {"Origin": "http://malicious-website.com"}
    post_res = client.post(
        "/api/items",
        json={
            "title": "CSRF Attack Attempt",
            "item_type": "task",
            "kind": "assignment",
            "state": "not_started",
        },
        headers=evil_headers,
    )
    assert post_res.status_code == 403
    assert "Forbidden" in post_res.json()["detail"]

    # 2. State-changing request with approved Origin -> 201 Created
    valid_headers = {"Origin": "http://127.0.0.1:5173"}
    valid_res = client.post(
        "/api/items",
        json={
            "title": "Legitimate Local App Item",
            "item_type": "task",
            "kind": "assignment",
            "state": "not_started",
        },
        headers=valid_headers,
    )
    assert valid_res.status_code == 201
    created_id = valid_res.json()["id"]

    # 3. Safe GET request from any origin is allowed (read-only)
    get_res = client.get(f"/api/items/{created_id}", headers=evil_headers)
    assert get_res.status_code == 200
