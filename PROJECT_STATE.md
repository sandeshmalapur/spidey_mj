# ClassMail J - Project State

## Current Milestone
- Milestone 7: Deterministic Reminders and Notification System (pure deterministic planning, bounded urgent escalation, confirmed deadline T-24h/T-3h reminders, ambiguous confirmation requests, replanning upon item changes, quiet-hours deferral and morning summary collapse, frequency-cap collapse, demo item isolation, ntfy HTTP plain-text client, test notification, notification status, persistent settings toggle, and frontend notification panel).

## Implemented Features
- **Backend Scaffold & SQLite Database**: FastAPI application with strict CORS, additive migrations, `items`, `sources`, `item_events`, `sync_history`, `settings`, `reminders`, and `notification_log` tables.
- **Rules Extraction & Priority Engine**:
  - Deterministic rules-based extraction with pre-normalization (stripping markdown, HTML entities, and zero-width characters while preserving original stored evidence).
  - Hardened candidate date/time parser: optional weekdays, ISO dates, multi-format calendar dates, 12h/24h times, ranges, and configurable `DEFAULT_IST_MEANING`.
  - Actionable classification for interviews, meetings, and events (with "Confirm attendance" action).
  - Non-overdue event status handling ("Event time has passed") for past events.
  - Re-analysis endpoints (`POST /api/items/{id}/reanalyze` and `POST /api/reanalyze`) preserving user-edited fields tracked via `user_edited_fields`.
- **Gmail Read-Only Integration**:
  - OAuth Desktop loopback auth CLI (`python -m app.integrations.google_auth gmail`).
  - Read-only GmailClient fetching plain-text / sanitized HTML bodies, deduplicating messages, updating thread-linked deadlines, and recording item events.
- **Google Classroom Read-Only Integration**:
  - Loopback OAuth CLI (`python -m app.integrations.google_auth classroom`) saving to `CLASSROOM_TOKEN_FILE` with strictly 4 read-only scopes.
  - `ClassroomClient` wrapper querying active courses, coursework (with pagination), announcements, and student submission states.
  - Accurate UTC conversion for `dueDate` and `dueTime`.
- **Automatic Background Sync**:
  - APScheduler `BackgroundScheduler` running inside the FastAPI lifespan every `SYNC_INTERVAL_MINUTES`.
  - Shared concurrency lock (`threading.Lock`) between background runs and manual sync buttons.
  - Exponential backoff on network/quota errors (capped at 30 minutes).
  - Auth failure handling with exact re-auth command.
  - Endpoints: `GET /api/sync/status`, `POST /api/sync/settings`, `POST /api/sync/gmail`, and `POST /api/sync/classroom`.
- **Deterministic Reminders & Notification System**:
  - Reminder tick job executed every `REMINDER_TICK_SECONDS` (default 60s) via the shared APScheduler instance with an injectable clock.
  - Dynamic priority recomputation on active items during each tick to catch time-based urgency threshold crossings (e.g. crossing 48h boundary).
  - Additive database tables: `reminders` (with unique `dedupe_key` preventing duplication) and `notification_log`.
  - Schedule rules:
    - Urgent: notified once upon urgency, followed by bounded escalation up to `URGENT_MAX_REPEATS` at least `URGENT_REPEAT_MINUTES` apart until acknowledged, started, completed, or cancelled.
    - Confirmed deadlines: reminders at T-24h and T-3h; past times skipped; past events receive no reminders.
    - Important: notified once if not already notified at higher level, plus T-24h reminder.
    - Normal: no individual push; included in daily digest at `DIGEST_HOUR` in user `TIMEZONE`.
    - Ambiguous: single "needs your confirmation" request.
  - Replanning: automatic cancellation of obsolete reminders and creation of updated reminders on deadline change, state update, snooze, acknowledgement, or re-analysis.
  - Dispatch guards: live item check, skip completed/cancelled/acknowledged, snooze reschedule, quiet-hours deferral and morning summary collapse, frequency-cap collapse (`MAX_NOTIFICATIONS_PER_HOUR`), and demo item isolation (logged locally).
  - Ntfy Publisher: HTTP POST of minimal, generic plain-text (`NTFY_SERVER` + `/` + `NTFY_TOPIC`) with 5s timeout and ASCII headers. Never includes email bodies, evidence text, or sender names. Never logs or exposes `NTFY_TOPIC`.
  - Endpoints: `GET /api/notifications`, `POST /api/notifications/test`, `GET /api/notifications/status`, `POST /api/notifications/settings`.
