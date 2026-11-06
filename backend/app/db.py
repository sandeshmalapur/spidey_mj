import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import app.config

SCHEMA_SOURCES_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    name TEXT NOT NULL,
    source_type TEXT NOT NULL
);
"""

SCHEMA_ITEMS_SQL = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    item_type TEXT NOT NULL DEFAULT 'task',
    kind TEXT NOT NULL DEFAULT 'other',
    summary TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    priority_reason TEXT,
    priority_override TEXT,
    extraction_confidence REAL NOT NULL DEFAULT 1.0,
    state TEXT NOT NULL DEFAULT 'needs_review',
    acknowledged_at TEXT,
    snoozed_until TEXT,
    deadline_text TEXT,
    deadline_at TEXT,
    deadline_status TEXT NOT NULL DEFAULT 'none',
    links TEXT NOT NULL DEFAULT '[]',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_id TEXT,
    thread_id TEXT,
    source_title TEXT,
    sender TEXT,
    received_at TEXT,
    evidence TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at TEXT,
    user_edited_fields TEXT NOT NULL DEFAULT '[]',
    source_status TEXT,
    deadline_changed_at TEXT,
    duration_minutes INTEGER,
    extraction_provider TEXT DEFAULT 'rules'
);
"""

SCHEMA_ITEM_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS item_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source_id TEXT,
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
);
"""

SCHEMA_SYNC_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS sync_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    fetched INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
"""

SCHEMA_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

