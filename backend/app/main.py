import hashlib
import html
import json
import logging
import secrets
import sqlite3
import threading
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger("classmail.api")

from app.auth import (
    ALL_AUTH_SCOPES,
    CLASSROOM_READONLY_SCOPES,
    GMAIL_READONLY_SCOPE,
    auth_rate_limiter,
    consume_auth_state,
    create_session,
    delete_session,
    exchange_code_for_tokens,
    generate_pkce_pair,
    get_session_from_cookie,
    load_web_client_config,
    revoke_google_token,
    save_auth_state,
    save_service_token_file,
    verify_google_id_token,
    verify_signed_session_id,
)
from app import config as app_config
from app.config import ALLOWED_ORIGINS, APP_VERSION, TIMEZONE, validate_auth_config
from app.db import check_db_health, get_connection, init_db, utc_now_iso
from app.demo_data import get_demo_items
from app.extraction import RulesProvider, check_ollama_status, get_extraction_provider
from app.models import (
    ClassroomSyncCounts,
    ClassroomSyncResultResponse,
    DeadlineStatus,
    ItemCreate,
    ItemKind,
    ItemResponse,
    ItemState,
    ItemType,
    ItemUpdate,
    NotificationLogEntry,
    NotificationSettingsUpdate,
    OllamaStatusResponse,
    NotificationStatusResponse,
    Priority,
    ReanalyzeCounts,
    ReanalyzeResultResponse,
    ReminderResponse,
    SnoozeRequest,
    SourceType,
    SyncResultResponse,
    SyncSettingsUpdate,
    SyncStatusResponse,
)
from app.priority import recompute_priority
from app.integrations.sync_service import get_sync_status, sync_gmail
from app.reminders import (
    get_notification_status,
    plan_or_replan_item_reminders,
    send_test_notification,
    set_notifications_enabled,
)
from app.scheduler import sync_scheduler
from app.integrations.sarvam_voice import (
    get_voice_status,
    is_voice_enabled,
    trigger_urgent_voice_call,
)


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


class SystemStatusResponse(BaseModel):
    database_ok: bool
    gmail_token_present: bool
    classroom_token_present: bool
    ntfy_configured: bool
    scheduler_running: bool
    auto_sync_enabled: bool
    last_sync_gmail: Optional[str] = None
    last_sync_classroom: Optional[str] = None
    version: str
    ollama: Optional[OllamaStatusResponse] = None


class ManualIngestRequest(BaseModel):
    source_title: Optional[str] = Field(default=None, max_length=500)
    sender: Optional[str] = Field(default=None, max_length=500)
    text: str = Field(..., min_length=1, max_length=20000)
    received_at: Optional[str] = None


class IngestResponse(BaseModel):
    duplicate: bool
    item: ItemResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate auth configuration if enabled
    validate_auth_config()
    # Initialize the SQLite schema on startup
    init_db()
    sync_scheduler.start()
    yield
    sync_scheduler.shutdown()


app = FastAPI(
    title="ClassMail J API",
    version=APP_VERSION,
    lifespan=lifespan,
)

# Host validation defense: restrict to loopback / local hosts
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "testserver"],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def check_origin_middleware(request: Request, call_next):
    # Enforce origin check on state-changing requests when Origin header is sent by browsers
    if request.method in ("POST", "PATCH", "DELETE", "PUT"):
        origin = request.headers.get("origin")
        if origin:
            norm_origin = origin.rstrip("/")
            allowed = {o.rstrip("/") for o in ALLOWED_ORIGINS}
            if norm_origin not in allowed:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Forbidden: Request origin not allowed."},
                )
    return await call_next(request)


@app.middleware("http")
async def auth_enforcement_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    if app_config.AUTH_ENABLED and request.url.path.startswith("/api"):
        path = request.url.path
        if not (path == "/api/health" or path.startswith("/api/auth/")):
            conn = get_connection()
            try:
                sess = get_session_from_cookie(request, conn)
                if not sess:
                    return JSONResponse(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        content={"detail": "Unauthorized"},
                    )
                request.state.session_user = sess["email"]
            finally:
                conn.close()

    return await call_next(request)


