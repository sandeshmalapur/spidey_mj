"""Background sync scheduler for ClassMail J.

Uses APScheduler to periodically run Gmail and Google Classroom sync with
exponential backoff on network/quota errors, stops retrying on auth failure,
guarantees non-overlapping runs via a shared lock with manual sync buttons,
and persists user toggle in settings.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import app.config
from app.db import get_connection, get_setting, set_setting, utc_now_iso
from app.integrations.classroom_client import BaseClassroomClient, ClassroomAuthError
from app.integrations.classroom_sync import sync_classroom
from app.integrations.gmail_client import BaseGmailClient, GmailAuthError
from app.integrations.sync_service import sync_gmail
from app.models import (
    ClassroomSyncCounts,
    ClassroomSyncResultResponse,
    SyncCounts,
    SyncResultResponse,
    SyncStatusResponse,
)

logger = logging.getLogger("classmail.scheduler")

GMAIL_RECONNECT_COMMAND = "python -m app.integrations.google_auth gmail"
RECONNECT_COMMAND = GMAIL_RECONNECT_COMMAND
CLASSROOM_RECONNECT_COMMAND = "python -m app.integrations.google_auth classroom"
MAX_BACKOFF_MINUTES = 30
JOB_ID = "auto_sync_all"


class SyncSchedulerManager:
    """Manages background APScheduler job, shared lock, backoff, and status."""

    def __init__(
        self,
        interval_minutes: Optional[int] = None,
        clock: Optional[Callable[[], datetime]] = None,
        gmail_client_factory: Optional[Callable[[], BaseGmailClient]] = None,
        classroom_client_factory: Optional[Callable[[], BaseClassroomClient]] = None,
    ) -> None:
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self.gmail_client_factory: Optional[Callable[[], BaseGmailClient]] = gmail_client_factory
        self.classroom_client_factory: Optional[Callable[[], BaseClassroomClient]] = classroom_client_factory
        self.interval_minutes: int = interval_minutes or getattr(app.config, "SYNC_INTERVAL_MINUTES", 5)
        self.auto_sync_enabled: bool = getattr(app.config, "SYNC_ENABLED", True)

        # Shared synchronization lock between background job & manual triggers
        self.sync_lock = threading.Lock()

        # Gmail state tracking
        self.current_status: str = "idle"
        self.last_run_at: Optional[str] = None
        self.last_counts: SyncCounts = SyncCounts()
        self.last_error: Optional[str] = None
        self.consecutive_errors: int = 0
        self.backoff_until: Optional[datetime] = None
        self.reconnect_needed: bool = False

        # Classroom state tracking
        self.classroom_status: str = "idle"
        self.classroom_last_run_at: Optional[str] = None
        self.classroom_last_counts: ClassroomSyncCounts = ClassroomSyncCounts()
        self.classroom_last_error: Optional[str] = None
        self.classroom_reconnect_needed: bool = False

        self._next_run_time_override: Optional[datetime] = None

        # Underlying APScheduler instance
        self.scheduler: BackgroundScheduler = BackgroundScheduler(daemon=True)

    def now(self) -> datetime:
        dt = self.clock()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def init_from_db(self) -> None:
        """Load persistent settings and latest sync history from the database."""
        conn = get_connection()
        try:
            # 1. Read persistent toggle
            persisted_toggle = get_setting("auto_sync_enabled", conn=conn)
            if persisted_toggle is not None:
                self.auto_sync_enabled = persisted_toggle.strip().lower() in ("true", "1", "yes")
            else:
                self.auto_sync_enabled = getattr(app.config, "SYNC_ENABLED", True)

            # 2. Read latest Gmail sync history
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM sync_history
                WHERE source_type = 'gmail'
                ORDER BY id DESC LIMIT 1;
                """
            )
            g_row = cur.fetchone()
            if g_row:
                row_dict = dict(g_row)
                self.last_run_at = row_dict.get("completed_at") or row_dict.get("started_at")
                self.last_counts = SyncCounts(
                    fetched=row_dict.get("fetched", 0),
                    created=row_dict.get("created", 0),
                    updated=row_dict.get("updated", 0),
                    skipped=row_dict.get("skipped", 0),
                    errors=row_dict.get("errors", 0),
                )
                status_val = row_dict.get("status", "idle")
                self.last_error = row_dict.get("error_message")
                if status_val == "reconnect_needed":
                    self.current_status = "reconnect_needed"
                    self.reconnect_needed = True
                elif status_val == "error":
                    self.current_status = "error"
                else:
                    self.current_status = "idle"

            # 3. Read latest Classroom sync history
            cur.execute(
                """
                SELECT * FROM sync_history
                WHERE source_type = 'classroom'
                ORDER BY id DESC LIMIT 1;
                """
            )
            c_row = cur.fetchone()
            if c_row:
                c_dict = dict(c_row)
                self.classroom_last_run_at = c_dict.get("completed_at") or c_dict.get("started_at")
                self.classroom_last_counts = ClassroomSyncCounts(
                    courses=c_dict.get("fetched", 0),
                    created=c_dict.get("created", 0),
                    updated=c_dict.get("updated", 0),
                    skipped=c_dict.get("skipped", 0),
                    errors=c_dict.get("errors", 0),
                )
                c_status = c_dict.get("status", "idle")
                self.classroom_last_error = c_dict.get("error_message")
                if c_status == "reconnect_needed":
                    self.classroom_status = "reconnect_needed"
                    self.classroom_reconnect_needed = True
                elif c_status == "error":
                    self.classroom_status = "error"
                else:
                    self.classroom_status = "idle"
        finally:
            conn.close()

    def start(self) -> None:
        """Start the background scheduler."""
        self.init_from_db()

        # Add job with coalesce=True, max_instances=1, small jitter (5 sec)
        trigger = IntervalTrigger(minutes=self.interval_minutes, jitter=5)
        self.scheduler.add_job(
            self._job_listener,
            trigger=trigger,
            id=JOB_ID,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

        try:
            self.scheduler.start()
        except Exception as e:
            logger.warning(f"Scheduler already running or failed to start: {e}")

        if not self.auto_sync_enabled or (self.reconnect_needed and self.classroom_reconnect_needed):
            self._pause_job()

    def shutdown(self) -> None:
        """Gracefully stop the background scheduler."""
        if self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception as e:
                logger.warning(f"Error shutting down scheduler: {e}")

    def _pause_job(self) -> None:
        try:
            job = self.scheduler.get_job(JOB_ID)
            if job:
                job.pause()
        except Exception:
            pass

    def _resume_job(self) -> None:
        try:
            job = self.scheduler.get_job(JOB_ID)
            if job:
                job.resume()
                next_fire = self.now() + timedelta(minutes=self.interval_minutes)
                job.modify(next_run_time=next_fire)
                self._next_run_time_override = next_fire
        except Exception:
            pass

    def _job_listener(self) -> None:
        """Target called by APScheduler for scheduled runs."""
        if not self.auto_sync_enabled:
            return
        # Run Gmail sync
        self.run_sync(is_manual=False)
        # Run Classroom sync
        self.run_classroom_sync(is_manual=False)

    def run_sync(
        self,
        is_manual: bool = False,
        client: Optional[BaseGmailClient] = None,
    ) -> Optional[SyncResultResponse]:
        """Execute a Gmail sync run under the shared lock."""
        acquired = self.sync_lock.acquire(blocking=False)
        if not acquired:
            if is_manual:
                return SyncResultResponse(
                    status="skipped",
                    message="Sync already in progress. Please wait for the current run to finish.",
                    counts=SyncCounts(),
                )
            logger.info("Background sync skipped: another sync run is currently executing.")
            return None

        try:
            now_dt = self.now()

            if not is_manual:
                if self.reconnect_needed:
                    logger.info("Background sync skipped: Gmail reconnect required.")
                    return None
                if self.backoff_until and now_dt < self.backoff_until:
                    logger.info(f"Background sync skipped: backoff active until {self.backoff_until.isoformat()}.")
                    return None

            self.current_status = "running"
            self.last_run_at = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            active_client = client or (self.gmail_client_factory() if self.gmail_client_factory else None)

            try:
                res = sync_gmail(client=active_client)
            except GmailAuthError as e:
                res = SyncResultResponse(
                    status="error",
                    message=str(e),
                    counts=SyncCounts(errors=1),
                    error_message=str(e),
                )
            except Exception as e:
                res = SyncResultResponse(
                    status="error",
                    message=f"Sync error: {e}",
                    counts=SyncCounts(errors=1),
                    error_message=str(e),
                )

            self.last_counts = res.counts
            err_msg_lower = (res.error_message or res.message or "").lower()

            is_auth_error = (
                res.status == "error"
                and (
                    "token file not found" in err_msg_lower
                    or "re-authenticate" in err_msg_lower
                    or "invalid or expired" in err_msg_lower
                    or "invalid_grant" in err_msg_lower
                    or "reconnect" in err_msg_lower
                    or "auth" in err_msg_lower
                    or "credential" in err_msg_lower
                )
            )

            if is_auth_error:
                self.reconnect_needed = True
                self.current_status = "reconnect_needed"
                self.last_error = (
                    f"Gmail authorization required. Please run `{GMAIL_RECONNECT_COMMAND}` to re-authorize."
                )
                self.consecutive_errors = 0
                self.backoff_until = None
                self._next_run_time_override = None

            elif res.status == "error":
                self.consecutive_errors += 1
                self.current_status = "error"
                self.last_error = res.error_message or res.message

                backoff_mins = min(
                    MAX_BACKOFF_MINUTES,
                    self.interval_minutes * (2 ** (self.consecutive_errors - 1)),
                )
                self.backoff_until = now_dt + timedelta(minutes=backoff_mins)
                self._next_run_time_override = self.backoff_until

                try:
                    job = self.scheduler.get_job(JOB_ID)
                    if job:
                        job.modify(next_run_time=self.backoff_until)
                except Exception:
                    pass

            else:
                self.consecutive_errors = 0
                self.backoff_until = None
                self.reconnect_needed = False
                self.current_status = "idle"
                self.last_error = None

                next_fire = now_dt + timedelta(minutes=self.interval_minutes)
                self._next_run_time_override = next_fire
                try:
                    job = self.scheduler.get_job(JOB_ID)
                    if job and self.auto_sync_enabled:
                        job.modify(next_run_time=next_fire)
                except Exception:
                    pass

            return res

        finally:
            self.sync_lock.release()

    def run_classroom_sync(
        self,
        is_manual: bool = False,
        client: Optional[BaseClassroomClient] = None,
    ) -> Optional[ClassroomSyncResultResponse]:
        """Execute a Google Classroom sync run under the shared lock."""
        acquired = self.sync_lock.acquire(blocking=False)
        if not acquired:
            if is_manual:
                return ClassroomSyncResultResponse(
                    status="skipped",
                    message="Sync already in progress. Please wait for the current run to finish.",
                    counts=ClassroomSyncCounts(),
                )
            logger.info("Background Classroom sync skipped: another sync run is currently executing.")
            return None

        try:
            now_dt = self.now()

            if not is_manual and self.classroom_reconnect_needed:
                logger.info("Background Classroom sync skipped: Classroom reconnect required.")
                return None

            self.classroom_status = "running"
            self.classroom_last_run_at = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            active_client = client or (self.classroom_client_factory() if self.classroom_client_factory else None)

            try:
                res = sync_classroom(client=active_client)
            except ClassroomAuthError as e:
                res = ClassroomSyncResultResponse(
                    status="reconnect_needed",
                    message=str(e),
                    counts=ClassroomSyncCounts(errors=1),
                    error_message=str(e),
                )
            except Exception as e:
                res = ClassroomSyncResultResponse(
                    status="error",
                    message=f"Classroom sync error: {e}",
                    counts=ClassroomSyncCounts(errors=1),
                    error_message=str(e),
                )

            self.classroom_last_counts = res.counts
            err_msg_lower = (res.error_message or res.message or "").lower()

            is_auth_error = (
                res.status in ("error", "reconnect_needed")
                and (
                    "token file not found" in err_msg_lower
                    or "re-authenticate" in err_msg_lower
                    or "invalid or expired" in err_msg_lower
                    or "invalid_grant" in err_msg_lower
                    or "reconnect" in err_msg_lower
                    or "auth" in err_msg_lower
                    or "credential" in err_msg_lower
                )
            )

            if is_auth_error or res.status == "reconnect_needed":
                self.classroom_reconnect_needed = True
                self.classroom_status = "reconnect_needed"
                self.classroom_last_error = (
                    f"Google Classroom authorization required. Please run `{CLASSROOM_RECONNECT_COMMAND}` to re-authorize."
                )
            elif res.status == "error":
                self.classroom_status = "error"
                self.classroom_last_error = res.error_message or res.message
            else:
                self.classroom_reconnect_needed = False
                self.classroom_status = "idle"
                self.classroom_last_error = None

            return res

        finally:
            self.sync_lock.release()

    def get_status(self) -> SyncStatusResponse:
        """Return combined sync status, schedule times, counts, and settings."""
        next_run_iso: Optional[str] = None
        if self.auto_sync_enabled and not (self.reconnect_needed and self.classroom_reconnect_needed):
            if self.scheduler.running:
                try:
                    job = self.scheduler.get_job(JOB_ID)
                    if job and job.next_run_time:
                        next_run_iso = job.next_run_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    pass

            if not next_run_iso and self.backoff_until:
                next_run_iso = self.backoff_until.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif not next_run_iso and self._next_run_time_override:
                next_run_iso = self._next_run_time_override.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        backoff_mins = None
        if self.consecutive_errors > 0:
            backoff_mins = min(
                MAX_BACKOFF_MINUTES,
                self.interval_minutes * (2 ** (self.consecutive_errors - 1)),
            )

        return SyncStatusResponse(
            source_type="gmail",
            last_synced_at=self.last_run_at,
            next_run_at=next_run_iso,
            status=self.current_status,
            counts=self.last_counts,
            error_message=self.last_error,
            auto_sync_enabled=self.auto_sync_enabled,
            interval_minutes=self.interval_minutes,
            reconnect_command=GMAIL_RECONNECT_COMMAND if self.current_status == "reconnect_needed" else None,
            consecutive_errors=self.consecutive_errors,
            backoff_minutes=backoff_mins,
            # Classroom sync status
            classroom_status=self.classroom_status,
            classroom_last_synced_at=self.classroom_last_run_at,
            classroom_counts=self.classroom_last_counts,
            classroom_error_message=self.classroom_last_error,
            classroom_reconnect_command=CLASSROOM_RECONNECT_COMMAND if self.classroom_status == "reconnect_needed" else None,
        )

    def set_enabled(self, enabled: bool) -> SyncStatusResponse:
        """Update auto-sync enable/disable toggle persistently."""
        self.auto_sync_enabled = enabled
        set_setting("auto_sync_enabled", "true" if enabled else "false")

        if enabled:
            self.reconnect_needed = False
            self.classroom_reconnect_needed = False
            self.consecutive_errors = 0
            self.backoff_until = None
            if self.current_status == "reconnect_needed":
                self.current_status = "idle"
                self.last_error = None
            if self.classroom_status == "reconnect_needed":
                self.classroom_status = "idle"
                self.classroom_last_error = None
            self._resume_job()
        else:
            self._pause_job()
            self._next_run_time_override = None

        return self.get_status()


# Global singleton instance used in application lifecycle
sync_scheduler = SyncSchedulerManager()