- **Frontend Dashboard**:
  - React + Vite + TypeScript application with priority tabs, snooze presets, full CRUD lifecycle, manual paste modal, and re-analysis controls.
  - Notifications Center modal displaying delivery status cards, quiet hours, rate limit, runtime enable/pause toggle, "Send Test Alert" action, and recent delivery logs table.
  - Per-item "Next reminder" indicators on item cards and in the item detail view.
  - "📥 Sync Classroom now" and "📥 Sync Gmail now" buttons with live sync status.
  - Visual distinction with `Classroom` and `Gmail` source badges and "Turned in per Classroom" badge.

## Mocked vs Real
- **Database & Settings**: Real SQLite database (`backend/data/classmail.db`), additive schema migrations, and persistent `settings` table.
- **Scheduler**: Real APScheduler `BackgroundScheduler` in FastAPI lifespan with real threading locks.
- **Gmail Integration**: Real Gmail API client wrapper; unit tests use a `MockGmailClient` and `FakeClock` clearly documented as mocks (unit tests do not prove real Gmail works).
- **Classroom Integration**: Real Classroom API client wrapper (`app.integrations.classroom_client.ClassroomClient`); unit tests use a `MockClassroomClient` clearly documented as a mock (unit tests do not prove real Google Classroom works).
- **Notifications & Reminders**: Real ntfy HTTP client (`app.reminders.NtfyClient`) publishing to ntfy server; unit tests use `MockNtfyClient` and `FakeClock` clearly documented as mocks (unit tests do not prove real ntfy delivery).

## Stack and Run Commands
- **Stack**:
  - Python 3.14.0 (FastAPI, APScheduler, Pydantic, Uvicorn, Pytest, google-api-python-client, google-auth-oauthlib)
  - Node v24.19.0 / v25.1.0 (Vite 8, React 19, TypeScript)
- **Run Backend**:
  ```powershell
  cd backend
  .\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
  ```
- **Run Tests**:
  ```powershell
  cd backend
  .\.venv\Scripts\python.exe -m pytest -v
  ```
- **Run Frontend**:
  ```powershell
  cd frontend
  npm run dev
  ```
- **Build Frontend**:
  ```powershell
  cd frontend
  npm run build
  ```

## Environment Variables (Names Only)
- `DATABASE_PATH`
- `FRONTEND_ORIGIN`
- `TIMEZONE`
- `DEFAULT_IST_MEANING`
- `VITE_API_BASE_URL`
- `URGENT_WITHIN_HOURS`
- `IMPORTANT_WITHIN_DAYS`
- `GOOGLE_CLIENT_FILE`
- `GMAIL_TOKEN_FILE`
- `CLASSROOM_TOKEN_FILE`
- `GMAIL_QUERY`
- `GMAIL_MAX_MESSAGES`
- `SYNC_INTERVAL_MINUTES`
- `SYNC_ENABLED`
- `SCHEDULER_ENABLED`
- `REMINDER_TICK_SECONDS`
- `NOTIFICATIONS_ENABLED`
- `URGENT_MAX_REPEATS`
- `URGENT_REPEAT_MINUTES`
- `DIGEST_HOUR`
- `QUIET_HOURS_START`
- `QUIET_HOURS_END`
- `URGENT_BYPASS_QUIET`
- `MAX_NOTIFICATIONS_PER_HOUR`
- `NOTIFY_DEMO_ITEMS`
- `NTFY_SERVER`
- `NTFY_TOPIC`
- `NTFY_INCLUDE_TITLE`

## Tests Last Run
- Backend Pytest (`cd backend; .\.venv\Scripts\python.exe -m pytest -v`): 83 passed across all 7 test suites (`test_reminders_and_notifications.py`, `test_classroom_sync.py`, `test_background_sync.py`, `test_extraction_and_priority.py`, `test_gmail_sync.py`, `test_health_and_db.py`, `test_items_api.py`).
- Frontend Build (`cd frontend; npm run build`): TypeScript check and Vite build passed in 164ms with 0 errors.

## Known Blockers
- None.

## Next Step
- Milestone 8 / Production Polish & Deployment.
