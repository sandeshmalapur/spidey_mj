"""Google Classroom sync service for ClassMail J.

Synchronizes coursework (assignments) and announcements from Google Classroom.
Performs deduplication by (source_type='classroom', source_id),
maps UTC due dates accurately, tracks submission state, updates existing items
with item_events, recomputes priority, and isolates per-course permission errors.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import app.config
from app.db import get_connection, utc_now_iso
from app.extraction import OllamaProvider, RulesProvider, get_extraction_provider
from app.integrations.classroom_client import (
    BaseClassroomClient,
    ClassroomAuthError,
    ClassroomClient,
    ClassroomCourseWork,
)
from app.models import (
    ClassroomSyncCounts,
    ClassroomSyncResultResponse,
    DeadlineStatus,
    ItemKind,
    ItemState,
    ItemType,
    Priority,
)
from app.priority import recompute_priority

logger = logging.getLogger("classmail.classroom_sync")

RECONNECT_COMMAND = "python -m app.integrations.google_auth classroom"


def _format_utc_deadline(
    due_date: Optional[Dict[str, int]],
    due_time: Optional[Dict[str, int]],
) -> Tuple[DeadlineStatus, Optional[str], Optional[str]]:
    """Convert Classroom dueDate and dueTime to (status, deadline_at_utc, deadline_text).

    Semantics verified against Google Classroom v1 discovery schema:
    - dueDate is in UTC (year, month, day).
    - dueTime is in UTC (hours, minutes, seconds).
    If both exist -> confirmed UTC ISO timestamp and formatted text.
    If only dueDate exists -> ambiguous status, formatted date text, never invent a time.
    If neither exists -> none status, None, None.
    """
    if not due_date:
        return DeadlineStatus.NONE, None, None

    year = due_date.get("year", 0)
    month = due_date.get("month", 0)
    day = due_date.get("day", 0)

    if not (year and month and day):
        return DeadlineStatus.NONE, None, None

    if due_time:
        hours = due_time.get("hours", 0)
        minutes = due_time.get("minutes", 0)
        seconds = due_time.get("seconds", 0)

        # Google API allows hours=24 for 24:00:00 midnight
        if hours == 24:
            dt_utc = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1)
        else:
            dt_utc = datetime(year, month, day, hours, minutes, seconds, tzinfo=timezone.utc)

        utc_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        text = f"{year:04d}-{month:02d}-{day:02d} {hours:02d}:{minutes:02d} UTC"
        return DeadlineStatus.CONFIRMED, utc_iso, text

    # dueDate only -> ambiguous, keep date text, never invent a time
    text = f"{year:04d}-{month:02d}-{day:02d}"
    return DeadlineStatus.AMBIGUOUS, None, text


def _record_sync_history(
    conn: sqlite3.Connection,
    status: str,
    started_at: str,
    completed_at: str,
    counts: ClassroomSyncCounts,
    error_message: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO sync_history (
            source_type, started_at, completed_at, status,
            fetched, created, updated, skipped, errors, error_message
        ) VALUES ('classroom', ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            started_at,
            completed_at,
            status,
            counts.courses,
            counts.created,
            counts.updated,
            counts.skipped,
            counts.errors,
            error_message,
        ),
    )
    conn.commit()


def sync_classroom(
    client: Optional[BaseClassroomClient] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> ClassroomSyncResultResponse:
    """Synchronize coursework and announcements from Google Classroom (read-only).

    Isolates per-course errors, never reopens completed items, writes item_events
    on updates, and sets deadline_changed_at for reminder rescheduling.
    """
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True

    started_iso = utc_now_iso()
    counts = ClassroomSyncCounts()

    # 1. Instantiate Classroom client if not provided
    if client is None:
        try:
            client = ClassroomClient()
        except ClassroomAuthError as e:
            err_msg = str(e)
            _record_sync_history(conn, "reconnect_needed", started_iso, utc_now_iso(), counts, err_msg)
            return ClassroomSyncResultResponse(
                status="reconnect_needed",
                message=err_msg,
                counts=ClassroomSyncCounts(errors=1),
                error_message=err_msg,
            )
        except Exception as e:
            err_msg = f"Unexpected Classroom client initialization error: {e}"
            _record_sync_history(conn, "error", started_iso, utc_now_iso(), counts, err_msg)
            return ClassroomSyncResultResponse(
                status="error",
                message=err_msg,
                counts=ClassroomSyncCounts(errors=1),
                error_message=err_msg,
            )

    # 2. Query active courses
    try:
        courses = client.list_courses()
    except ClassroomAuthError as e:
        err_msg = str(e)
        _record_sync_history(conn, "reconnect_needed", started_iso, utc_now_iso(), counts, err_msg)
        return ClassroomSyncResultResponse(
            status="reconnect_needed",
            message=err_msg,
            counts=ClassroomSyncCounts(errors=1),
            error_message=err_msg,
        )
    except Exception as e:
        err_msg = f"Error fetching courses from Google Classroom: {e}"
        _record_sync_history(conn, "error", started_iso, utc_now_iso(), counts, err_msg)
        return ClassroomSyncResultResponse(
            status="error",
            message=err_msg,
            counts=ClassroomSyncCounts(errors=1),
            error_message=err_msg,
        )

    counts.courses = len(courses)
    if not courses:
        _record_sync_history(conn, "success", started_iso, utc_now_iso(), counts)
        return ClassroomSyncResultResponse(
            status="success",
            message="No active Google Classroom courses found.",
            counts=counts,
        )

    provider = get_extraction_provider()
    rules_provider = RulesProvider()
    ollama_processed_count = 0
    cur = conn.cursor()
    course_errors: List[str] = []

    try:
        for course in courses:
            course_id = course.id
            course_name = course.name

            # -------------------------------------------------------------
            # A. Process CourseWork (Assignments)
            # -------------------------------------------------------------
            try:
                course_works = client.list_course_work(course_id)
            except Exception as e:
                # Per-course error isolation: continue with other courses
                counts.errors += 1
                err_text = f"Course '{course_name}': error fetching coursework: {e}"
                logger.warning(err_text)
                course_errors.append(err_text)
                course_works = []

            for cw in course_works:
                source_id = f"cw:{course_id}:{cw.id}"
                dl_status, dl_at, dl_text = _format_utc_deadline(cw.due_date, cw.due_time)

                # Collect links (alternateLink + material URLs, http/https only)
                links: List[str] = []
                if cw.alternate_link and cw.alternate_link.startswith(("http://", "https://")):
                    links.append(cw.alternate_link)
                for mat in cw.materials:
                    if mat not in links:
                        links.append(mat)
                links_json = json.dumps(links)

                now_dt = datetime.now(timezone.utc)
                now_iso = utc_now_iso()
                received_iso = cw.creation_time or now_iso

                # Check if item already exists
                cur.execute(
                    "SELECT * FROM items WHERE source_type = 'classroom' AND source_id = ?;",
                    (source_id,),
                )
                existing_row = cur.fetchone()

                if existing_row is None:
                    # New assignment item
                    item_dict = {
                        "title": cw.title,
                        "item_type": ItemType.TASK.value,
                        "kind": ItemKind.ASSIGNMENT.value,
                        "deadline_status": dl_status.value,
                        "deadline_at": dl_at,
                        "state": ItemState.NOT_STARTED.value,
                        "created_at": now_iso,
                    }
                    priority, reason = recompute_priority(item_dict, now=now_dt)

                    cur.execute(
                        """
                        INSERT INTO items (
                            title, item_type, kind, summary, priority, priority_reason,
                            extraction_confidence, state, deadline_text, deadline_at, deadline_status,
                            links, source_type, source_id, source_title, sender, received_at,
                            evidence, source_status, is_demo, created_at, updated_at, extraction_provider
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?,
                            1.0, ?, ?, ?, ?,
                            ?, 'classroom', ?, ?, ?, ?,
                            ?, ?, 0, ?, ?, 'classroom'
                        );
                        """,
                        (
                            cw.title,
                            ItemType.TASK.value,
                            ItemKind.ASSIGNMENT.value,
                            cw.description[:300] if cw.description else None,
                            priority.value,
                            reason,
                            ItemState.NOT_STARTED.value,
                            dl_text,
                            dl_at,
                            dl_status.value,
                            links_json,
                            source_id,
                            course_name,
                            course_name,
                            received_iso,
                            cw.description or cw.title,
                            cw.submission_state,
                            now_iso,
                            now_iso,
                        ),
                    )
                    conn.commit()
                    counts.created += 1

                else:
                    # Existing item: check for updates
                    existing = dict(existing_row)
                    item_id = existing["id"]

                    # Never reopen completed items
                    is_completed = existing.get("state") == ItemState.COMPLETED.value

                    title_changed = existing["title"] != cw.title
                    deadline_changed = (
                        existing.get("deadline_at") != dl_at
                        or existing.get("deadline_text") != dl_text
                        or existing.get("deadline_status") != dl_status.value
                    )
                    submission_changed = existing.get("source_status") != cw.submission_state

                    if title_changed or deadline_changed or submission_changed:
                        update_fields: List[str] = []
                        update_vals: List[Any] = []

                        if title_changed:
                            conn.execute(
                                """
                                INSERT INTO item_events (item_id, at, kind, old_value, new_value, source_id)
                                VALUES (?, ?, 'classroom_title_changed', ?, ?, ?);
                                """,
                                (item_id, now_iso, existing["title"], cw.title, source_id),
                            )
                            update_fields.append("title = ?")
                            update_vals.append(cw.title)
                            existing["title"] = cw.title

                        if deadline_changed:
                            conn.execute(
                                """
                                INSERT INTO item_events (item_id, at, kind, old_value, new_value, source_id)
                                VALUES (?, ?, 'classroom_deadline_changed', ?, ?, ?);
                                """,
                                (
                                    item_id,
                                    now_iso,
                                    str(existing.get("deadline_at") or existing.get("deadline_text")),
                                    str(dl_at or dl_text),
                                    source_id,
                                ),
                            )
                            update_fields.extend(
                                ["deadline_at = ?", "deadline_text = ?", "deadline_status = ?", "deadline_changed_at = ?"]
                            )
                            update_vals.extend([dl_at, dl_text, dl_status.value, now_iso])
                            existing["deadline_at"] = dl_at
                            existing["deadline_text"] = dl_text
                            existing["deadline_status"] = dl_status.value

                        if submission_changed:
                            conn.execute(
                                """
                                INSERT INTO item_events (item_id, at, kind, old_value, new_value, source_id)
                                VALUES (?, ?, 'classroom_submission_changed', ?, ?, ?);
                                """,
                                (
                                    item_id,
                                    now_iso,
                                    str(existing.get("source_status")),
                                    str(cw.submission_state),
                                    source_id,
                                ),
                            )
                            update_fields.append("source_status = ?")
                            update_vals.append(cw.submission_state)
                            existing["source_status"] = cw.submission_state

                        # Recompute priority (maintaining completed status if closed)
                        new_p, new_reason = recompute_priority(existing, now=now_dt)
                        update_fields.extend(["priority = ?", "priority_reason = ?", "updated_at = ?"])
                        update_vals.extend([new_p.value, new_reason, now_iso, item_id])

                        conn.execute(
                            f"UPDATE items SET {', '.join(update_fields)} WHERE id = ?;",
                            update_vals,
                        )
                        conn.commit()
                        counts.updated += 1
                    else:
                        counts.skipped += 1

            # -------------------------------------------------------------
            # B. Process Announcements
            # -------------------------------------------------------------
            try:
                announcements = client.list_announcements(course_id)
            except Exception as e:
                counts.errors += 1
                err_text = f"Course '{course_name}': error fetching announcements: {e}"
                logger.warning(err_text)
                course_errors.append(err_text)
                announcements = []

            for ann in announcements:
                source_id = f"ann:{course_id}:{ann.id}"

                # Deduplication check
                cur.execute(
                    "SELECT id FROM items WHERE source_type = 'classroom' AND source_id = ?;",
                    (source_id,),
                )
                if cur.fetchone() is not None:
                    counts.skipped += 1
                    continue

                links = []
                if ann.alternate_link and ann.alternate_link.startswith(("http://", "https://")):
                    links.append(ann.alternate_link)
                for mat in ann.materials:
                    if mat not in links:
                        links.append(mat)
                links_json = json.dumps(links)

                now_dt = datetime.now(timezone.utc)
                now_iso = utc_now_iso()
                ann_dt = now_dt
                if ann.creation_time:
                    try:
                        clean_time = ann.creation_time.replace("Z", "+00:00")
                        ann_dt = datetime.fromisoformat(clean_time)
                    except Exception:
                        ann_dt = now_dt

                # Run through extraction provider (respecting sync cap)
                active_provider = provider
                if isinstance(provider, OllamaProvider):
                    if ollama_processed_count < app.config.OLLAMA_MAX_ITEMS_PER_SYNC:
                        active_provider = provider
                        ollama_processed_count += 1
                    else:
                        active_provider = rules_provider

                ext_res = active_provider.extract(
                    text=ann.text,
                    subject=f"{course_name} Announcement",
                    sender=course_name,
                    received_at=ann_dt,
                    timezone_str=app.config.TIMEZONE,
                    now=now_dt,
                )

                item_state = (
                    ItemState.NEEDS_REVIEW.value
                    if ext_res.item_type == ItemType.TASK
                    else ItemState.NOT_STARTED.value
                )

                item_dict = {
                    "title": ext_res.title,
                    "item_type": ext_res.item_type.value,
                    "kind": ext_res.kind.value,
                    "deadline_status": ext_res.deadline_status.value,
                    "deadline_at": ext_res.deadline_at,
                    "state": item_state,
                    "created_at": now_iso,
                }
                priority, reason = recompute_priority(item_dict, now=now_dt)

                cur.execute(
                    """
                    INSERT INTO items (
                        title, item_type, kind, summary, priority, priority_reason,
                        extraction_confidence, state, deadline_text, deadline_at, deadline_status,
                        links, source_type, source_id, source_title, sender, received_at,
                        evidence, is_demo, created_at, updated_at, extraction_provider
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, 'classroom', ?, ?, ?, ?,
                        ?, 0, ?, ?, ?
                    );
                    """,
                    (
                        ext_res.title,
                        ext_res.item_type.value,
                        ext_res.kind.value,
                        ext_res.summary,
                        priority.value,
                        reason,
                        ext_res.extraction_confidence,
                        item_state,
                        ext_res.deadline_text,
                        ext_res.deadline_at,
                        ext_res.deadline_status.value,
                        links_json,
                        source_id,
                        course_name,
                        course_name,
                        ann.creation_time or now_iso,
                        ann.text,
                        now_iso,
                        now_iso,
                        ext_res.extraction_provider,
                    ),
                )
                conn.commit()
                counts.created += 1

        overall_status = "error" if (counts.errors > 0 and counts.created == 0 and counts.updated == 0) else "success"
        err_msg = "; ".join(course_errors) if course_errors else None
        _record_sync_history(conn, overall_status, started_iso, utc_now_iso(), counts, err_msg)

        return ClassroomSyncResultResponse(
            status=overall_status,
            message=(
                f"Classroom sync finished: {counts.courses} courses, "
                f"{counts.created} created, {counts.updated} updated, "
                f"{counts.skipped} skipped"
                f"{f', {counts.errors} errors' if counts.errors > 0 else ''}."
            ),
            counts=counts,
            error_message=err_msg,
        )

    finally:
        if close_after:
            conn.close()