SCHEMA_REMINDERS_SQL = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    dedupe_key TEXT UNIQUE,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    sent_at TEXT,
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
);
"""

SCHEMA_NOTIFICATION_LOG_SQL = """
CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    outcome TEXT NOT NULL,
    error TEXT,
    scheduled_for TEXT,
    delay_seconds INTEGER,
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL
);
"""

SCHEMA_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    display_name TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

SCHEMA_AUTH_STATES_SQL = """
CREATE TABLE IF NOT EXISTS auth_states (
    state TEXT PRIMARY KEY,
    flow_type TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    nonce TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

SCHEMA_PENDING_CONNECTIONS_SQL = """
CREATE TABLE IF NOT EXISTS pending_connections (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    granted_email TEXT NOT NULL,
    token_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

SCHEMA_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_unique
ON items(source_type, source_id)
WHERE source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_items_thread_id ON items(thread_id);
CREATE INDEX IF NOT EXISTS idx_item_events_item_id ON item_events(item_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_dedupe ON reminders(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_reminders_item_status ON reminders(item_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_scheduled ON reminders(scheduled_for, status);
CREATE INDEX IF NOT EXISTS idx_notification_log_at ON notification_log(at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_states_expires_at ON auth_states(expires_at);
CREATE INDEX IF NOT EXISTS idx_pending_connections_expires_at ON pending_connections(expires_at);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_db_path(custom_path: Optional[str] = None) -> Path:
    path = custom_path or app.config.DATABASE_PATH
    return Path(path).resolve()


def get_connection(custom_path: Optional[str] = None) -> sqlite3.Connection:
    path = resolve_db_path(custom_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(custom_path: Optional[str] = None) -> str:
    path = resolve_db_path(custom_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.executescript(SCHEMA_SOURCES_SQL)

            # Check if items table already exists
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items';")
            items_exists = cur.fetchone() is not None

            if items_exists:
                cur.execute("PRAGMA table_info(items);")
                existing_cols = {row["name"] for row in cur.fetchall()}
                # If existing table does not have item_type (Milestone 1 schema)
                if "item_type" not in existing_cols:
                    cur.execute("SELECT COUNT(*) FROM items;")
                    row_count = cur.fetchone()[0]
                    if row_count == 0:
                        conn.execute("DROP TABLE items;")
                        conn.executescript(SCHEMA_ITEMS_SQL)
                    else:
                        # Non-destructive additive migration
                        additive_cols = [
                            ("item_type", "TEXT NOT NULL DEFAULT 'task'"),
                            ("summary", "TEXT"),
                            ("priority_reason", "TEXT"),
                            ("priority_override", "TEXT"),
                            ("extraction_confidence", "REAL NOT NULL DEFAULT 1.0"),
                            ("acknowledged_at", "TEXT"),
                            ("snoozed_until", "TEXT"),
                            ("deadline_text", "TEXT"),
                            ("deadline_at", "TEXT"),
                            ("deadline_status", "TEXT NOT NULL DEFAULT 'none'"),
                            ("links", "TEXT NOT NULL DEFAULT '[]'"),
                            ("thread_id", "TEXT"),
                            ("source_title", "TEXT"),
                            ("sender", "TEXT"),
                            ("received_at", "TEXT"),
                            ("evidence", "TEXT"),
                            ("updated_at", "TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"),
                            ("completed_at", "TEXT"),
                        ]
                        for col_name, col_def in additive_cols:
                            if col_name not in existing_cols:
                                conn.execute(f"ALTER TABLE items ADD COLUMN {col_name} {col_def};")
                else:
                    if "thread_id" not in existing_cols:
                        conn.execute("ALTER TABLE items ADD COLUMN thread_id TEXT;")
                    if "user_edited_fields" not in existing_cols:
                        conn.execute("ALTER TABLE items ADD COLUMN user_edited_fields TEXT NOT NULL DEFAULT '[]';")
                    if "source_status" not in existing_cols:
                        conn.execute("ALTER TABLE items ADD COLUMN source_status TEXT;")
                    if "deadline_changed_at" not in existing_cols:
                        conn.execute("ALTER TABLE items ADD COLUMN deadline_changed_at TEXT;")
                    if "duration_minutes" not in existing_cols:
                        conn.execute("ALTER TABLE items ADD COLUMN duration_minutes INTEGER;")
                    if "extraction_provider" not in existing_cols:
                        conn.execute("ALTER TABLE items ADD COLUMN extraction_provider TEXT DEFAULT 'rules';")
            else:
                conn.executescript(SCHEMA_ITEMS_SQL)

            conn.executescript(SCHEMA_ITEM_EVENTS_SQL)
            conn.executescript(SCHEMA_SYNC_HISTORY_SQL)
            conn.executescript(SCHEMA_SETTINGS_SQL)
            conn.executescript(SCHEMA_REMINDERS_SQL)
            conn.executescript(SCHEMA_NOTIFICATION_LOG_SQL)
            conn.executescript(SCHEMA_SESSIONS_SQL)
            conn.executescript(SCHEMA_AUTH_STATES_SQL)
            conn.executescript(SCHEMA_PENDING_CONNECTIONS_SQL)

            # Check notification_log columns for migration
            cur.execute("PRAGMA table_info(notification_log);")
            notif_cols = {row["name"] for row in cur.fetchall()}
            if "scheduled_for" not in notif_cols:
                conn.execute("ALTER TABLE notification_log ADD COLUMN scheduled_for TEXT;")
            if "delay_seconds" not in notif_cols:
                conn.execute("ALTER TABLE notification_log ADD COLUMN delay_seconds INTEGER;")

            conn.executescript(SCHEMA_INDEX_SQL)
    finally:
        conn.close()
    return str(path)


def check_db_health(custom_path: Optional[str] = None) -> bool:
    try:
        path = resolve_db_path(custom_path)
        if not path.is_file():
            return False
        uri_path = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri_path, uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            result = cursor.fetchone()
            return result is not None and result[0] == 1
        finally:
            conn.close()
    except Exception:
        return False


def get_setting(key: str, default: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> Optional[str]:
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?;", (key,))
        row = cur.fetchone()
        if row:
            return row["value"]
        return default
    finally:
        if close_after:
            conn.close()


def set_setting(key: str, value: str, conn: Optional[sqlite3.Connection] = None) -> None:
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True
    now_iso = utc_now_iso()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                """,
                (key, value, now_iso),
            )
    finally:
        if close_after:
            conn.close()