NOT_ALLOWED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ClassMail J - Access Not Allowed</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 1rem;
      box-sizing: border-box;
    }
    .card {
      background: #1e293b;
      padding: 2.5rem 2rem;
      border-radius: 0.75rem;
      max-width: 440px;
      width: 100%;
      text-align: center;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
      border: 1px solid #334155;
    }
    .badge {
      display: inline-block;
      padding: 0.25rem 0.75rem;
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.3);
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
      margin-bottom: 1rem;
    }
    h1 { font-size: 1.4rem; margin: 0 0 0.75rem; color: #ffffff; }
    p { font-size: 0.925rem; line-height: 1.5; color: #94a3b8; margin: 0 0 1.5rem; }
    .btn {
      display: inline-block;
      background: #2563eb;
      color: #ffffff;
      text-decoration: none;
      padding: 0.65rem 1.25rem;
      border-radius: 0.375rem;
      font-weight: 500;
      font-size: 0.9rem;
      transition: background 0.15s ease;
    }
    .btn:hover { background: #1d4ed8; }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">403 Forbidden</div>
    <h1>Access Not Allowed</h1>
    <p>This ClassMail J instance is restricted to its designated owner. The Google account you signed in with is not authorized.</p>
    <a href="/api/auth/google/start" class="btn">Sign in with Owner Account</a>
  </div>
</body>
</html>"""


def render_auth_error_page(message: str) -> str:
    safe_msg = html.escape(message)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ClassMail J - Authentication Error</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 1rem;
      box-sizing: border-box;
    }}
    .card {{
      background: #1e293b;
      padding: 2.5rem 2rem;
      border-radius: 0.75rem;
      max-width: 440px;
      width: 100%;
      text-align: center;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
      border: 1px solid #334155;
    }}
    .badge {{
      display: inline-block;
      padding: 0.25rem 0.75rem;
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.3);
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
      margin-bottom: 1rem;
    }}
    h1 {{ font-size: 1.4rem; margin: 0 0 0.75rem; color: #ffffff; }}
    p {{ font-size: 0.925rem; line-height: 1.5; color: #94a3b8; margin: 0 0 1.5rem; }}
    .btn {{
      display: inline-block;
      background: #2563eb;
      color: #ffffff;
      text-decoration: none;
      padding: 0.65rem 1.25rem;
      border-radius: 0.375rem;
      font-weight: 500;
      font-size: 0.9rem;
    }}
    .btn:hover {{ background: #1d4ed8; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">OAuth Error</div>
    <h1>Authentication Failed</h1>
    <p>{safe_msg}</p>
    <a href="/api/auth/google/start" class="btn">Try Again</a>
  </div>
</body>
</html>"""


def _fetch_item_or_404(conn: sqlite3.Connection, item_id: int) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM items WHERE id = ?;", (item_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with id {item_id} not found",
        )
    return dict(row)


def _attach_next_reminder(conn: sqlite3.Connection, row_dict: dict[str, Any]) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT scheduled_for, kind FROM reminders
        WHERE item_id = ? AND status = 'pending'
        ORDER BY scheduled_for ASC, id ASC LIMIT 1;
        """,
        (row_dict["id"],),
    )
    rem = cur.fetchone()
    if rem:
        row_dict["next_reminder_at"] = rem["scheduled_for"]
        row_dict["next_reminder_kind"] = rem["kind"]
    else:
        row_dict["next_reminder_at"] = None
        row_dict["next_reminder_kind"] = None
    return row_dict


def _recompute_and_persist_priority(conn: sqlite3.Connection, row_dict: dict[str, Any], now: datetime) -> dict[str, Any]:
    new_priority, new_reason = recompute_priority(row_dict, now=now)
    if row_dict.get("priority") != new_priority.value or row_dict.get("priority_reason") != new_reason:
        now_iso = utc_now_iso()
        conn.execute(
            "UPDATE items SET priority = ?, priority_reason = ?, updated_at = ? WHERE id = ?;",
            (new_priority.value, new_reason, now_iso, row_dict["id"]),
        )
        conn.commit()
        row_dict["priority"] = new_priority.value
        row_dict["priority_reason"] = new_reason
        row_dict["updated_at"] = now_iso
    return row_dict


@app.get("/")
def read_root():
    return {
        "app": "ClassMail J",
        "version": APP_VERSION,
        "health": "/api/health",
        "docs": "/docs",
    }


@app.get("/api/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    db_ok = check_db_health()
    if not db_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "db": "error", "version": APP_VERSION},
        )
    return HealthResponse(
        status="ok",
        db="ok",
        version=APP_VERSION,
    )


@app.get("/api/items", response_model=List[ItemResponse])
def get_items(
    priority: Optional[Priority] = Query(None, description="Filter by effective priority"),
    state: Optional[ItemState] = Query(None, description="Filter by state"),
    item_type: Optional[ItemType] = Query(None, description="Filter by item_type"),
    include_snoozed: bool = Query(False, description="Whether to include currently snoozed items"),
) -> List[ItemResponse]:
    conn = get_connection()
    try:
        now_dt = datetime.now(timezone.utc)
        cur = conn.cursor()
        cur.execute("SELECT * FROM items;")
        all_rows = [dict(r) for r in cur.fetchall()]

        # Recompute dynamic priorities and persist if changed
        updated_rows = []
        for r in all_rows:
            updated = _recompute_and_persist_priority(conn, r, now_dt)
            updated_rows.append(updated)

        # Apply filtering
        now_iso = utc_now_iso()
        filtered = []
        for r in updated_rows:
            eff_p = r.get("priority_override") or r["priority"]
            if priority is not None and eff_p != priority.value:
                continue
            if state is not None and r["state"] != state.value:
                continue
            if item_type is not None and r["item_type"] != item_type.value:
                continue
            if not include_snoozed and r.get("snoozed_until"):
                if r["snoozed_until"] > now_iso:
                    continue
            filtered.append(r)

        # Sort by id descending
        filtered.sort(key=lambda x: x["id"], reverse=True)

        # Batch attach next reminder
        cur.execute(
            """
            SELECT item_id, scheduled_for, kind
            FROM reminders
            WHERE status = 'pending'
            ORDER BY scheduled_for ASC, id ASC;
            """
        )
        reminder_map: dict[int, tuple[str, str]] = {}
        for rem in cur.fetchall():
            iid = rem["item_id"]
            if iid not in reminder_map:
                reminder_map[iid] = (rem["scheduled_for"], rem["kind"])

        for r in filtered:
            if r["id"] in reminder_map:
                r["next_reminder_at"], r["next_reminder_kind"] = reminder_map[r["id"]]
            else:
                r["next_reminder_at"], r["next_reminder_kind"] = None, None

        return [ItemResponse.from_row(r) for r in filtered]
    finally:
        conn.close()


@app.get("/api/items/{item_id}", response_model=ItemResponse)
def get_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        row = _fetch_item_or_404(conn, item_id)
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
        row = _attach_next_reminder(conn, row)
        return ItemResponse.from_row(row)
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate) -> ItemResponse:
    conn = get_connection()
    try:
        now_iso = utc_now_iso()
        now_dt = datetime.now(timezone.utc)
        links_json = json.dumps(payload.links)

        # Check existing unique constraint
        if payload.source_id is not None:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM items WHERE source_type = ? AND source_id = ?;",
                (payload.source_type.value, payload.source_id),
            )
            if cur.fetchone() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Item with source_type '{payload.source_type.value}' and source_id '{payload.source_id}' already exists.",
                )

        # Recompute priority with engine
        computed_priority, computed_reason = recompute_priority(payload, now=now_dt)
        actual_priority = payload.priority if payload.priority != Priority.NORMAL else computed_priority
        actual_reason = payload.priority_reason or computed_reason

        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO items (
                    title, item_type, kind, summary, priority, priority_reason, priority_override,
                    extraction_confidence, state, deadline_text, deadline_at, deadline_status,
                    duration_minutes, links, source_type, source_id, thread_id, source_title, sender, received_at, evidence,
                    source_status, is_demo, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                );
                """,
                (
                    payload.title,
                    payload.item_type.value,
                    payload.kind.value,
                    payload.summary,
                    actual_priority.value,
                    actual_reason,
                    payload.priority_override.value if payload.priority_override else None,
                    payload.extraction_confidence,
                    payload.state.value,
                    payload.deadline_text,
                    payload.deadline_at,
                    payload.deadline_status.value,
                    payload.duration_minutes,
                    links_json,
                    payload.source_type.value,
                    payload.source_id,
                    payload.thread_id,
                    payload.source_title,
                    payload.sender,
                    payload.received_at,
                    payload.evidence,
                    payload.source_status,
                    1 if payload.is_demo else 0,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Item violates unique constraint: {str(e)}",
            )

        row = _fetch_item_or_404(conn, new_id)
        plan_or_replan_item_reminders(row, conn, now_dt)
        conn.commit()
        row = _attach_next_reminder(conn, row)
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/ingest/manual", response_model=IngestResponse)
def ingest_manual(payload: ManualIngestRequest, response: Response) -> IngestResponse:
    conn = get_connection()
    try:
        now_dt = datetime.now(timezone.utc)
        now_iso = utc_now_iso()

        # Deterministic source_id calculation
        norm_key = f"{payload.sender or ''}|{payload.source_title or ''}|{payload.text.strip()}"
        source_id = hashlib.sha256(norm_key.encode("utf-8")).hexdigest()

        # Check duplicate
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM items WHERE source_type = 'manual' AND source_id = ?;",
            (source_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            existing_dict = _recompute_and_persist_priority(conn, dict(existing), now_dt)
            return IngestResponse(duplicate=True, item=ItemResponse.from_row(existing_dict))

        # Extract using configured provider (rules or ollama)
        provider = get_extraction_provider()
        ext_res = provider.extract(
            text=payload.text,
            subject=payload.source_title,
            sender=payload.sender,
            received_at=now_dt,
            timezone_str=TIMEZONE,
            now=now_dt,
        )

        # Priority calculation
        priority, priority_reason = recompute_priority(ext_res, now=now_dt)

        # Initial state: needs_review if ambiguous deadline or confidence < 0.5; otherwise not_started
        if ext_res.deadline_status == DeadlineStatus.AMBIGUOUS or ext_res.extraction_confidence < 0.5:
            initial_state = ItemState.NEEDS_REVIEW
        else:
            initial_state = ItemState.NOT_STARTED

        links_json = json.dumps(ext_res.links)

        cur.execute(
            """
            INSERT INTO items (
                title, item_type, kind, summary, priority, priority_reason, priority_override,
                extraction_confidence, state, deadline_text, deadline_at, deadline_status,
                duration_minutes, links, source_type, source_id, source_title, sender, received_at, evidence,
                is_demo, created_at, updated_at, extraction_provider
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?
            );
            """,
            (
                ext_res.title,
                ext_res.item_type.value,
                ext_res.kind.value,
                ext_res.summary,
                priority.value,
                priority_reason,
                None,
                ext_res.extraction_confidence,
                initial_state.value,
                ext_res.deadline_text,
                ext_res.deadline_at,
                ext_res.deadline_status.value,
                ext_res.duration_minutes,
                links_json,
                "manual",
                source_id,
                payload.source_title,
                payload.sender,
                payload.received_at or now_iso,
                payload.text,
                0,
                now_iso,
                now_iso,
                ext_res.extraction_provider,
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        response.status_code = status.HTTP_201_CREATED
        new_row = _fetch_item_or_404(conn, new_id)
        plan_or_replan_item_reminders(new_row, conn, now_dt)
        conn.commit()
        new_row = _attach_next_reminder(conn, new_row)
        return IngestResponse(duplicate=False, item=ItemResponse.from_row(new_row))
    finally:
        conn.close()


@app.patch("/api/items/{item_id}", response_model=ItemResponse)
def patch_item(item_id: int, payload: ItemUpdate) -> ItemResponse:
    conn = get_connection()
    try:
        existing = _fetch_item_or_404(conn, item_id)
        update_data = payload.model_dump(exclude_unset=True)

        if not update_data:
            return ItemResponse.from_row(existing)

        # Track user edits in user_edited_fields JSON column
        raw_edited = existing.get("user_edited_fields")
        try:
            existing_edited_list = json.loads(raw_edited) if raw_edited else []
        except Exception:
            existing_edited_list = []

        new_edited_set = set(existing_edited_list)
        for k in update_data.keys():
            new_edited_set.add(k)

        fields: List[str] = []
        values: List[Any] = []

        for k, v in update_data.items():
            fields.append(f"{k} = ?")
            if v is None:
                values.append(None)
            elif isinstance(v, (ItemType, ItemKind, Priority, DeadlineStatus, ItemState)):
                values.append(v.value)
            else:
                values.append(v)

        fields.append("user_edited_fields = ?")
        values.append(json.dumps(list(new_edited_set)))

        fields.append("updated_at = ?")
        values.append(utc_now_iso())
        values.append(item_id)

        sql = f"UPDATE items SET {', '.join(fields)} WHERE id = ?;"
        conn.execute(sql, values)
        conn.commit()

        row = _fetch_item_or_404(conn, item_id)
        now_dt = datetime.now(timezone.utc)
        row = _recompute_and_persist_priority(conn, row, now_dt)
        plan_or_replan_item_reminders(row, conn, now_dt)
        conn.commit()
        row = _attach_next_reminder(conn, row)
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/items/{item_id}/acknowledge", response_model=ItemResponse)
def acknowledge_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        _fetch_item_or_404(conn, item_id)
        now_iso = utc_now_iso()
        conn.execute(
            "UPDATE items SET acknowledged_at = ?, updated_at = ? WHERE id = ?;",
            (now_iso, now_iso, item_id),
        )
        conn.commit()
        row = _fetch_item_or_404(conn, item_id)
        now_dt = datetime.now(timezone.utc)
        row = _recompute_and_persist_priority(conn, row, now_dt)
        plan_or_replan_item_reminders(row, conn, now_dt)
        conn.commit()
        row = _attach_next_reminder(conn, row)
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/items/{item_id}/snooze", response_model=ItemResponse)
def snooze_item(item_id: int, payload: SnoozeRequest) -> ItemResponse:
    conn = get_connection()
    try:
        _fetch_item_or_404(conn, item_id)
        now_iso = utc_now_iso()
        conn.execute(
            "UPDATE items SET snoozed_until = ?, updated_at = ? WHERE id = ?;",
            (payload.until, now_iso, item_id),
        )
        conn.commit()
        row = _fetch_item_or_404(conn, item_id)
        now_dt = datetime.now(timezone.utc)
        row = _recompute_and_persist_priority(conn, row, now_dt)
        plan_or_replan_item_reminders(row, conn, now_dt)
        conn.commit()
        row = _attach_next_reminder(conn, row)
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/items/{item_id}/start", response_model=ItemResponse)
def start_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        _fetch_item_or_404(conn, item_id)
        now_iso = utc_now_iso()
        conn.execute(
            "UPDATE items SET state = ?, updated_at = ? WHERE id = ?;",
            (ItemState.IN_PROGRESS.value, now_iso, item_id),
        )
        conn.commit()
        row = _fetch_item_or_404(conn, item_id)
        now_dt = datetime.now(timezone.utc)
        row = _recompute_and_persist_priority(conn, row, now_dt)
        plan_or_replan_item_reminders(row, conn, now_dt)
        conn.commit()
        row = _attach_next_reminder(conn, row)
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/items/{item_id}/complete", response_model=ItemResponse)
def complete_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        _fetch_item_or_404(conn, item_id)
        now_iso = utc_now_iso()
        conn.execute(
            "UPDATE items SET state = ?, completed_at = ?, updated_at = ? WHERE id = ?;",
            (ItemState.COMPLETED.value, now_iso, now_iso, item_id),
        )
        conn.commit()
        row = _fetch_item_or_404(conn, item_id)
        now_dt = datetime.now(timezone.utc)
        row = _recompute_and_persist_priority(conn, row, now_dt)
        plan_or_replan_item_reminders(row, conn, now_dt)
        conn.commit()
        row = _attach_next_reminder(conn, row)
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/items/{item_id}/cancel", response_model=ItemResponse)
def cancel_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        _fetch_item_or_404(conn, item_id)
        now_iso = utc_now_iso()
        conn.execute(
            "UPDATE items SET state = ?, updated_at = ? WHERE id = ?;",
            (ItemState.CANCELLED.value, now_iso, item_id),
        )
        conn.commit()
        row = _fetch_item_or_404(conn, item_id)
        now_dt = datetime.now(timezone.utc)
        row = _recompute_and_persist_priority(conn, row, now_dt)
        plan_or_replan_item_reminders(row, conn, now_dt)
        conn.commit()
        row = _attach_next_reminder(conn, row)
        return ItemResponse.from_row(row)
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/items/{item_id}/reopen", response_model=ItemResponse)
def reopen_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        _fetch_item_or_404(conn, item_id)
        now_iso = utc_now_iso()
        conn.execute(
            "UPDATE items SET state = ?, completed_at = NULL, updated_at = ? WHERE id = ?;",
            (ItemState.NOT_STARTED.value, now_iso, item_id),
        )
        conn.commit()
        row = _fetch_item_or_404(conn, item_id)
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/demo/seed")
def seed_demo():
    conn = get_connection()
    try:
        now_dt = datetime.now(timezone.utc)
        now_iso = utc_now_iso()
        cur = conn.cursor()
        seeded_count = 0

        demo_items = get_demo_items(now_dt)

        for item in demo_items:
            cur.execute(
                "SELECT id FROM items WHERE source_type = ? AND source_id = ?;",
                (item["source_type"], item["source_id"]),
            )
            if cur.fetchone() is not None:
                continue

            # Compute priority through the priority engine
            p, reason = recompute_priority(item, now=now_dt)

            links_json = json.dumps(item.get("links", []))
            cur.execute(
                """
                INSERT INTO items (
                    title, item_type, kind, summary, priority, priority_reason, priority_override,
                    extraction_confidence, state, deadline_text, deadline_at, deadline_status,
                    duration_minutes, links, source_type, source_id, source_title, sender, received_at, evidence,
                    is_demo, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                );
                """,
                (
                    item["title"],
                    item["item_type"],
                    item["kind"],
                    item.get("summary"),
                    p.value,
                    reason,
                    item.get("priority_override"),
                    item.get("extraction_confidence", 1.0),
                    item["state"],
                    item.get("deadline_text"),
                    item.get("deadline_at"),
                    item.get("deadline_status", "none"),
                    item.get("duration_minutes"),
                    links_json,
                    item["source_type"],
                    item["source_id"],
                    item.get("source_title"),
                    item.get("sender"),
                    item.get("received_at"),
                    item.get("evidence"),
                    1,
                    now_iso,
                    now_iso,
                ),
            )
            seeded_count += 1

        conn.commit()

        # Plan reminders for seeded demo items
        cur.execute("SELECT * FROM items WHERE is_demo = 1;")
        for demo_row in cur.fetchall():
            plan_or_replan_item_reminders(dict(demo_row), conn, now_dt)
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM items WHERE is_demo = 1;")
        total_demo = cur.fetchone()[0]

        return {"status": "ok", "seeded": seeded_count, "total_demo": total_demo}
    finally:
        conn.close()


@app.post("/api/demo/reset")
def reset_demo():
    """Clear all existing demo data and reload fresh relative-date demo items."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM items WHERE is_demo = 1;")
        deleted_count = cur.rowcount
        conn.commit()

        now_dt = datetime.now(timezone.utc)
        now_iso = utc_now_iso()
        demo_items = get_demo_items(now_dt)
        seeded_count = 0

        for item in demo_items:
            p, reason = recompute_priority(item, now=now_dt)
            links_json = json.dumps(item.get("links", []))
            cur.execute(
                """
                INSERT INTO items (
                    title, item_type, kind, summary, priority, priority_reason, priority_override,
                    extraction_confidence, state, deadline_text, deadline_at, deadline_status,
                    duration_minutes, links, source_type, source_id, source_title, sender, received_at, evidence,
                    is_demo, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                );
                """,
                (
                    item["title"],
                    item["item_type"],
                    item["kind"],
                    item.get("summary"),
                    p.value,
                    reason,
                    item.get("priority_override"),
                    item.get("extraction_confidence", 1.0),
                    item["state"],
                    item.get("deadline_text"),
                    item.get("deadline_at"),
                    item.get("deadline_status", "none"),
                    item.get("duration_minutes"),
                    links_json,
                    item["source_type"],
                    item["source_id"],
                    item.get("source_title"),
                    item.get("sender"),
                    item.get("received_at"),
                    item.get("evidence"),
                    1,
                    now_iso,
                    now_iso,
                ),
            )
            seeded_count += 1

        conn.commit()

        # Plan reminders for seeded demo items
        cur.execute("SELECT * FROM items WHERE is_demo = 1;")
        for demo_row in cur.fetchall():
            plan_or_replan_item_reminders(dict(demo_row), conn, now_dt)
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM items WHERE is_demo = 1;")
        total_demo = cur.fetchone()[0]

        return {"status": "ok", "deleted": deleted_count, "seeded": seeded_count, "total_demo": total_demo}
    finally:
        conn.close()


@app.get("/api/system/status", response_model=SystemStatusResponse)
def get_system_status_endpoint() -> SystemStatusResponse:
    """Return diagnostic system status without revealing any credentials, tokens or secrets."""
    from app.config import CLASSROOM_TOKEN_FILE, GMAIL_TOKEN_FILE, NTFY_TOPIC

    db_health = check_db_health()
    gmail_token_exists = Path(GMAIL_TOKEN_FILE).is_file()
    classroom_token_exists = Path(CLASSROOM_TOKEN_FILE).is_file()
    ntfy_configured = bool(NTFY_TOPIC and NTFY_TOPIC.strip())

    sync_st = sync_scheduler.get_status()

    return SystemStatusResponse(
        database_ok=db_health.get("status") == "ok",
        gmail_token_present=gmail_token_exists,
        classroom_token_present=classroom_token_exists,
        ntfy_configured=ntfy_configured,
        scheduler_running=sync_scheduler.scheduler.running,
        auto_sync_enabled=sync_st.auto_sync_enabled,
        last_sync_gmail=sync_st.last_synced_at,
        last_sync_classroom=sync_st.classroom_last_synced_at,
        version=APP_VERSION,
        ollama=check_ollama_status(),
    )


@app.get("/api/ollama/status", response_model=OllamaStatusResponse)
def get_ollama_status_endpoint() -> OllamaStatusResponse:
    """Return status of Ollama connection, configured model, and availability."""
    return OllamaStatusResponse(**check_ollama_status())


# ---------------------------------------------------------------------------
# Sarvam AI Voice Call endpoints
# ---------------------------------------------------------------------------

class SarvamVoiceStatusResponse(BaseModel):
    enabled: bool
    configured: bool
    max_calls_per_hour: int
    calls_in_last_hour: int


class SarvamTestCallRequest(BaseModel):
    item_id: int = Field(default=0, description="Item id (0 = test ping)")
    item_title: str = Field(default="Test Alert", max_length=200)
    deadline_label: str = Field(default="", max_length=100)


class SarvamTestCallResponse(BaseModel):
    success: bool
    skipped: bool
    reason: Optional[str] = None
    call_id: Optional[str] = None
    error: Optional[str] = None
    dry_run: Optional[bool] = None


@app.get("/api/sarvam/status", response_model=SarvamVoiceStatusResponse)
def get_sarvam_status_endpoint() -> SarvamVoiceStatusResponse:
    """Return Sarvam AI voice call configuration status."""
    return SarvamVoiceStatusResponse(**get_voice_status())


@app.post("/api/sarvam/test-call", response_model=SarvamTestCallResponse)
def post_sarvam_test_call(body: SarvamTestCallRequest) -> SarvamTestCallResponse:
    """
    Place a test voice call via Sarvam AI.
    Uses dry_run=False so the call is actually placed when configured.
    Returns an error if Sarvam is not configured.
    """
    if not is_voice_enabled():
        return SarvamTestCallResponse(
            success=False,
            skipped=True,
            reason="SARVAM_VOICE_ENABLED is false or required env vars are missing",
        )
    result = trigger_urgent_voice_call(
        item_id=body.item_id,
        item_title=body.item_title,
        deadline_label=body.deadline_label,
        dry_run=False,
    )
    return SarvamTestCallResponse(**result)


@app.delete("/api/demo")
def delete_demo():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM items WHERE is_demo = 1;")
        count = cur.fetchone()[0]
        cur.execute("DELETE FROM items WHERE is_demo = 1;")
        conn.commit()
        return {"status": "ok", "deleted": count}
    finally:
        conn.close()


@app.post("/api/sync/gmail", response_model=SyncResultResponse)
def sync_gmail_endpoint() -> SyncResultResponse:
    res = sync_scheduler.run_sync(is_manual=True)
    if res is None:
        return SyncResultResponse(
            status="skipped",
            message="Sync already in progress. Please wait for the current run to finish.",
            counts=SyncCounts(),
        )
    return res


@app.post("/api/sync/classroom", response_model=ClassroomSyncResultResponse)
def sync_classroom_endpoint() -> ClassroomSyncResultResponse:
    res = sync_scheduler.run_classroom_sync(is_manual=True)
    if res is None:
        return ClassroomSyncResultResponse(
            status="skipped",
            message="Sync already in progress. Please wait for the current run to finish.",
            counts=ClassroomSyncCounts(),
        )
    return res


@app.get("/api/sync/status", response_model=SyncStatusResponse)
def get_sync_status_endpoint() -> SyncStatusResponse:
    return sync_scheduler.get_status()


@app.post("/api/sync/settings", response_model=SyncStatusResponse)
def update_sync_settings_endpoint(payload: SyncSettingsUpdate) -> SyncStatusResponse:
    return sync_scheduler.set_enabled(payload.auto_sync_enabled)


def _reanalyze_item_logic(
    conn: sqlite3.Connection,
    item_dict: dict[str, Any],
    provider: Any,
    now_dt: datetime,
) -> Tuple[dict[str, Any], int]:
    evidence = item_dict.get("evidence")
    if not evidence:
        return item_dict, 0

    rec_at = item_dict.get("received_at")
    if rec_at:
        try:
            clean_rec = rec_at.replace("Z", "+00:00")
            rec_dt = datetime.fromisoformat(clean_rec)
        except Exception:
            rec_dt = now_dt
    else:
        rec_dt = now_dt

    ext_res = provider.extract(
        text=evidence,
        subject=item_dict.get("source_title"),
        sender=item_dict.get("sender"),
        received_at=rec_dt,
        timezone_str=TIMEZONE,
        now=now_dt,
    )

    raw_edited = item_dict.get("user_edited_fields")
    try:
        user_edited = set(json.loads(raw_edited)) if raw_edited else set()
    except Exception:
        user_edited = set()

    changes = 0
    now_iso = utc_now_iso()
    item_id = item_dict["id"]
    source_id = item_dict.get("source_id")

    fields_to_check = [
        ("title", ext_res.title, item_dict.get("title")),
        ("item_type", ext_res.item_type.value, item_dict.get("item_type")),
        ("kind", ext_res.kind.value, item_dict.get("kind")),
        ("summary", ext_res.summary, item_dict.get("summary")),
        ("deadline_text", ext_res.deadline_text, item_dict.get("deadline_text")),
        ("deadline_at", ext_res.deadline_at, item_dict.get("deadline_at")),
        ("deadline_status", ext_res.deadline_status.value, item_dict.get("deadline_status")),
        ("duration_minutes", ext_res.duration_minutes, item_dict.get("duration_minutes")),
        ("extraction_provider", ext_res.extraction_provider, item_dict.get("extraction_provider")),
    ]

    update_fields: List[str] = []
    update_values: List[Any] = []

    for field_name, new_val, old_val in fields_to_check:
        if field_name not in user_edited and new_val != old_val:
            changes += 1
            item_dict[field_name] = new_val
            update_fields.append(f"{field_name} = ?")
            update_values.append(new_val)
            conn.execute(
                """
                INSERT INTO item_events (item_id, at, kind, old_value, new_value, source_id)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    item_id,
                    now_iso,
                    f"reanalyzed_{field_name}",
                    str(old_val) if old_val is not None else None,
                    str(new_val) if new_val is not None else None,
                    source_id,
                ),
            )

    if ext_res.extraction_confidence != item_dict.get("extraction_confidence"):
        item_dict["extraction_confidence"] = ext_res.extraction_confidence
        update_fields.append("extraction_confidence = ?")
        update_values.append(ext_res.extraction_confidence)

    if changes > 0:
        new_p, new_reason = recompute_priority(item_dict, now=now_dt)
        item_dict["priority"] = new_p.value
        item_dict["priority_reason"] = new_reason
        item_dict["updated_at"] = now_iso

        update_fields.extend(["priority = ?", "priority_reason = ?", "updated_at = ?"])
        update_values.extend([new_p.value, new_reason, now_iso, item_id])

        conn.execute(
            f"UPDATE items SET {', '.join(update_fields)} WHERE id = ?;",
            update_values,
        )
        conn.commit()

    return item_dict, changes


@app.post("/api/items/{item_id}/reanalyze", response_model=ItemResponse)
def reanalyze_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        row = _fetch_item_or_404(conn, item_id)
        now_dt = datetime.now(timezone.utc)
        provider = get_extraction_provider()
        _reanalyze_item_logic(conn, dict(row), provider, now_dt)
        refetched = _fetch_item_or_404(conn, item_id)
        plan_or_replan_item_reminders(refetched, conn, now_dt)
        conn.commit()
        refetched = _attach_next_reminder(conn, refetched)
        return ItemResponse.from_row(refetched)
    finally:
        conn.close()


@app.post("/api/reanalyze", response_model=ReanalyzeResultResponse)
def reanalyze_all() -> ReanalyzeResultResponse:
    conn = get_connection()
    try:
        now_dt = datetime.now(timezone.utc)
        provider = get_extraction_provider()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM items
            WHERE is_demo = 0 AND state NOT IN ('completed', 'cancelled')
            ORDER BY id ASC;
            """
        )
        rows = [dict(r) for r in cur.fetchall()]

        total = len(rows)
        reanalyzed = 0
        updated = 0
        errors = 0

        for r in rows:
            reanalyzed += 1
            try:
                _, num_changes = _reanalyze_item_logic(conn, r, provider, now_dt)
                if num_changes > 0:
                    plan_or_replan_item_reminders(r, conn, now_dt)
                    updated += 1
            except Exception:
                errors += 1
        conn.commit()

        msg = f"Re-analyzed {reanalyzed} items: {updated} updated, {errors} errors."
        return ReanalyzeResultResponse(
            status="ok",
            message=msg,
            counts=ReanalyzeCounts(
                total=total,
                reanalyzed=reanalyzed,
                updated=updated,
                errors=errors,
            ),
        )
    finally:
        conn.close()


# -------------------------------------------------------------
# Notifications & Reminders Endpoints
# -------------------------------------------------------------
@app.get("/api/notifications", response_model=List[NotificationLogEntry])
def get_notifications(limit: int = Query(50, ge=1, le=200)) -> List[NotificationLogEntry]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, item_id, at, channel, title, body, outcome, error, scheduled_for, delay_seconds
            FROM notification_log
            ORDER BY at DESC, id DESC
            LIMIT ?;
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        return [NotificationLogEntry(**r) for r in rows]
    finally:
        conn.close()


@app.get("/api/items/{item_id}/reminders", response_model=List[ReminderResponse])
def get_item_reminders(item_id: int) -> List[ReminderResponse]:
    conn = get_connection()
    try:
        _fetch_item_or_404(conn, item_id)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, item_id, kind, scheduled_for, status, dedupe_key, reason, created_at, sent_at
            FROM reminders
            WHERE item_id = ?
            ORDER BY scheduled_for ASC, id ASC;
            """,
            (item_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        return [ReminderResponse(**r) for r in rows]
    finally:
        conn.close()


@app.post("/api/notifications/test")
def trigger_test_notification() -> dict[str, Any]:
    conn = get_connection()
    try:
        with conn:
            return send_test_notification(conn)
    finally:
        conn.close()


@app.get("/api/notifications/status", response_model=NotificationStatusResponse)
def get_notifications_status() -> NotificationStatusResponse:
    conn = get_connection()
    try:
        st = get_notification_status(conn)
        return NotificationStatusResponse(**st)
    finally:
        conn.close()


@app.post("/api/notifications/settings", response_model=NotificationStatusResponse)
def update_notification_settings(payload: NotificationSettingsUpdate) -> NotificationStatusResponse:
    conn = get_connection()
    try:
        with conn:
            set_notifications_enabled(payload.notifications_enabled, conn)
            st = get_notification_status(conn)
        return NotificationStatusResponse(**st)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Google OpenID Connect & Unified Data Consent Endpoints
# ---------------------------------------------------------------------------
class DisconnectRequest(BaseModel):
    source: str = Field(..., description="Source to disconnect: 'gmail' or 'classroom'")


@app.get("/api/auth/google/start")
def google_auth_start(request: Request):
    """Initiate Google OAuth 2.0 / OpenID Connect authorization code flow with PKCE.

    Requests OpenID identity plus read-only Gmail and Google Classroom scopes in one flow.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not auth_rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication requests. Please wait a moment.",
        )

    try:
        client_cfg = load_web_client_config()
    except Exception as e:
        logger.error(f"Failed to load web OAuth client configuration: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth client configuration error. Please verify google_web_client.json.",
        )

    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    conn = get_connection()
    try:
        save_auth_state(
            conn,
            state=state,
            flow_type="login",
            code_verifier=code_verifier,
            nonce=nonce,
            ttl_minutes=15,
        )
    finally:
        conn.close()

    params = {
        "client_id": client_cfg["client_id"],
        "redirect_uri": app_config.AUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(ALL_AUTH_SCOPES),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    auth_url = f"{client_cfg['auth_uri']}?{urllib.parse.urlencode(params)}"

    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header:
        return {"auth_url": auth_url}

    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@app.get("/api/auth/google/callback")
def google_auth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle OAuth callback, verify ID token, persist granted tokens, and create session."""
    client_ip = request.client.host if request.client else "unknown"
    if not auth_rate_limiter.is_allowed(client_ip):
        return HTMLResponse(
            content=render_auth_error_page("Too many requests. Please try again later."),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if error:
        return HTMLResponse(
            content=render_auth_error_page(f"Google authorization failed: {error}"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not code or not state:
        return HTMLResponse(
            content=render_auth_error_page("Missing required authorization code or state parameter."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    conn = get_connection()
    try:
        auth_state = consume_auth_state(conn, state=state, flow_type="login")
        if not auth_state:
            return HTMLResponse(
                content=render_auth_error_page("Invalid or expired OAuth state. Please restart sign-in."),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            client_cfg = load_web_client_config()
            token_resp = exchange_code_for_tokens(
                code=code,
                code_verifier=auth_state["code_verifier"],
                redirect_uri=app_config.AUTH_REDIRECT_URI,
                client_config=client_cfg,
            )
            claims = verify_google_id_token(
                id_token_jwt=token_resp.get("id_token", ""),
                client_id=client_cfg["client_id"],
                expected_nonce=auth_state.get("nonce"),
            )
        except Exception as e:
            logger.warning(f"OAuth code exchange or token verification failed: {e}")
            return HTMLResponse(
                content=render_auth_error_page("Authentication verification failed. Please try again."),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        authenticated_email = claims.get("email", "").strip().lower()
        owner_email = app_config.OWNER_EMAIL.strip().lower()

        # Enforce single-owner allowlist: non-owner gets generic not-allowed page and nothing is saved
        if not authenticated_email or (owner_email and authenticated_email != owner_email):
            logger.warning(f"Unauthorized email attempted sign-in: {authenticated_email}")
            return HTMLResponse(content=NOT_ALLOWED_HTML, status_code=status.HTTP_403_FORBIDDEN)

        # Inspect which scopes were actually granted
        raw_scopes_str = token_resp.get("scope", "")
        granted_scopes = set(raw_scopes_str.split())
        access_tok = token_resp.get("access_token")
        refresh_tok = token_resp.get("refresh_token")

        has_gmail = GMAIL_READONLY_SCOPE in granted_scopes
        classroom_granted = [s for s in CLASSROOM_READONLY_SCOPES if s in granted_scopes]
        has_classroom = len(classroom_granted) > 0

        # Persist service tokens at existing paths in authorized user format
        if has_gmail and access_tok:
            save_service_token_file(
                token_path_str=app_config.GMAIL_TOKEN_FILE,
                access_token=access_tok,
                refresh_token=refresh_tok,
                scopes=[GMAIL_READONLY_SCOPE],
                client_config=client_cfg,
            )
            sync_scheduler.reconnect_needed = False
            sync_scheduler.current_status = "idle"

        if has_classroom and access_tok:
            save_service_token_file(
                token_path_str=app_config.CLASSROOM_TOKEN_FILE,
                access_token=access_tok,
                refresh_token=refresh_tok,
                scopes=classroom_granted,
                client_config=client_cfg,
            )
            sync_scheduler.classroom_reconnect_needed = False
            sync_scheduler.classroom_status = "idle"

        # Trigger automatic non-blocking first sync
        def _initial_sync_worker(do_gmail: bool, do_classroom: bool):
            if do_gmail:
                try:
                    sync_scheduler.run_sync(is_manual=True)
                except Exception as ex:
                    logger.warning(f"Initial Gmail sync error: {ex}")
            if do_classroom:
                try:
                    sync_scheduler.run_classroom_sync(is_manual=True)
                except Exception as ex:
                    logger.warning(f"Initial Classroom sync error: {ex}")

        if has_gmail or has_classroom:
            threading.Thread(
                target=_initial_sync_worker,
                args=(has_gmail, has_classroom),
                daemon=True,
            ).start()

        # Create server-side session
        session_id, signed_cookie_val, expires_at_iso = create_session(
            conn,
            email=authenticated_email,
            display_name=claims.get("name"),
            ttl_hours=app_config.SESSION_TTL_HOURS,
        )

        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        max_age = app_config.SESSION_TTL_HOURS * 3600
        response.set_cookie(
            key="classmail_session",
            value=signed_cookie_val,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            path="/",
            secure=False,
        )
        return response
    finally:
        conn.close()


@app.get("/api/auth/me")
def get_auth_me(request: Request):
    """Return authentication status, owner identity, and whether auth is enabled."""
    if not app_config.AUTH_ENABLED:
        return {
            "authenticated": True,
            "auth_enabled": False,
            "email": None,
            "display_name": None,
        }

    conn = get_connection()
    try:
        sess = get_session_from_cookie(request, conn)
        if not sess:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "authenticated": False,
                    "auth_enabled": True,
                    "email": None,
                    "display_name": None,
                },
            )
        return {
            "authenticated": True,
            "auth_enabled": True,
            "email": sess["email"],
            "display_name": sess.get("display_name"),
        }
    finally:
        conn.close()


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    """End the user browser session. Does NOT delete stored data connection tokens."""
    conn = get_connection()
    try:
        cookie_val = request.cookies.get("classmail_session")
        if cookie_val:
            secret = getattr(app_config, "SESSION_SECRET", "")
            session_id = verify_signed_session_id(cookie_val, secret)
            if session_id:
                delete_session(conn, session_id)
    finally:
        conn.close()

    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key="classmail_session", path="/")
    return response


@app.get("/api/connections")
def get_connections():
    """Return per-source connection state, granted scopes, and last sync timestamp."""
    gmail_file = Path(app_config.GMAIL_TOKEN_FILE)
    gmail_connected = gmail_file.is_file()
    gmail_scopes: List[str] = []
    if gmail_connected:
        try:
            data = json.loads(gmail_file.read_text(encoding="utf-8"))
            gmail_scopes = data.get("scopes", [])
        except Exception:
            pass

    classroom_file = Path(app_config.CLASSROOM_TOKEN_FILE)
    classroom_connected = classroom_file.is_file()
    classroom_scopes: List[str] = []
    if classroom_connected:
        try:
            data = json.loads(classroom_file.read_text(encoding="utf-8"))
            classroom_scopes = data.get("scopes", [])
        except Exception:
            pass

    return {
        "gmail": {
            "connected": gmail_connected,
            "scopes_granted": gmail_scopes,
            "last_sync": sync_scheduler.last_run_at,
            "needs_reconnect": (sync_scheduler.current_status == "reconnect_needed"),
        },
        "classroom": {
            "connected": classroom_connected,
            "scopes_granted": classroom_scopes,
            "last_sync": sync_scheduler.classroom_last_run_at,
            "needs_reconnect": (sync_scheduler.classroom_status == "reconnect_needed"),
        },
    }


@app.post("/api/connections/disconnect")
def disconnect_connection(payload: DisconnectRequest, request: Request):
    """Disconnect an integration, revoke Google token at endpoint, and remove token file."""
    if app_config.AUTH_ENABLED:
        conn = get_connection()
        try:
            sess = get_session_from_cookie(request, conn)
            owner = app_config.OWNER_EMAIL.strip().lower()
            if not sess or sess.get("email", "").lower() != owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: Only instance owner may disconnect integrations.",
                )
        finally:
            conn.close()

    source = payload.source.strip().lower()
    if source not in ("gmail", "classroom"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid source. Must be 'gmail' or 'classroom'.",
        )

    revoked = False
    if source == "gmail":
        token_path = Path(app_config.GMAIL_TOKEN_FILE)
        if token_path.is_file():
            try:
                data = json.loads(token_path.read_text(encoding="utf-8"))
                tok = data.get("refresh_token") or data.get("token")
                if tok:
                    revoked = revoke_google_token(tok)
            except Exception:
                pass
            token_path.unlink(missing_ok=True)
        sync_scheduler.current_status = "idle"
        sync_scheduler.reconnect_needed = False
        return {"ok": True, "source": "gmail", "revoked": revoked}

    elif source == "classroom":
        token_path = Path(app_config.CLASSROOM_TOKEN_FILE)
        if token_path.is_file():
            try:
                data = json.loads(token_path.read_text(encoding="utf-8"))
                tok = data.get("refresh_token") or data.get("token")
                if tok:
                    revoked = revoke_google_token(tok)
            except Exception:
                pass
            token_path.unlink(missing_ok=True)
        sync_scheduler.classroom_status = "idle"
        sync_scheduler.classroom_reconnect_needed = False
        return {"ok": True, "source": "classroom", "revoked": revoked}
