# ClassMail J - Project State

## Current Milestone
- Milestone 6: Google Classroom Read-Only Integration (read-only OAuth scopes, ClassroomClient, UTC due date parsing, deduplication, item updates with event logging, student submission status, background/manual sync, frontend UI badges and source links).

## Implemented Features
- **Backend Scaffold & SQLite Database**: FastAPI application with strict CORS, additive migrations, `items`, `sources`, `item_events`, `sync_history`, and persistent `settings` tables.
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
  - Loopback OAuth CLI (`python -m app.integrations.google_auth classroom`) saving to `CLASSROOM_TOKEN_FILE` with strictly 4 read-only scopes (courses, coursework, announcements, student submissions).
  - `ClassroomClient` wrapper querying active courses, coursework (with pagination), announcements, and student submission states.
  - Mapping coursework to `task`/`assignment`, `source_type='classroom'`, course title as sender, safe links, converting `dueDate` and `dueTime` (explicitly documented as UTC in Classroom v1 API) to confirmed UTC ISO timestamp. `dueDate`-only mapped to `ambiguous` without inventing a time.
  - Announcements mapped via `RulesProvider` (`source_id='ann:<courseId>:<announcementId>'`).
  - Updates: detects due date/time, title, and submission state changes, updates items, sets `deadline_changed_at`, logs `item_events`, and never reopens completed items.
  - Submission state tracking: stores student submission state in `source_status` and displays "Turned in per Classroom" badge without auto-completing items.
  - Sync endpoints & background integration: `POST /api/sync/classroom` and inclusion in scheduled background sync under shared concurrency lock.
- **Automatic Background Sync**:
  - APScheduler `BackgroundScheduler` running inside the FastAPI lifespan every `SYNC_INTERVAL_MINUTES` (default 5, `max_instances=1`, `coalesce=True`, jitter).
  - Shared concurrency lock (`threading.Lock`) between background runs and manual sync buttons (Gmail & Classroom) to eliminate overlaps.
  - Exponential backoff on network/quota errors (capped at 30 minutes).
  - Auth failure handling: stops retries, sets status `"reconnect_needed"`, and presents the exact re-authorization CLI command without crashing.
  - Persistent runtime enable/disable toggle stored in SQLite `settings` table surviving application restarts.
  - Endpoints: `GET /api/sync/status`, `POST /api/sync/settings`, `POST /api/sync/gmail`, and `POST /api/sync/classroom`.
- **Frontend Dashboard**:
  - React + Vite + TypeScript application with priority tabs, snooze presets, full CRUD lifecycle, manual paste modal, and re-analysis controls.
  - "📥 Sync Classroom now" and "📥 Sync Gmail now" buttons with live sync status and error banners.
  - Visual distinction with `Classroom` and `Gmail` source badges, direct links to Classroom assignments (`alternateLink`), and "Turned in per Classroom" submission status badge.
  - Header status showing "Auto-sync every N min", live sync state badge, and runtime pause/resume toggle.
  - Visible "Reconnect needed" alert banners detailing the exact re-auth command.
  - Periodic status updates with last run time, next run time, and item sync counts.

## Mocked vs Real
- **Database & Settings**: Real SQLite database (`backend/data/classmail.db`) and persistent `settings` table.
- **Scheduler**: Real APScheduler `BackgroundScheduler` in FastAPI lifespan and real threading locks.
- **Gmail Integration**: Real Gmail API client wrapper; unit tests use a `MockGmailClient` and `FakeClock` clearly documented as mocks (unit tests do not prove real Gmail works).
- **Classroom Integration**: Real Classroom API client wrapper (`app.integrations.classroom_client.ClassroomClient`); unit tests use a `MockClassroomClient` clearly documented as a mock (unit tests do not prove real Google Classroom works).

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

## Tests Last Run
- Backend Pytest (`cd backend; .\.venv\Scripts\python.exe -m pytest -v`): 69 passed across all test suites (`test_classroom_sync.py`, `test_background_sync.py`, `test_extraction_and_priority.py`, `test_gmail_sync.py`, `test_health_and_db.py`, `test_items_api.py`).
- Frontend Build (`cd frontend; npm run build`): TypeScript check and Vite build passed with 0 errors.

## Known Blockers
- None.

## Next Step
- Milestone 7: Reminders & Notification System.
