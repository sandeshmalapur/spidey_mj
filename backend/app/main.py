import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import ALLOWED_ORIGINS, APP_VERSION
from app.db import check_db_health, get_connection, init_db, utc_now_iso
from app.demo_data import DEMO_ITEMS
from app.models import (
    DeadlineStatus,
    ItemCreate,
    ItemKind,
    ItemResponse,
    ItemState,
    ItemType,
    ItemUpdate,
    Priority,
    SnoozeRequest,
    SourceType,
)


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the SQLite schema on startup
    init_db()
    yield


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
        query = "SELECT * FROM items WHERE 1=1"
        params: List[Any] = []

        if priority is not None:
            query += " AND COALESCE(priority_override, priority) = ?"
            params.append(priority.value)

        if state is not None:
            query += " AND state = ?"
            params.append(state.value)

        if item_type is not None:
            query += " AND item_type = ?"
            params.append(item_type.value)

        if not include_snoozed:
            now_iso = utc_now_iso()
            query += " AND (snoozed_until IS NULL OR snoozed_until <= ?)"
            params.append(now_iso)

        query += " ORDER BY id DESC;"

        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        return [ItemResponse.from_row(dict(r)) for r in rows]
    finally:
        conn.close()


@app.get("/api/items/{item_id}", response_model=ItemResponse)
def get_item(item_id: int) -> ItemResponse:
    conn = get_connection()
    try:
        row = _fetch_item_or_404(conn, item_id)
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate) -> ItemResponse:
    conn = get_connection()
    try:
        now_iso = utc_now_iso()
        links_json = json.dumps(payload.links)

        # Check existing unique constraint before insert for early 409
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

        cur = conn.cursor()
        try:
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
                    payload.title,
                    payload.item_type.value,
                    payload.kind.value,
                    payload.summary,
                    payload.priority.value,
                    payload.priority_reason,
                    payload.priority_override.value if payload.priority_override else None,
                    payload.extraction_confidence,
                    payload.state.value,
                    payload.deadline_text,
                    payload.deadline_at,
                    payload.deadline_status.value,
                    links_json,
                    payload.source_type.value,
                    payload.source_id,
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


@app.patch("/api/items/{item_id}", response_model=ItemResponse)
def patch_item(item_id: int, payload: ItemUpdate) -> ItemResponse:
    conn = get_connection()
    try:
        _fetch_item_or_404(conn, item_id)
        update_data = payload.model_dump(exclude_unset=True)

        if not update_data:
            row = _fetch_item_or_404(conn, item_id)
            return ItemResponse.from_row(row)

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

        fields.append("updated_at = ?")
        values.append(utc_now_iso())
        values.append(item_id)

        sql = f"UPDATE items SET {', '.join(fields)} WHERE id = ?;"
        conn.execute(sql, values)
        conn.commit()

        row = _fetch_item_or_404(conn, item_id)
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
        return ItemResponse.from_row(row)
    finally:
        conn.close()


@app.post("/api/demo/seed")
def seed_demo():
    conn = get_connection()
    try:
        now_iso = utc_now_iso()
        cur = conn.cursor()
        seeded_count = 0

        for item in DEMO_ITEMS:
            cur.execute(
                "SELECT id FROM items WHERE source_type = ? AND source_id = ?;",
                (item["source_type"], item["source_id"]),
            )
            if cur.fetchone() is not None:
                continue

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
                    item["priority"],
                    item.get("priority_reason"),
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
