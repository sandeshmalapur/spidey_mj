"""Deterministic reminders and notification engine for ClassMail J.

Implements:
1. Pure/deterministic reminder planning with an injectable clock.
2. Escalation bounds (URGENT_MAX_REPEATS, URGENT_REPEAT_MINUTES).
3. Configurable deadline offsets (REMINDER_OFFSETS_ASSIGNMENT_MINUTES, REMINDER_OFFSETS_EVENT_MINUTES).
4. Assessment latest_start and heads-up reminders with duration calculation.
5. First-seen-inside-window immediate single reminder (no backlog).
6. Time-critical dispatch guards (latest_start and final submission nudge ignore quiet hours,
   ignore snooze, bypass frequency cap collapse, use ntfy priority urgent).
7. Catch-up grace handling (marked late within grace, skipped beyond grace) and delay logging.
8. Suppression on completed/cancelled states and source_status=TURNED_IN.
9. Ntfy publisher with minimal generic plain-text (no evidence text or sender names;
   never prints, logs, or returns NTFY_TOPIC).
"""

import logging
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import app.config
from app.db import get_connection, get_setting, set_setting, utc_now_iso
from app.models import DeadlineStatus, ItemKind, ItemState, Priority
from app.priority import recompute_priority

logger = logging.getLogger("classmail.reminders")


