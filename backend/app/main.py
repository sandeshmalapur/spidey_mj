import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, List, Optional
from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import ALLOWED_ORIGINS, APP_VERSION, TIMEZONE
from app.db import check_db_health, get_connection, init_db, utc_now_iso
from app.demo_data import get_demo_items
from app.extraction import RulesProvider
from app.models import (
    DeadlineStatus,
    ItemCreate,
    ItemKind,
    ItemResponse,
    ItemState,
    ItemType,
    ItemUpdate,
    Priority,
    ReanalyzeCounts,
    ReanalyzeResultResponse,
    SnoozeRequest,
    SourceType,
    SyncResultResponse,
    SyncSettingsUpdate,
    SyncStatusResponse,
)
from app.priority import recompute_priority
from app.integrations.sync_service import get_sync_status, sync_gmail
from app.scheduler import sync_scheduler


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


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
        return [ItemResponse.from_row(r) for r in filtered]
    finally:
        conn.close()


@app.get("/api/items/{item_id}", response_model=ItemResponse)
def get_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        row = _fetch_item_or_404(conn, item_id)
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
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
                    links, source_type, source_id, thread_id, source_title, sender, received_at, evidence,
                    is_demo, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
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
                    links_json,
                    payload.source_type.value,
                    payload.source_id,
                    payload.thread_id,
                    payload.source_title,
                    payload.sender,
                    payload.received_at,
                    payload.evidence,
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

        # Extract using RulesProvider
        provider = RulesProvider()
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
                links, source_type, source_id, source_title, sender, received_at, evidence,
                is_demo, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?
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
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        response.status_code = status.HTTP_201_CREATED
        new_row = _fetch_item_or_404(conn, new_id)
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
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
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
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
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
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
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
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
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
        row = _recompute_and_persist_priority(conn, row, datetime.now(timezone.utc))
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
                    links, source_type, source_id, source_title, sender, received_at, evidence,
                    is_demo, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
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

        cur.execute("SELECT COUNT(*) FROM items WHERE is_demo = 1;")
        total_demo = cur.fetchone()[0]

        return {"status": "ok", "seeded": seeded_count, "total_demo": total_demo}
    finally:
        conn.close()


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


@app.get("/api/sync/status", response_model=SyncStatusResponse)
def get_sync_status_endpoint() -> SyncStatusResponse:
    return sync_scheduler.get_status()


@app.post("/api/sync/settings", response_model=SyncStatusResponse)
def update_sync_settings_endpoint(payload: SyncSettingsUpdate) -> SyncStatusResponse:
    return sync_scheduler.set_enabled(payload.auto_sync_enabled)


def _reanalyze_item_logic(
    conn: sqlite3.Connection,
    item_dict: dict[str, Any],
    provider: RulesProvider,
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
        provider = RulesProvider()
        _reanalyze_item_logic(conn, dict(row), provider, now_dt)
        refetched = _fetch_item_or_404(conn, item_id)
        return ItemResponse.from_row(refetched)
    finally:
        conn.close()


@app.post("/api/reanalyze", response_model=ReanalyzeResultResponse)
def reanalyze_all() -> ReanalyzeResultResponse:
    conn = get_connection()
    try:
        now_dt = datetime.now(timezone.utc)
        provider = RulesProvider()
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
                    updated += 1
            except Exception:
                errors += 1

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
