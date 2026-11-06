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
    source_title TEXT,
    sender TEXT,
    received_at TEXT,
    evidence TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at TEXT
);
"""

SCHEMA_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_unique
ON items(source_type, source_id)
WHERE source_id IS NOT NULL;
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
                conn.executescript(SCHEMA_ITEMS_SQL)

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