def parse_utc_iso(iso_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 string into a timezone-aware UTC datetime."""
    if not iso_str:
        return None
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def format_utc_iso(dt: datetime) -> str:
    """Format a datetime into a standard UTC ISO 8601 string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_offsets_list(val: str, default: List[int]) -> List[int]:
    """Parse a comma-separated list of integer offset minutes, sorted descending (max 3)."""
    try:
        offsets = [int(x.strip()) for x in val.split(",") if x.strip()]
        if not offsets:
            return default
        return sorted(offsets, reverse=True)[:3]
    except Exception:
        return default


def get_assignment_offsets() -> List[int]:
    raw = getattr(app.config, "REMINDER_OFFSETS_ASSIGNMENT_MINUTES", "1440,180,45")
    return parse_offsets_list(raw, [1440, 180, 45])


def get_event_offsets() -> List[int]:
    raw = getattr(app.config, "REMINDER_OFFSETS_EVENT_MINUTES", "1440,60,15")
    return parse_offsets_list(raw, [1440, 60, 15])


def offset_to_kind(minutes: int) -> str:
    """Map offset minutes to human-readable kind strings."""
    if minutes == 1440:
        return "deadline_24h"
    elif minutes == 180:
        return "deadline_3h"
    elif minutes == 60:
        return "deadline_1h"
    return f"deadline_{minutes}m"


def is_final_submission_nudge(kind: str, reason: Optional[str] = None) -> bool:
    """Check if reminder represents the final submission nudge."""
    if reason == "final_submission_nudge":
        return True
    return kind in ("deadline_45m", "deadline_15m")


def is_reminder_time_critical(kind: str, reason: Optional[str] = None) -> bool:
    """Check if a reminder is time-critical (latest_start or final submission nudge)."""
    if kind in ("latest_start", "latest_start_passed"):
        return True
    return is_final_submission_nudge(kind, reason)


def is_in_quiet_hours(
    now_utc: datetime,
    tz_name: Optional[str] = None,
    start_str: Optional[str] = None,
    end_str: Optional[str] = None,
) -> Tuple[bool, datetime]:
    """Evaluate whether now_utc falls inside user quiet hours.

    Returns:
        (is_quiet, quiet_hours_end_utc)
    """
    tz_str = tz_name or getattr(app.config, "TIMEZONE", "UTC")
    try:
        user_tz = ZoneInfo(tz_str)
    except Exception:
        user_tz = timezone.utc

    start_raw = start_str or getattr(app.config, "QUIET_HOURS_START", "22:00")
    end_raw = end_str or getattr(app.config, "QUIET_HOURS_END", "07:00")

    try:
        sh, sm = (int(x) for x in start_raw.split(":"))
        eh, em = (int(x) for x in end_raw.split(":"))
    except Exception:
        sh, sm = 22, 0
        eh, em = 7, 0

    local_dt = now_utc.astimezone(user_tz)
    local_time = local_dt.time()
    start_time = time(sh, sm)
    end_time = time(eh, em)

    # Overnight quiet hours (e.g., 22:00 to 07:00)
    if start_time > end_time:
        is_quiet = local_time >= start_time or local_time < end_time
        if is_quiet:
            if local_time >= start_time:
                # End is tomorrow at end_time
                next_end_date = local_dt.date() + timedelta(days=1)
            else:
                # End is today at end_time
                next_end_date = local_dt.date()
            end_local = datetime.combine(next_end_date, end_time, tzinfo=user_tz)
            end_utc = end_local.astimezone(timezone.utc)
            return True, end_utc
        else:
            return False, now_utc
    else:
        # Same day quiet hours (e.g. 13:00 to 15:00)
        is_quiet = start_time <= local_time < end_time
        if is_quiet:
            end_local = datetime.combine(local_dt.date(), end_time, tzinfo=user_tz)
            end_utc = end_local.astimezone(timezone.utc)
            return True, end_utc
        return False, now_utc


class BaseNtfyClient:
    """Base class for ntfy dispatch."""

    def send(
        self,
        title: str,
        body: str,
        priority: str = "default",
        tags: str = "bell",
    ) -> Tuple[bool, Optional[str]]:
        raise NotImplementedError


class NtfyClient(BaseNtfyClient):
    """Production ntfy client.

    Never logs or prints the topic or full URL.
    Enforces a 5-second timeout and ASCII-only headers.
    """

    def __init__(
        self,
        server: Optional[str] = None,
        topic: Optional[str] = None,
        timeout: float = 5.0,
    ) -> None:
        self.server = (server or getattr(app.config, "NTFY_SERVER", "https://ntfy.sh")).rstrip("/")
        self.topic = topic if topic is not None else getattr(app.config, "NTFY_TOPIC", "")
        self.timeout = timeout

    def send(
        self,
        title: str,
        body: str,
        priority: str = "default",
        tags: str = "bell",
    ) -> Tuple[bool, Optional[str]]:
        clean_topic = (self.topic or "").strip()
        if not clean_topic:
            return False, "ntfy unconfigured: NTFY_TOPIC is empty"

        url = f"{self.server}/{clean_topic}"

        # Clean ASCII-only headers per specification
        safe_title = title.encode("ascii", "ignore").decode("ascii") or "ClassMail J Alert"
        safe_priority = priority.encode("ascii", "ignore").decode("ascii") or "default"
        safe_tags = tags.encode("ascii", "ignore").decode("ascii") or "bell"

        headers = {
            "Title": safe_title,
            "Priority": safe_priority,
            "Tags": safe_tags,
            "Content-Type": "text/plain; charset=utf-8",
        }

        data = body.encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                status_code = response.getcode()
                if 200 <= status_code < 300:
                    return True, None
                return False, f"HTTP error {status_code}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            return False, f"Network error: {e.reason}"
        except Exception as e:
            return False, f"Request failed: {e}"


def get_notifications_enabled(conn: sqlite3.Connection) -> bool:
    """Read the runtime notifications toggle from SQLite settings (survives restarts)."""
    val = get_setting("notifications_enabled", conn=conn)
    if val is not None:
        return val.strip().lower() in ("true", "1", "yes")
    return getattr(app.config, "NOTIFICATIONS_ENABLED", True)


def set_notifications_enabled(enabled: bool, conn: sqlite3.Connection) -> None:
    """Persist the runtime notifications toggle."""
    set_setting("notifications_enabled", "true" if enabled else "false", conn=conn)


def _schedule_offsets(
    item_dict: Dict[str, Any],
    conn: sqlite3.Connection,
    now_utc: datetime,
    deadline_dt: datetime,
    offsets: List[int],
) -> None:
    """Schedule configurable deadline offsets for an item."""
    item_id = item_dict["id"]
    now_iso = format_utc_iso(now_utc)
    final_offset = offsets[-1] if offsets else 45

    if now_utc >= deadline_dt:
        return

    expected_times = {format_utc_iso(deadline_dt - timedelta(minutes=m)) for m in offsets}

    # Cancel pending deadline reminders that no longer match the current deadline
    placeholders = ",".join("?" for _ in expected_times)
    conn.execute(
        f"""
        UPDATE reminders
        SET status = 'cancelled', reason = 'deadline_changed'
        WHERE item_id = ? AND status = 'pending'
          AND (kind LIKE 'deadline%' OR kind IN ('latest_start', 'assessment_headsup', 'latest_start_passed'))
          AND scheduled_for NOT IN ({placeholders});
        """,
        (item_id, *expected_times),
    )

    # Check how many deadline reminders have already been sent for this item
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM reminders
        WHERE item_id = ? AND (kind LIKE 'deadline%' OR reason = 'final_submission_nudge') AND status = 'sent';
        """,
        (item_id,),
    )
    sent_deadline_count = cur.fetchone()[0]

    future_offsets = [m for m in offsets if (deadline_dt - timedelta(minutes=m)) > now_utc]
    past_offsets = [m for m in offsets if (deadline_dt - timedelta(minutes=m)) <= now_utc]

    # First seen inside window: send ONLY the single nearest remaining reminder immediately, never a backlog
    if sent_deadline_count == 0 and past_offsets:
        nearest_m = past_offsets[-1]  # Closest to now/deadline
        kind = offset_to_kind(nearest_m)
        is_final = (nearest_m == final_offset)
        reason = "final_submission_nudge" if is_final else f"{nearest_m} minutes before deadline"
        dedupe_key = f"{item_id}:{kind}:first_seen_immediate:{format_utc_iso(deadline_dt)}"
        conn.execute(
            """
            INSERT OR IGNORE INTO reminders
            (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?);
            """,
            (item_id, kind, now_iso, dedupe_key, reason, now_iso),
        )

    # Schedule future offsets at exact scheduled times
    for m in future_offsets:
        sched_dt = deadline_dt - timedelta(minutes=m)
        sched_iso = format_utc_iso(sched_dt)
        kind = offset_to_kind(m)
        is_final = (m == final_offset)
        reason = "final_submission_nudge" if is_final else f"{m} minutes before deadline"
        dedupe_key = f"{item_id}:{kind}:{sched_iso}"
        conn.execute(
            """
            INSERT OR IGNORE INTO reminders
            (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?);
            """,
            (item_id, kind, sched_iso, dedupe_key, reason, now_iso),
        )


def plan_or_replan_item_reminders(
    item_dict: Dict[str, Any],
    conn: sqlite3.Connection,
    now_utc: datetime,
) -> None:
    """Plan or replan deterministic reminders for a single item.

    Handles:
    - Suppression on completed/cancelled states or source_status='TURNED_IN'.
    - Assessment latest_start and heads-up with duration calculation.
    - Missing duration fallback to assignment offsets.
    - Configurable submission offsets (REMINDER_OFFSETS_ASSIGNMENT_MINUTES, REMINDER_OFFSETS_EVENT_MINUTES).
    - First-seen-inside-window single immediate reminder.
    - Acknowledge stops urgent escalation but NEVER cancels latest_start or final nudge.
    """
    item_id = item_dict["id"]
    now_iso = format_utc_iso(now_utc)

    # 1. Terminal states: cancel all pending reminders
    state = item_dict.get("state", "needs_review")
    completed_at = item_dict.get("completed_at")
    if state in (ItemState.COMPLETED.value, ItemState.CANCELLED.value, "completed", "cancelled") or completed_at:
        conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled', reason = 'item_completed_or_cancelled'
            WHERE item_id = ? AND status = 'pending';
            """,
            (item_id,),
        )
        return

    # 2. Turned in suppression (e.g. from Google Classroom)
    source_status = item_dict.get("source_status")
    if source_status and str(source_status).upper() == "TURNED_IN":
        conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled', reason = 'turned_in'
            WHERE item_id = ? AND status = 'pending'
              AND (kind LIKE 'deadline%' OR kind IN ('latest_start', 'assessment_headsup', 'latest_start_passed'));
            """,
            (item_id,),
        )
        return

    # 3. Event kinds whose start has passed get no reminders
    item_kind = item_dict.get("kind", "other")
    is_event = item_kind in (ItemKind.INTERVIEW.value, ItemKind.MEETING.value, ItemKind.EVENT.value, "interview", "meeting", "event")
    deadline_at_str = item_dict.get("deadline_at")
    deadline_dt = parse_utc_iso(deadline_at_str)

    if is_event and deadline_dt and deadline_dt <= now_utc:
        conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled', reason = 'event_start_passed'
            WHERE item_id = ? AND status = 'pending';
            """,
            (item_id,),
        )
        return

    # 4. Stop urgent escalation if acknowledged or started (in_progress)
    acknowledged = item_dict.get("acknowledged_at") is not None
    is_in_progress = state in (ItemState.IN_PROGRESS.value, "in_progress")
    if acknowledged or is_in_progress:
        conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled', reason = 'escalation_stopped_by_user'
            WHERE item_id = ? AND status = 'pending' AND kind LIKE 'urgent_escalation%';
            """,
            (item_id,),
        )

    # 5. Confirmed deadline scheduling
    deadline_status = item_dict.get("deadline_status", "none")
    if deadline_status in (DeadlineStatus.CONFIRMED.value, "confirmed") and deadline_dt:
        if item_kind in (ItemKind.ASSESSMENT.value, "assessment"):
            duration = item_dict.get("duration_minutes")
            if duration and duration > 0:
                latest_start_dt = deadline_dt - timedelta(minutes=duration)
                latest_start_iso = format_utc_iso(latest_start_dt)

                # Cancel previous pending deadline reminders if time changed
                conn.execute(
                    """
                    UPDATE reminders
                    SET status = 'cancelled', reason = 'deadline_or_duration_changed'
                    WHERE item_id = ? AND status = 'pending'
                      AND (kind IN ('latest_start', 'assessment_headsup', 'latest_start_passed') OR kind LIKE 'deadline%')
                      AND scheduled_for != ?;
                    """,
                    (item_id, latest_start_iso),
                )

                if latest_start_dt >= now_utc:
                    dedupe_key = f"{item_id}:latest_start:{latest_start_iso}"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO reminders
                        (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
                        VALUES (?, 'latest_start', ?, 'pending', ?, 'start assessment before deadline', ?);
                        """,
                        (item_id, latest_start_iso, dedupe_key, now_iso),
                    )

                    headsup_min = getattr(app.config, "ASSESSMENT_HEADSUP_MINUTES", 30)
                    if headsup_min > 0:
                        headsup_dt = latest_start_dt - timedelta(minutes=headsup_min)
                        if headsup_dt > now_utc:
                            headsup_iso = format_utc_iso(headsup_dt)
                            h_dedupe = f"{item_id}:assessment_headsup:{headsup_iso}"
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO reminders
                                (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
                                VALUES (?, 'assessment_headsup', ?, 'pending', ?, 'assessment heads-up', ?);
                                """,
                                (item_id, headsup_iso, h_dedupe, now_iso),
                            )
                elif now_utc < deadline_dt:
                    # latest_start has passed, but deadline has not
                    remaining_min = max(1, int((deadline_dt - now_utc).total_seconds() / 60))
                    dl_iso = format_utc_iso(deadline_dt)
                    dedupe_key = f"{item_id}:latest_start_passed:{dl_iso}"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO reminders
                        (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
                        VALUES (?, 'latest_start_passed', ?, 'pending', ?, ?, ?);
                        """,
                        (item_id, now_iso, dedupe_key, f"{remaining_min} minutes remain", now_iso),
                    )
                else:
                    # Deadline has passed -> send nothing
                    pass
            else:
                # Assessment without duration: fall back to assignment offsets
                _schedule_offsets(item_dict, conn, now_utc, deadline_dt, get_assignment_offsets())
        else:
            if is_event:
                offsets = get_event_offsets()
            else:
                offsets = get_assignment_offsets()
            _schedule_offsets(item_dict, conn, now_utc, deadline_dt, offsets)
    else:
        conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled', reason = 'deadline_not_confirmed'
            WHERE item_id = ? AND status = 'pending'
              AND (kind LIKE 'deadline%' OR kind IN ('latest_start', 'assessment_headsup', 'latest_start_passed'));
            """,
            (item_id,),
        )

    # 6. Ambiguous deadline confirmation
    if deadline_status in (DeadlineStatus.AMBIGUOUS.value, "ambiguous"):
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM reminders WHERE item_id = ? AND kind = 'ambiguous_confirmation';",
            (item_id,),
        )
        if cur.fetchone()[0] == 0:
            dedupe_key = f"{item_id}:ambiguous_confirmation:{now_iso}"
            conn.execute(
                """
                INSERT OR IGNORE INTO reminders
                (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
                VALUES (?, 'ambiguous_confirmation', ?, 'pending', ?, 'needs your confirmation', ?);
                """,
                (item_id, now_iso, dedupe_key, now_iso),
            )

    # 7. Priority-driven notifications
    eff_priority = item_dict.get("priority_override") or item_dict.get("priority") or "normal"

    if eff_priority in (Priority.URGENT.value, "urgent"):
        if not acknowledged and not is_in_progress:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, kind, status, scheduled_for, sent_at
                FROM reminders
                WHERE item_id = ? AND kind LIKE 'urgent%'
                ORDER BY id ASC;
                """,
                (item_id,),
            )
            urgent_rows = [dict(r) for r in cur.fetchall()]
            sent_urgent = [r for r in urgent_rows if r["status"] == "sent"]
            pending_urgent = [r for r in urgent_rows if r["status"] == "pending"]

            sent_count = len(sent_urgent)
            max_repeats = getattr(app.config, "URGENT_MAX_REPEATS", 3)
            repeat_minutes = getattr(app.config, "URGENT_REPEAT_MINUTES", 30)

            if sent_count == 0 and not pending_urgent:
                dedupe_key = f"{item_id}:urgent_first:{now_iso}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reminders
                    (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
                    VALUES (?, 'urgent_first', ?, 'pending', ?, 'Item became urgent', ?);
                    """,
                    (item_id, now_iso, dedupe_key, now_iso),
                )
            elif 0 < sent_count < (1 + max_repeats) and not pending_urgent:
                last_sent = sent_urgent[-1]
                last_sent_dt = parse_utc_iso(last_sent.get("sent_at") or last_sent.get("scheduled_for")) or now_utc
                next_esc_dt = max(now_utc, last_sent_dt + timedelta(minutes=repeat_minutes))
                next_esc_iso = format_utc_iso(next_esc_dt)
                kind = f"urgent_escalation_{sent_count}"
                dedupe_key = f"{item_id}:{kind}:{next_esc_iso}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reminders
                    (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
                    VALUES (?, ?, ?, 'pending', ?, ?, ?);
                    """,
                    (item_id, kind, next_esc_iso, dedupe_key, f"Urgent escalation #{sent_count}", now_iso),
                )

    elif eff_priority in (Priority.IMPORTANT.value, "important"):
        conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled', reason = 'priority_downgraded_to_important'
            WHERE item_id = ? AND status = 'pending' AND kind LIKE 'urgent%';
            """,
            (item_id,),
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM reminders WHERE item_id = ? AND kind LIKE 'urgent%' AND status = 'sent';",
            (item_id,),
        )
        has_urgent_sent = cur.fetchone()[0] > 0
        if not has_urgent_sent:
            cur.execute(
                "SELECT COUNT(*) FROM reminders WHERE item_id = ? AND kind = 'important_first';",
                (item_id,),
            )
            if cur.fetchone()[0] == 0:
                dedupe_key = f"{item_id}:important_first:{now_iso}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reminders
                    (item_id, kind, scheduled_for, status, dedupe_key, reason, created_at)
                    VALUES (?, 'important_first', ?, 'pending', ?, 'Item became important', ?);
                    """,
                    (item_id, now_iso, dedupe_key, now_iso),
                )

    elif eff_priority in (Priority.NORMAL.value, "normal"):
        conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled', reason = 'priority_downgraded_to_normal'
            WHERE item_id = ? AND status = 'pending' AND (kind LIKE 'urgent%' OR kind LIKE 'important%');
            """,
            (item_id,),
        )


def _build_minimal_message(
    item_title: Optional[str],
    kind: str,
    reason: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Generate minimal generic plain-text for push notifications.

    Guarantees:
    - NEVER includes email bodies, evidence text, or sender names.
    - Minimal generic summary text by default.
    - Specific required text for latest_start and final submission nudge.

    Returns:
        (title, body, ntfy_priority)
    """
    include_title = getattr(app.config, "NTFY_INCLUDE_TITLE", False)
    if include_title and item_title:
        title = item_title[:100]
    else:
        title = "ClassMail J Alert"

    if kind == "latest_start":
        body = "ClassMail J: start your assessment now to finish before the deadline"
        priority = "urgent"
    elif kind == "latest_start_passed":
        remain_text = reason if reason else "some time remains"
        body = f"ClassMail J: latest start has passed, {remain_text}"
        priority = "urgent"
    elif kind == "assessment_headsup":
        body = "ClassMail J: assessment start time approaching"
        priority = "high"
    elif is_final_submission_nudge(kind, reason):
        body = "ClassMail J: 1 submission due soon, upload now"
        priority = "urgent"
    elif kind.startswith("urgent"):
        body = "ClassMail J: 1 urgent item needs attention"
        priority = "urgent"
    elif kind.startswith("deadline"):
        note = f" ({reason})" if reason and "submission" not in reason else ""
        body = f"ClassMail J: 1 deadline approaching{note}"
        priority = "high"
    elif kind.startswith("important"):
        body = "ClassMail J: 1 important item needs attention"
        priority = "high"
    elif kind == "ambiguous_confirmation":
        body = "ClassMail J: 1 item needs your confirmation"
        priority = "default"
    else:
        body = "ClassMail J: 1 item reminder"
        priority = "default"

    return title, body, priority


