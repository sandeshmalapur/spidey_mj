"""Tests for Google Classroom read-only integration in ClassMail J.

Covers:
1. UTC conversion for dueDate and dueTime
2. dueDate only -> ambiguous status, never invent a time
3. Deduplication across repeated sync runs
4. Update on due date/time change with item_events recording and deadline_changed_at flag
5. Completed items are never reopened
6. Per-course error isolation (permission error on one course does not stop others)
7. Scheduler includes Google Classroom sync
8. API endpoints flow (/api/sync/classroom and /api/sync/status)

NOTE: Unit tests use a MockClassroomClient clearly documented as a mock;
unit tests do NOT prove real Google Classroom works.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.db import get_connection, init_db
from app.integrations.classroom_client import (
    BaseClassroomClient,
    ClassroomAnnouncement,
    ClassroomAuthError,
    ClassroomCourse,
    ClassroomCourseWork,
)
from app.integrations.classroom_sync import _format_utc_deadline, sync_classroom
from app.main import app
from app.models import DeadlineStatus, ItemState
from app.scheduler import SyncSchedulerManager

FIXED_NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


class MockClassroomClient(BaseClassroomClient):
    """Documented mock Google Classroom API client for unit tests.

    Unit tests do NOT prove real Google Classroom works.
    """

    def __init__(
        self,
        courses: Optional[List[ClassroomCourse]] = None,
        course_works_by_course: Optional[Dict[str, List[ClassroomCourseWork]]] = None,
        announcements_by_course: Optional[Dict[str, List[ClassroomAnnouncement]]] = None,
        failing_courses: Optional[Dict[str, Exception]] = None,
        auth_error: bool = False,
    ) -> None:
        self.courses = courses or []
        self.course_works_by_course = course_works_by_course or {}
        self.announcements_by_course = announcements_by_course or {}
        self.failing_courses = failing_courses or {}
        self.auth_error = auth_error

    def list_courses(self) -> List[ClassroomCourse]:
        if self.auth_error:
            raise ClassroomAuthError(
                "Google Classroom token file not found. "
                "Please run `python -m app.integrations.google_auth classroom` to authenticate."
            )
        return self.courses

    def list_course_work(self, course_id: str) -> List[ClassroomCourseWork]:
        if course_id in self.failing_courses:
            raise self.failing_courses[course_id]
        return self.course_works_by_course.get(course_id, [])

    def list_announcements(self, course_id: str) -> List[ClassroomAnnouncement]:
        if course_id in self.failing_courses:
            raise self.failing_courses[course_id]
        return self.announcements_by_course.get(course_id, [])

    def get_student_submission_state(self, course_id: str, course_work_id: str) -> Optional[str]:
        return "TURNED_IN"


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_classroom.db")
    init_db(db_file)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_file)
    conn = get_connection(db_file)
    yield conn
    conn.close()


def test_classroom_utc_conversion():
    """Test accurate UTC conversion from Classroom dueDate and dueTime."""
    # Standard due date & time: 2026-11-13 at 17:30 UTC
    due_date = {"year": 2026, "month": 11, "day": 13}
    due_time = {"hours": 17, "minutes": 30, "seconds": 0}

    status, utc_iso, text = _format_utc_deadline(due_date, due_time)
    assert status == DeadlineStatus.CONFIRMED
    assert utc_iso == "2026-11-13T17:30:00Z"
    assert text == "2026-11-13 17:30 UTC"

    # Edge case: hours=24 (midnight at end of day)
    due_time_midnight = {"hours": 24, "minutes": 0, "seconds": 0}
    status_m, utc_m, _ = _format_utc_deadline(due_date, due_time_midnight)
    assert status_m == DeadlineStatus.CONFIRMED
    assert utc_m == "2026-11-14T00:00:00Z"


def test_classroom_date_only_is_ambiguous(test_db):
    """Test that dueDate without dueTime produces ambiguous status,

    preserves date text, and never invents a time.
    """
    conn = test_db
    course = ClassroomCourse(id="c1", name="Physics 101")
    cw = ClassroomCourseWork(
        id="cw_date_only",
        course_id="c1",
        title="Physics Lab 1",
        description="Submit lab writeup.",
        due_date={"year": 2026, "month": 11, "day": 13},
        due_time=None,  # No dueTime provided
    )
    client = MockClassroomClient(
        courses=[course],
        course_works_by_course={"c1": [cw]},
    )

    res = sync_classroom(client=client, conn=conn)
    assert res.status == "success"
    assert res.counts.created == 1

    cur = conn.cursor()
    cur.execute("SELECT * FROM items WHERE source_id = 'cw:c1:cw_date_only';")
    row = dict(cur.fetchone())

    assert row["deadline_status"] == "ambiguous"
    assert row["deadline_at"] is None  # Never invent a time!
    assert row["deadline_text"] == "2026-11-13"


def test_classroom_sync_dedupe(test_db):
    """Test deduplication: second run skips already ingested coursework and announcements."""
    conn = test_db
    course = ClassroomCourse(id="c1", name="CS 101")
    cw = ClassroomCourseWork(
        id="cw1",
        course_id="c1",
        title="Problem Set 1",
        due_date={"year": 2026, "month": 11, "day": 13},
        due_time={"hours": 23, "minutes": 59},
    )
    ann = ClassroomAnnouncement(
        id="ann1",
        course_id="c1",
        text="Welcome to CS 101! First lecture on Monday.",
    )
    client = MockClassroomClient(
        courses=[course],
        course_works_by_course={"c1": [cw]},
        announcements_by_course={"c1": [ann]},
    )

    # First run -> 2 items created
    res1 = sync_classroom(client=client, conn=conn)
    assert res1.counts.created == 2
    assert res1.counts.skipped == 0

    # Second run with same data -> 0 created, 2 skipped
    res2 = sync_classroom(client=client, conn=conn)
    assert res2.counts.created == 0
    assert res2.counts.skipped == 2

    # Verify database count
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items WHERE source_type = 'classroom';")
    assert cur.fetchone()[0] == 2


def test_classroom_update_due_date_with_event(test_db):
    """Test that modifying an assignment's due date updates the item,

    records an item_events row, and flags deadline_changed_at.
    """
    conn = test_db
    course = ClassroomCourse(id="c1", name="Math 201")
    cw_initial = ClassroomCourseWork(
        id="cw_due_update",
        course_id="c1",
        title="Math Homework 2",
        due_date={"year": 2026, "month": 11, "day": 10},
        due_time={"hours": 17, "minutes": 0},
    )
    client = MockClassroomClient(
        courses=[course],
        course_works_by_course={"c1": [cw_initial]},
    )

    # Run 1: initial ingestion
    sync_classroom(client=client, conn=conn)

    cur = conn.cursor()
    cur.execute("SELECT id, deadline_at FROM items WHERE source_id = 'cw:c1:cw_due_update';")
    row1 = cur.fetchone()
    item_id = row1[0]
    assert row1[1] == "2026-11-10T17:00:00Z"

    # Teacher moves deadline back 3 days
    cw_updated = ClassroomCourseWork(
        id="cw_due_update",
        course_id="c1",
        title="Math Homework 2",
        due_date={"year": 2026, "month": 11, "day": 13},
        due_time={"hours": 20, "minutes": 0},
    )
    client.course_works_by_course["c1"] = [cw_updated]

    # Run 2: update detection
    res2 = sync_classroom(client=client, conn=conn)
    assert res2.counts.updated == 1

    cur.execute("SELECT deadline_at, deadline_changed_at FROM items WHERE id = ?;", (item_id,))
    row2 = cur.fetchone()
    assert row2[0] == "2026-11-13T20:00:00Z"
    assert row2[1] is not None  # deadline_changed_at flagged

    # Check item_events table
    cur.execute("SELECT kind, old_value, new_value FROM item_events WHERE item_id = ?;", (item_id,))
    events = cur.fetchall()
    assert len(events) >= 1
    assert any(e[0] == "classroom_deadline_changed" for e in events)


def test_classroom_completed_not_reopened(test_db):
    """Test that updating an assignment whose item has already been marked completed

    never reopens it.
    """
    conn = test_db
    course = ClassroomCourse(id="c1", name="History 101")
    cw = ClassroomCourseWork(
        id="cw_comp",
        course_id="c1",
        title="Essay draft",
        due_date={"year": 2026, "month": 11, "day": 10},
        due_time={"hours": 12, "minutes": 0},
    )
    client = MockClassroomClient(
        courses=[course],
        course_works_by_course={"c1": [cw]},
    )

    sync_classroom(client=client, conn=conn)

    cur = conn.cursor()
    cur.execute(
        "UPDATE items SET state = 'completed', completed_at = '2026-11-06T10:00:00Z' WHERE source_id = 'cw:c1:cw_comp';"
    )
    conn.commit()

    # Title and due date change on Classroom
    cw_modified = ClassroomCourseWork(
        id="cw_comp",
        course_id="c1",
        title="Essay draft - Final Revised Version",
        due_date={"year": 2026, "month": 11, "day": 15},
        due_time={"hours": 18, "minutes": 0},
    )
    client.course_works_by_course["c1"] = [cw_modified]

    res2 = sync_classroom(client=client, conn=conn)
    assert res2.counts.updated == 1

    cur.execute("SELECT state, completed_at, title FROM items WHERE source_id = 'cw:c1:cw_comp';")
    row = cur.fetchone()
    assert row[0] == ItemState.COMPLETED.value  # NEVER reopened
    assert row[1] == "2026-11-06T10:00:00Z"
    assert row[2] == "Essay draft - Final Revised Version"


def test_classroom_per_course_error_isolation(test_db):
    """Test that a permission error on one course does not stop other courses from syncing."""
    conn = test_db
    c1 = ClassroomCourse(id="c1", name="Course 1")
    c2 = ClassroomCourse(id="c2", name="Course 2 (Forbidden)")
    c3 = ClassroomCourse(id="c3", name="Course 3")

    cw1 = ClassroomCourseWork(id="cw_c1", course_id="c1", title="Assignment 1")
    cw3 = ClassroomCourseWork(id="cw_c3", course_id="c3", title="Assignment 3")

    client = MockClassroomClient(
        courses=[c1, c2, c3],
        course_works_by_course={"c1": [cw1], "c3": [cw3]},
        failing_courses={"c2": RuntimeError("403 Forbidden: The caller does not have permission")},
    )

    res = sync_classroom(client=client, conn=conn)
    assert res.counts.courses == 3
    assert res.counts.created == 2  # Course 1 & Course 3 succeeded
    assert res.counts.errors >= 1   # Course 2 error isolated
    assert "Course 2 (Forbidden)" in res.error_message


def test_scheduler_includes_classroom(test_db):
    """Test that SyncSchedulerManager includes Google Classroom sync."""
    course = ClassroomCourse(id="c_sched", name="Sched Course")
    cw = ClassroomCourseWork(id="cw_sched", course_id="c_sched", title="Scheduler CW")
    mock_client = MockClassroomClient(
        courses=[course],
        course_works_by_course={"c_sched": [cw]},
    )

    manager = SyncSchedulerManager(
        interval_minutes=5,
        classroom_client_factory=lambda: mock_client,
    )

    # Run classroom sync via manager
    res = manager.run_classroom_sync(is_manual=False)
    assert res is not None
    assert res.counts.created == 1

    status = manager.get_status()
    assert status.classroom_status == "idle"
    assert status.classroom_last_synced_at is not None
    assert status.classroom_counts.created == 1


def test_classroom_api_endpoints_flow(tmp_path, monkeypatch):
    """Test POST /api/sync/classroom endpoint and status integration."""
    db_file = str(tmp_path / "test_api_classroom.db")
    init_db(db_file)
    monkeypatch.setattr("app.config.DATABASE_PATH", db_file)

    missing_token = str(tmp_path / "secrets" / "missing_classroom.json")
    monkeypatch.setattr("app.config.CLASSROOM_TOKEN_FILE", missing_token)

    with TestClient(app) as client:
        # Check initial status
        status_res = client.get("/api/sync/status")
        assert status_res.status_code == 200
        assert "classroom_status" in status_res.json()

        # Trigger classroom sync (missing token reports reconnect needed cleanly without 500)
        sync_res = client.post("/api/sync/classroom")
        assert sync_res.status_code == 200
        sync_data = sync_res.json()
        assert sync_data["status"] == "reconnect_needed"
        assert "python -m app.integrations.google_auth classroom" in sync_data["message"]
