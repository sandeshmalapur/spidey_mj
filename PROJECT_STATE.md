# ClassMail J - Project State

## Current Milestone
- Milestone 5: Automatic Background Sync (Gmail sync scheduling with APScheduler, shared concurrency lock, exponential backoff, persistent settings, and frontend status widget).

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
- **Automatic Background Sync**:
  - APScheduler `BackgroundScheduler` running inside the FastAPI lifespan every `SYNC_INTERVAL_MINUTES` (default 5, `max_instances=1`, `coalesce=True`, jitter).
  - Shared concurrency lock (`threading.Lock`) between background runs and manual "Sync now" button to eliminate overlaps.
  - Exponential backoff on network/quota errors (capped at 30 minutes).
  - Auth failure handling: stops retries, sets status `"reconnect_needed"`, and presents the exact re-authorization CLI command without crashing.
  - Persistent runtime enable/disable toggle stored in SQLite `settings` table surviving application restarts.
  - Endpoints: `GET /api/sync/status`, `POST /api/sync/settings`, and `POST /api/sync/gmail`.
- **Frontend Dashboard**:
  - React + Vite + TypeScript application with priority tabs, snooze presets, full CRUD lifecycle, manual paste modal, and re-analysis controls.
  - Header status showing "Auto-sync every N min", live sync state badge, and runtime pause/resume toggle.
  - Visible "Reconnect needed" alert banner detailing the exact re-auth command.
  - Periodic status updates with last run time, next run time, and item sync counts.

## Mocked vs Real
- **Database & Settings**: Real SQLite database (`backend/data/classmail.db`) and persistent `settings` table.
- **Scheduler**: Real APScheduler `BackgroundScheduler` in FastAPI lifespan and real threading locks.
- **Gmail Integration**: Real Gmail API client wrapper; unit tests use a `MockGmailClient` and `FakeClock` clearly documented as mocks (unit tests do not prove real Gmail works).
- **Classroom Integration**: Not yet implemented (Classroom sync does not exist yet).

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
- `GMAIL_QUERY`
- `GMAIL_MAX_MESSAGES`
- `SYNC_INTERVAL_MINUTES`
- `SYNC_ENABLED`

## Tests Last Run
- Backend Pytest (`cd backend; .\.venv\Scripts\python.exe -m pytest -v`): 61 passed across all test suites (`test_background_sync.py`, `test_extraction_and_priority.py`, `test_gmail_sync.py`, `test_health_and_db.py`, `test_items_api.py`).
- Frontend Build (`cd frontend; npm run build`): TypeScript check and Vite build passed in 258ms with 0 errors.

## Known Blockers
- None.

## Next Step
- Milestone 6: Reminders & Notifications or Google Classroom read-only integration.