def dispatch_due_reminders(
    conn: sqlite3.Connection,
    now_utc: datetime,
    ntfy_client: Optional[BaseNtfyClient] = None,
) -> int:
    """Evaluate dispatch guards and send due reminders."""
    if not get_notifications_enabled(conn):
        return 0

    now_iso = format_utc_iso(now_utc)
    client = ntfy_client or NtfyClient()

    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.id AS reminder_id, r.item_id, r.kind, r.scheduled_for, r.reason, r.dedupe_key
        FROM reminders r
        WHERE r.status = 'pending' AND r.scheduled_for <= ?
        ORDER BY r.scheduled_for ASC, r.id ASC;
        """,
        (now_iso,),
    )
    pending_rows = [dict(r) for r in cur.fetchall()]
    if not pending_rows:
        return 0

    dispatched_count = 0
    to_dispatch_items: List[Dict[str, Any]] = []

    # Quiet hours evaluation
    is_quiet, quiet_end_utc = is_in_quiet_hours(now_utc)
    urgent_bypass = getattr(app.config, "URGENT_BYPASS_QUIET", False)
    catchup_grace_minutes = getattr(app.config, "CATCHUP_GRACE_MINUTES", 15)

    for r in pending_rows:
        r_id = r["reminder_id"]
        item_id = r["item_id"]
        kind = r["kind"]
        reason = r.get("reason")
        scheduled_for_iso = r["scheduled_for"]

        cur.execute("SELECT * FROM items WHERE id = ?;", (item_id,))
        item_row = cur.fetchone()
        if not item_row:
            conn.execute("UPDATE reminders SET status = 'skipped', reason = 'item_missing' WHERE id = ?;", (r_id,))
            continue

        item = dict(item_row)
        state = item.get("state", "needs_review")

        # Guard 1: Completed or cancelled
        if state in (ItemState.COMPLETED.value, ItemState.CANCELLED.value, "completed", "cancelled") or item.get("completed_at"):
            conn.execute("UPDATE reminders SET status = 'skipped', reason = 'item_completed_or_cancelled' WHERE id = ?;", (r_id,))
            continue

        # Guard 1b: TURNED_IN suppression
        if item.get("source_status") and str(item.get("source_status")).upper() == "TURNED_IN":
            if kind.startswith("deadline") or kind in ("latest_start", "assessment_headsup", "latest_start_passed"):
                conn.execute("UPDATE reminders SET status = 'skipped', reason = 'turned_in' WHERE id = ?;", (r_id,))
                continue

        # Guard 2: Acknowledged for urgent escalations
        if item.get("acknowledged_at") and kind.startswith("urgent_escalation"):
            conn.execute("UPDATE reminders SET status = 'skipped', reason = 'item_acknowledged' WHERE id = ?;", (r_id,))
            continue

        # Check time-critical status
        is_time_critical = is_reminder_time_critical(kind, reason)

        # Catch-up check: compare clock to scheduled_for
        sched_dt = parse_utc_iso(scheduled_for_iso)
        delay_seconds = max(0, int((now_utc - sched_dt).total_seconds())) if sched_dt else 0
        delay_minutes = delay_seconds // 60

        if delay_minutes > catchup_grace_minutes:
            conn.execute(
                "UPDATE reminders SET status = 'skipped', reason = 'catchup_grace_exceeded' WHERE id = ?;",
                (r_id,),
            )
            continue

        # Guard 3: Snooze deferral (ignored by time-critical reminders)
        snoozed_until_str = item.get("snoozed_until")
        if snoozed_until_str and not is_time_critical:
            snooze_dt = parse_utc_iso(snoozed_until_str)
            if snooze_dt and now_utc < snooze_dt:
                new_scheduled_iso = format_utc_iso(snooze_dt)
                new_dedupe = f"{item_id}:{kind}:snoozed:{r_id}:{new_scheduled_iso}"
                conn.execute(
                    """
                    UPDATE reminders
                    SET scheduled_for = ?, dedupe_key = ?, reason = 'deferred_due_to_snooze'
                    WHERE id = ?;
                    """,
                    (new_scheduled_iso, new_dedupe, r_id),
                )
                continue

        # Guard 4: Deadline changed
        if kind.startswith("deadline_") and not is_final_submission_nudge(kind, reason):
            current_dl = parse_utc_iso(item.get("deadline_at"))
            if not current_dl:
                conn.execute("UPDATE reminders SET status = 'skipped', reason = 'deadline_removed' WHERE id = ?;", (r_id,))
                continue

        # Guard 5: Event start passed
        item_kind = item.get("kind", "other")
        is_event = item_kind in (ItemKind.INTERVIEW.value, ItemKind.MEETING.value, ItemKind.EVENT.value, "interview", "meeting", "event")
        if is_event and item.get("deadline_at"):
            dl_dt = parse_utc_iso(item["deadline_at"])
            if dl_dt and dl_dt <= now_utc:
                conn.execute("UPDATE reminders SET status = 'skipped', reason = 'event_start_passed' WHERE id = ?;", (r_id,))
                continue

        # Guard 6: Quiet hours deferral (ignored by time-critical reminders)
        is_urgent = kind.startswith("urgent")
        if is_quiet and not is_time_critical and not (is_urgent and urgent_bypass):
            quiet_end_iso = format_utc_iso(quiet_end_utc)
            new_dedupe = f"{item_id}:{kind}:{quiet_end_iso}"
            conn.execute(
                """
                UPDATE reminders
                SET scheduled_for = ?, dedupe_key = ?, reason = 'deferred_quiet_hours'
                WHERE id = ?;
                """,
                (quiet_end_iso, new_dedupe, r_id),
            )
            continue

        to_dispatch_items.append({
            "reminder": r,
            "item": item,
            "delay_seconds": delay_seconds,
            "delay_minutes": delay_minutes,
            "is_time_critical": is_time_critical,
            "scheduled_for": scheduled_for_iso,
        })

    if not to_dispatch_items:
        return 0

    # Guard 7: Morning Quiet-Hours Collapse (applies only to non-time-critical items)
    quiet_deferred = [
        entry for entry in to_dispatch_items
        if not entry["is_time_critical"] and "deferred_quiet_hours" in (entry["reminder"].get("reason") or "")
    ]
    if len(quiet_deferred) >= 2:
        count = len(quiet_deferred)
        summary_title = "ClassMail J: Morning Summary"
        summary_body = f"ClassMail J: {count} items need attention"

        has_real_items = any(not e["item"].get("is_demo") for e in quiet_deferred)
        notify_demo = getattr(app.config, "NOTIFY_DEMO_ITEMS", False)

        if has_real_items or notify_demo:
            success, err = client.send(summary_title, summary_body, priority="default", tags="sunrise")
            channel = "ntfy" if success else "local"
            outcome = "collapsed" if success else "failed"
        else:
            channel = "local"
            outcome = "collapsed"
            err = None

        conn.execute(
            """
            INSERT INTO notification_log (item_id, at, channel, title, body, outcome, error, scheduled_for, delay_seconds)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 0);
            """,
            (now_iso, channel, summary_title, summary_body, outcome, err, now_iso),
        )

        for entry in quiet_deferred:
            conn.execute(
                "UPDATE reminders SET status = 'sent', sent_at = ? WHERE id = ?;",
                (now_iso, entry["reminder"]["reminder_id"]),
            )
            dispatched_count += 1

        collapsed_ids = {e["reminder"]["reminder_id"] for e in quiet_deferred}
        to_dispatch_items = [e for e in to_dispatch_items if e["reminder"]["reminder_id"] not in collapsed_ids]

    if not to_dispatch_items:
        return dispatched_count

    # Frequency cap check (MAX_NOTIFICATIONS_PER_HOUR)
    # Time-critical reminders are NEVER collapsed away or deferred by the frequency cap
    one_hour_ago = format_utc_iso(now_utc - timedelta(hours=1))
    cur.execute(
        """
        SELECT COUNT(*) FROM notification_log
        WHERE channel = 'ntfy' AND outcome IN ('delivered', 'collapsed') AND at >= ?;
        """,
        (one_hour_ago,),
    )
    sent_in_last_hour = cur.fetchone()[0]
    max_per_hour = getattr(app.config, "MAX_NOTIFICATIONS_PER_HOUR", 5)

    time_critical_entries = [e for e in to_dispatch_items if e["is_time_critical"]]
    normal_entries = [e for e in to_dispatch_items if not e["is_time_critical"]]

    remaining_capacity = max(0, max_per_hour - sent_in_last_hour - len(time_critical_entries))

    if len(normal_entries) > remaining_capacity and remaining_capacity > 0:
        can_send_normal = normal_entries[:remaining_capacity]
        must_collapse_normal = normal_entries[remaining_capacity:]
    elif remaining_capacity == 0:
        can_send_normal = []
        must_collapse_normal = normal_entries
    else:
        can_send_normal = normal_entries
        must_collapse_normal = []

    # Send individual entries (all time-critical + allowed normal entries)
    individual_entries = time_critical_entries + can_send_normal

    for entry in individual_entries:
        rem = entry["reminder"]
        itm = entry["item"]
        title, body, prio = _build_minimal_message(itm.get("title"), rem["kind"], rem.get("reason"))

        # If late within grace, append marker
        if entry["delay_minutes"] >= 1:
            body = f"{body} (late, {entry['delay_minutes']} min)"

        is_demo = bool(itm.get("is_demo", 0))
        notify_demo = getattr(app.config, "NOTIFY_DEMO_ITEMS", False)

        if is_demo and not notify_demo:
            channel = "local"
            outcome = "logged"
            err = None
        else:
            success, err = client.send(title, body, priority=prio, tags="bell")
            channel = "ntfy" if success else "local"
            outcome = "delivered" if success else "failed"

        conn.execute(
            """
            INSERT INTO notification_log (item_id, at, channel, title, body, outcome, error, scheduled_for, delay_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (itm["id"], now_iso, channel, title, body, outcome, err, entry["scheduled_for"], entry["delay_seconds"]),
        )
        conn.execute(
            "UPDATE reminders SET status = 'sent', sent_at = ? WHERE id = ?;",
            (now_iso, rem["reminder_id"]),
        )
        dispatched_count += 1

    # Collapse excess normal entries into one summary
    if must_collapse_normal:
        count = len(must_collapse_normal)
        summary_title = "ClassMail J: Notification Summary"
        summary_body = f"ClassMail J: {count} additional notifications deferred (rate limited)"

        has_real = any(not e["item"].get("is_demo") for e in must_collapse_normal)
        notify_demo = getattr(app.config, "NOTIFY_DEMO_ITEMS", False)

        if has_real or notify_demo:
            success, err = client.send(summary_title, summary_body, priority="default", tags="hourglass")
            channel = "ntfy" if success else "local"
            outcome = "collapsed" if success else "failed"
        else:
            channel = "local"
            outcome = "collapsed"
            err = None

        conn.execute(
            """
            INSERT INTO notification_log (item_id, at, channel, title, body, outcome, error, scheduled_for, delay_seconds)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 0);
            """,
            (now_iso, channel, summary_title, summary_body, outcome, err, now_iso),
        )

        for entry in must_collapse_normal:
            conn.execute(
                "UPDATE reminders SET status = 'sent', sent_at = ? WHERE id = ?;",
                (now_iso, entry["reminder"]["reminder_id"]),
            )
            dispatched_count += 1

    return dispatched_count


