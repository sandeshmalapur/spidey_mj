"""Gmail sync service for ClassMail J.

Fetches messages, deduplicates by (source_type='gmail', source_id),
runs extraction & priority engine, updates thread-linked deadlines,
records item_events, and tracks sync status.
"""

import email.utils
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import app.config
from app.config import GMAIL_MAX_MESSAGES, GMAIL_QUERY, TIMEZONE
from app.db import get_connection, utc_now_iso
from app.extraction import OllamaProvider, RulesProvider, get_extraction_provider
from app.integrations.gmail_client import BaseGmailClient, GmailAuthError, GmailClient
from app.models import (
    DeadlineStatus,
    ItemState,
    SyncCounts,
    SyncResultResponse,
    SyncStatusResponse,
)
from app.priority import recompute_priority
from app.reminders import plan_or_replan_item_reminders


def get_sync_status(conn: Optional[sqlite3.Connection] = None) -> SyncStatusResponse:
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM sync_history
            WHERE source_type = 'gmail'
            ORDER BY id DESC LIMIT 1;
            """
        )
        row = cur.fetchone()
        if not row:
            return SyncStatusResponse(
                source_type="gmail",
                last_synced_at=None,
                status="idle",
                counts=SyncCounts(),
                error_message=None,
            )

        row_dict = dict(row)
        return SyncStatusResponse(
            source_type="gmail",
            last_synced_at=row_dict.get("completed_at") or row_dict.get("started_at"),
            status=row_dict["status"],
            counts=SyncCounts(
                fetched=row_dict.get("fetched", 0),
                created=row_dict.get("created", 0),
                updated=row_dict.get("updated", 0),
                skipped=row_dict.get("skipped", 0),
                errors=row_dict.get("errors", 0),
            ),
            error_message=row_dict.get("error_message"),
        )
    finally:
        if close_after:
            conn.close()


def sync_gmail(
    client: Optional[BaseGmailClient] = None,
    conn: Optional[sqlite3.Connection] = None,
    query: Optional[str] = None,
    max_messages: Optional[int] = None,
) -> SyncResultResponse:
    """Synchronize messages from Gmail (read-only).

    Handles deduplication, thread-linked deadline updates, item_events recording,
    and priority recomputation. Never logs tokens or secret credentials.
    """
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True

    started_iso = utc_now_iso()
    counts = SyncCounts()

    # Instantiate Gmail client if not provided
    if client is None:
        try:
            client = GmailClient()
        except GmailAuthError as e:
            err_msg = str(e)
            _record_sync_history(
                conn, "error", started_iso, utc_now_iso(), counts, err_msg
            )
            return SyncResultResponse(
                status="error",
                message=err_msg,
                counts=SyncCounts(errors=1),
                error_message=err_msg,
            )
        except Exception as e:
            err_msg = f"Unexpected Gmail client initialization error: {e}"
            _record_sync_history(
                conn, "error", started_iso, utc_now_iso(), counts, err_msg
            )
            return SyncResultResponse(
                status="error",
                message=err_msg,
                counts=SyncCounts(errors=1),
                error_message=err_msg,
            )

    # Query message IDs
    q = query or GMAIL_QUERY
    max_res = max_messages or GMAIL_MAX_MESSAGES
    try:
        message_ids = client.list_message_ids(query=q, max_results=max_res)
    except GmailAuthError as e:
        err_msg = str(e)
        _record_sync_history(conn, "error", started_iso, utc_now_iso(), counts, err_msg)
        return SyncResultResponse(
            status="error",
            message=err_msg,
            counts=SyncCounts(errors=1),
            error_message=err_msg,
        )
    except Exception as e:
        err_msg = f"Error fetching message list from Gmail: {e}"
        _record_sync_history(conn, "error", started_iso, utc_now_iso(), counts, err_msg)
        return SyncResultResponse(
            status="error",
            message=err_msg,
            counts=SyncCounts(errors=1),
            error_message=err_msg,
        )

    provider = get_extraction_provider()
    rules_provider = RulesProvider()
    ollama_processed_count = 0
    cur = conn.cursor()

    try:
        for msg_id in message_ids:
            counts.fetched += 1

            # 1. Deduplication check
            cur.execute(
                """
                SELECT id FROM items WHERE source_type = 'gmail' AND source_id = ?
                UNION
                SELECT item_id FROM item_events WHERE source_id = ?;
                """,
                (msg_id, msg_id),
            )
            if cur.fetchone() is not None:
                counts.skipped += 1
                continue

            # 2. Fetch full message
            try:
                msg = client.get_message(msg_id)
            except Exception:
                counts.errors += 1
                continue

            # 3. Parse message date
            now_dt = datetime.now(timezone.utc)
            now_iso = utc_now_iso()
            received_iso = now_iso
            msg_dt = now_dt

            if msg.date_str:
                try:
                    parsed_dt = email.utils.parsedate_to_datetime(msg.date_str)
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    msg_dt = parsed_dt.astimezone(timezone.utc)
                    received_iso = msg_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    pass

            # 4. Extract structured fields with provider (respecting sync cap)
            active_provider = provider
            if isinstance(provider, OllamaProvider):
                max_ollama = getattr(app.config, "OLLAMA_MAX_ITEMS_PER_SYNC", 5)
                if ollama_processed_count >= max_ollama:
                    active_provider = rules_provider
                else:
                    ollama_processed_count += 1

            ext_res = active_provider.extract(
                text=msg.body_text,
                subject=msg.subject,
                sender=msg.sender,
                received_at=msg_dt,
                timezone_str=TIMEZONE,
                now=now_dt,
            )

            # 5. Check if another message in the same thread already has an item
            cur.execute(
                """
                SELECT * FROM items
                WHERE source_type = 'gmail' AND thread_id = ?
                ORDER BY id ASC LIMIT 1;
                """,
                (msg.thread_id,),
            )
            existing_thread_row = cur.fetchone()

            is_confirmed_deadline = (ext_res.deadline_status == DeadlineStatus.CONFIRMED)

            if existing_thread_row is not None and is_confirmed_deadline:
                existing_dict = dict(existing_thread_row)
                old_deadline_at = existing_dict.get("deadline_at")

                # If this later message has a DIFFERENT confirmed deadline, update existing item
                if ext_res.deadline_at != old_deadline_at:
                    # Record row in item_events table
                    cur.execute(
                        """
                        INSERT INTO item_events (item_id, at, kind, old_value, new_value, source_id)
                        VALUES (?, ?, 'deadline_updated', ?, ?, ?);
                        """,
                        (
                            existing_dict["id"],
                            now_iso,
                            old_deadline_at or "none",
                            ext_res.deadline_at,
                            msg_id,
                        ),
                    )

                    # If item is already completed, do NOT reopen it
                    is_completed = (existing_dict["state"] == ItemState.COMPLETED.value)
                    if is_completed:
                        cur.execute(
                            """
                            UPDATE items SET
                                deadline_text = ?,
                                deadline_at = ?,
                                deadline_status = ?,
                                updated_at = ?
                            WHERE id = ?;
                            """,
                            (
                                ext_res.deadline_text,
                                ext_res.deadline_at,
                                ext_res.deadline_status.value,
                                now_iso,
                                existing_dict["id"],
                            ),
                        )
                    else:
                        # Recompute priority with the new deadline
                        updated_dict = dict(existing_dict)
                        updated_dict["deadline_at"] = ext_res.deadline_at
                        updated_dict["deadline_text"] = ext_res.deadline_text
                        updated_dict["deadline_status"] = ext_res.deadline_status.value

                        new_p, new_reason = recompute_priority(updated_dict, now=now_dt)

                        cur.execute(
                            """
                            UPDATE items SET
                                deadline_text = ?,
                                deadline_at = ?,
                                deadline_status = ?,
                                priority = ?,
                                priority_reason = ?,
                                updated_at = ?
                            WHERE id = ?;
                            """,
                            (
                                ext_res.deadline_text,
                                ext_res.deadline_at,
                                ext_res.deadline_status.value,
                                new_p.value,
                                new_reason,
                                now_iso,
                                existing_dict["id"],
                            ),
                        )

                    conn.commit()
                    # Replan reminders for the updated thread item
                    cur.execute("SELECT * FROM items WHERE id = ?;", (existing_dict["id"],))
                    up_row = cur.fetchone()
                    if up_row:
                        plan_or_replan_item_reminders(dict(up_row), conn, now_dt)
                        conn.commit()
                    counts.updated += 1
                    continue

            # 6. Otherwise, create a new item for this message
            computed_priority, computed_reason = recompute_priority(ext_res, now=now_dt)

            if ext_res.deadline_status == DeadlineStatus.AMBIGUOUS or ext_res.extraction_confidence < 0.5:
                initial_state = ItemState.NEEDS_REVIEW
            else:
                initial_state = ItemState.NOT_STARTED

            # Build links: Gmail web link + extracted links
            gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"
            all_links = [gmail_link]
            for l in ext_res.links:
                if l not in all_links:
                    all_links.append(l)

            links_json = json.dumps(all_links)

            cur.execute(
                """
                INSERT INTO items (
                    title, item_type, kind, summary, priority, priority_reason, priority_override,
                    extraction_confidence, state, deadline_text, deadline_at, deadline_status,
                    duration_minutes, links, source_type, source_id, thread_id, source_title, sender, received_at, evidence,
                    is_demo, created_at, updated_at, extraction_provider
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, 'gmail', ?, ?, ?, ?, ?, ?,
                    0, ?, ?, ?
                );
                """,
                (
                    ext_res.title,
                    ext_res.item_type.value,
                    ext_res.kind.value,
                    ext_res.summary,
                    computed_priority.value,
                    computed_reason,
                    None,
                    ext_res.extraction_confidence,
                    initial_state.value,
                    ext_res.deadline_text,
                    ext_res.deadline_at,
                    ext_res.deadline_status.value,
                    ext_res.duration_minutes,
                    links_json,
                    msg_id,
                    msg.thread_id,
                    msg.subject,
                    msg.sender,
                    received_iso,
                    msg.body_text,
                    now_iso,
                    now_iso,
                    ext_res.extraction_provider,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
            if new_id:
                cur.execute("SELECT * FROM items WHERE id = ?;", (new_id,))
                new_row = cur.fetchone()
                if new_row:
                    plan_or_replan_item_reminders(dict(new_row), conn, now_dt)
                    conn.commit()
            counts.created += 1

        completed_iso = utc_now_iso()
        _record_sync_history(conn, "success", started_iso, completed_iso, counts, None)

        msg_summary = (
            f"Gmail sync completed. Fetched: {counts.fetched}, "
            f"Created: {counts.created}, Updated: {counts.updated}, "
            f"Skipped: {counts.skipped}, Errors: {counts.errors}."
        )
        return SyncResultResponse(status="success", message=msg_summary, counts=counts)

    finally:
        if close_after:
            conn.close()


def _record_sync_history(
    conn: sqlite3.Connection,
    status: str,
    started_at: str,
    completed_at: str,
    counts: SyncCounts,
    error_message: Optional[str],
) -> None:
    try:
        conn.execute(
            """
            INSERT INTO sync_history (
                source_type, started_at, completed_at, status,
                fetched, created, updated, skipped, errors, error_message
            ) VALUES ('gmail', ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                started_at,
                completed_at,
                status,
                counts.fetched,
                counts.created,
                counts.updated,
                counts.skipped,
                counts.errors,
                error_message,
            ),
        )
        conn.commit()
    except Exception:
        pass