def run_reminder_tick(
    conn: sqlite3.Connection,
    now_utc: datetime,
    ntfy_client: Optional[BaseNtfyClient] = None,
) -> Dict[str, Any]:
    """Execute a single reminder tick."""
    if not get_notifications_enabled(conn):
        return {"status": "disabled", "recomputed": 0, "dispatched": 0}

    now_iso = format_utc_iso(now_utc)
    cur = conn.cursor()

    # Step 1: Recompute priority for active items
    cur.execute(
        """
        SELECT * FROM items
        WHERE state NOT IN ('completed', 'cancelled') AND completed_at IS NULL;
        """
    )
    active_rows = [dict(r) for r in cur.fetchall()]
    recomputed_count = 0

    for item in active_rows:
        new_priority, new_reason = recompute_priority(item, now=now_utc)
        old_priority = item.get("priority")
        priority_changed = old_priority != new_priority.value

        if priority_changed or item.get("priority_reason") != new_reason:
            conn.execute(
                """
                UPDATE items
                SET priority = ?, priority_reason = ?, updated_at = ?
                WHERE id = ?;
                """,
                (new_priority.value, new_reason, now_iso, item["id"]),
            )
            if priority_changed:
                conn.execute(
                    """
                    INSERT INTO item_events (item_id, at, kind, old_value, new_value)
                    VALUES (?, ?, 'priority_changed', ?, ?);
                    """,
                    (item["id"], now_iso, old_priority, new_priority.value),
                )
            recomputed_count += 1
            item["priority"] = new_priority.value
            item["priority_reason"] = new_reason

        # Ensure reminders are planned for this active item
        plan_or_replan_item_reminders(item, conn, now_utc)

    # Step 2: Daily Digest for Normal Priority items
    tz_str = getattr(app.config, "TIMEZONE", "UTC")
    try:
        user_tz = ZoneInfo(tz_str)
    except Exception:
        user_tz = timezone.utc

    local_dt = now_utc.astimezone(user_tz)
    digest_hour = getattr(app.config, "DIGEST_HOUR", 8)

    if local_dt.hour == digest_hour:
        today_str = local_dt.strftime("%Y-%m-%d")
        setting_key = f"daily_digest_sent_{today_str}"
        already_sent = get_setting(setting_key, conn=conn)

        if not already_sent:
            cur.execute(
                """
                SELECT COUNT(*) FROM items
                WHERE state NOT IN ('completed', 'cancelled')
                  AND completed_at IS NULL
                  AND COALESCE(priority_override, priority) = 'normal';
                """
            )
            normal_count = cur.fetchone()[0]
            if normal_count > 0:
                digest_title = "ClassMail J: Daily Digest"
                digest_body = f"ClassMail J: {normal_count} normal priority item(s) pending in your inbox"
                client = ntfy_client or NtfyClient()
                success, err = client.send(digest_title, digest_body, priority="low", tags="newspaper")
                channel = "ntfy" if success else "local"
                outcome = "delivered" if success else "failed"

                conn.execute(
                    """
                    INSERT INTO notification_log (item_id, at, channel, title, body, outcome, error, scheduled_for, delay_seconds)
                    VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 0);
                    """,
                    (now_iso, channel, digest_title, digest_body, outcome, err, now_iso),
                )
            set_setting(setting_key, now_iso, conn=conn)

    # Step 3: Dispatch due reminders
    dispatched_count = dispatch_due_reminders(conn, now_utc, ntfy_client=ntfy_client)

    return {
        "status": "success",
        "recomputed": recomputed_count,
        "dispatched": dispatched_count,
        "active_items": len(active_rows),
    }


def send_test_notification(
    conn: sqlite3.Connection,
    ntfy_client: Optional[BaseNtfyClient] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Dispatch a test notification clearly labeled as a test."""
    now = now_utc or datetime.now(timezone.utc)
    now_iso = format_utc_iso(now)
    client = ntfy_client or NtfyClient()

    test_title = "ClassMail J Test"
    test_body = "This is a test notification from ClassMail J."

    success, err = client.send(test_title, test_body, priority="default", tags="test_tube")
    channel = "ntfy" if success else "local"
    outcome = "delivered" if success else "failed"

    conn.execute(
        """
        INSERT INTO notification_log (item_id, at, channel, title, body, outcome, error, scheduled_for, delay_seconds)
        VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 0);
        """,
        (now_iso, channel, test_title, test_body, outcome, err, now_iso),
    )

    return {
        "status": "delivered" if success else "failed",
        "channel": channel,
        "outcome": outcome,
        "error": err,
        "sent_at": now_iso,
    }


def get_notification_status(conn: sqlite3.Connection, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Return notification status without revealing NTFY_TOPIC."""
    now = now_utc or datetime.now(timezone.utc)
    one_hour_ago = format_utc_iso(now - timedelta(hours=1))

    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM notification_log WHERE at >= ?;",
        (one_hour_ago,),
    )
    recent_count = cur.fetchone()[0]

    topic = getattr(app.config, "NTFY_TOPIC", "")
    is_configured = bool(topic and topic.strip())
    enabled = get_notifications_enabled(conn)

    return {
        "ntfy_configured": is_configured,
        "notifications_enabled": enabled,
        "quiet_hours_enabled": True,
        "quiet_hours_start": getattr(app.config, "QUIET_HOURS_START", "22:00"),
        "quiet_hours_end": getattr(app.config, "QUIET_HOURS_END", "07:00"),
        "urgent_bypass_quiet": getattr(app.config, "URGENT_BYPASS_QUIET", False),
        "max_per_hour": getattr(app.config, "MAX_NOTIFICATIONS_PER_HOUR", 5),
        "recent_count": recent_count,
    }
