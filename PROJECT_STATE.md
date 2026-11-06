# ClassMail J - Project State

## Current Milestone
- Milestone 10: Google Sign-In (OpenID Connect) & Unified Data Consent (optional single-owner authentication, authorization-code flow with PKCE, server-side session cookies, unified data consent requesting Gmail and Classroom read-only scopes, automatic background first sync, per-source connection state, Google token revocation, and CLI fallback).

## Implemented Features
- **Google OpenID Connect (OIDC) & Unified Data Consent**:
  - Optional single-owner authentication toggleable via `AUTH_ENABLED` (default `false` keeps application completely unauthenticated for local development).
  - Authorization-code flow with PKCE (`code_verifier`, `code_challenge` via S256) and single-use server-side `state` and `nonce` stored in SQLite `auth_states` table.
  - Unified single-consent flow requesting identity (`openid`, `email`, `profile`) plus read-only Gmail (`gmail.readonly`) and read-only Google Classroom scopes in one request with `access_type=offline`, `include_granted_scopes=true`, and `prompt=consent`.
  - Secure callback (`GET /api/auth/google/callback`): validates state, exchanges code with PKCE, verifies Google ID token (signature, issuer `accounts.google.com`, audience, expiry, nonce match, verified email), and restricts access strictly to `OWNER_EMAIL`.
  - Non-owner attempts receive a generic 403 Forbidden page and store nothing.
  - Inspects granted scopes and saves `token_gmail.json` and `token_classroom.json` at existing paths in the authorized user credentials format read by existing sync clients.
  - Automatic non-blocking first sync triggered immediately after successful callback for granted sources.
  - Server-side sessions table and signed `HttpOnly`, `SameSite=Lax` session cookie with constant-time HMAC-SHA256 signature verification.
  - Endpoints: `GET /api/auth/google/start`, `GET /api/auth/google/callback`, `GET /api/auth/me`, `POST /api/auth/logout`, `GET /api/connections`, `POST /api/connections/disconnect`.
  - Token revocation endpoint integration with `https://oauth2.googleapis.com/revoke`.
  - UI integration: dedicated signed-out screen when auth enabled, owner identity badge and sign-out in header, and per-source connection status strip ("Gmail connected", "Classroom not granted", "Reconnect needed") with in-app reconnect buttons and CLI fallback.
- **Security & Network Hardening**:
  - `TrustedHostMiddleware` restricting host headers strictly to loopback and local development hosts (`localhost`, `127.0.0.1`, `testserver`).
  - Origin validation middleware (`check_origin_middleware`) rejecting state-changing HTTP requests (`POST`, `PATCH`, `DELETE`, `PUT`) with unapproved Origin headers (HTTP 403 Forbidden).
  - Secret protection audit: git scan confirmed zero committed `.env`, token, database, or secret files. Plain text rendering audited with zero `dangerouslySetInnerHTML` or raw `innerHTML`. Outbound external links strictly use `rel="noopener noreferrer"`.
  - Frontend security audit: `npm audit` returned 0 vulnerabilities.
- **Backend Scaffold & SQLite Database**: FastAPI application with strict CORS, additive migrations, `items` (with `duration_minutes` and `source_status`), `sources`, `item_events`, `sync_history`, `settings`, `reminders`, and `notification_log` (with `scheduled_for` and `delay_seconds`) tables.
- **Rules Extraction & Priority Engine**:
  - Deterministic rules-based extraction with pre-normalization (stripping markdown, HTML entities, and zero-width characters while preserving original stored evidence).
  - Hardened candidate date/time parser: optional weekdays, ISO dates, multi-format calendar dates, 12h/24h times, ranges, and configurable `DEFAULT_IST_MEANING`.
  - Actionable classification for interviews, meetings, events, and assessments.
  - Kind `assessment` (cues: test, quiz, exam, assessment, timed; item_type task).
  - Duration extraction: parses "45 minutes", "45-minute", "45 mins", "1 hour", "1.5 hours", "1h 30m", "duration: 45 min", "time limit 30 min" with sanity range 5-600 minutes. Duration is never a deadline.
  - Re-analysis endpoints (`POST /api/items/{id}/reanalyze` and `POST /api/reanalyze`) preserving user-edited fields (including duration) tracked via `user_edited_fields`.
- **Gmail Read-Only Integration**:
  - OAuth Desktop loopback auth CLI (`python -m app.integrations.google_auth gmail`).
  - Read-only GmailClient fetching plain-text / sanitized HTML bodies, deduplicating messages, updating thread-linked deadlines, and recording item events.
- **Google Classroom Read-Only Integration**:
  - Loopback OAuth CLI (`python -m app.integrations.google_auth classroom`) saving to `CLASSROOM_TOKEN_FILE` with strictly read-only scopes.
  - `ClassroomClient` wrapper querying active courses, coursework (with pagination), announcements, and student submission states.
  - Accurate UTC conversion for `dueDate` and `dueTime`.
- **Automatic Background Sync**:
  - APScheduler `BackgroundScheduler` running inside the FastAPI lifespan every `SYNC_INTERVAL_MINUTES`.
  - Shared concurrency lock (`threading.Lock`) between background runs and manual sync buttons.
  - Individual job isolation: background Gmail and Classroom failures are isolated so one failure never blocks the other.
  - Exponential backoff on network/quota errors (capped at 30 minutes).
  - Auth failure handling with exact re-auth command.
  - Endpoints: `GET /api/sync/status`, `POST /api/sync/settings`, `POST /api/sync/gmail`, and `POST /api/sync/classroom`.
- **Deterministic Reminders & Timing Refinements**:
  - Reminder tick job executed every `REMINDER_TICK_SECONDS` (default 60s) via the shared APScheduler instance with an injectable clock.
  - Exact timing with one-shot `DateTrigger` APScheduler jobs for time-critical reminders (`latest_start` and final submission nudge), automatically re-created at startup via `rebuild_oneshot_reminder_jobs()`.
  - Assessment reminders:
    - Confirmed deadline + duration schedules kind `latest_start` at `deadline_at - duration_minutes`. Minimal text: "ClassMail J: start your assessment now to finish before the deadline".
    - Optional heads-up at `latest_start - ASSESSMENT_HEADSUP_MINUTES` (default 30; 0 disables). Kind `assessment_headsup`.
    - If `latest_start` has passed but deadline has not: sends ONE immediate "latest start has passed, N minutes remain" message. If deadline has passed, sends nothing.
    - If assessment has no duration: falls back to standard assignment offsets and UI prompts to add duration.
  - Submission reminders:
    - Configurable offsets: `REMINDER_OFFSETS_ASSIGNMENT_MINUTES="1440,180,45"` and `REMINDER_OFFSETS_EVENT_MINUTES="1440,60,15"` (at most 3 deadline reminders per item).
    - If item is first seen inside window: sends ONLY the single nearest remaining reminder immediately (no backlog).
    - Final-offset text: "ClassMail J: 1 submission due soon, upload now" with priority urgent.
  - Time-critical rules: `latest_start` and final submission nudge ignore quiet hours and snooze, are never collapsed away or deferred by the frequency cap (still count toward cap), and use ntfy priority urgent.
  - Catch-up grace handling: if overdue by `<= CATCHUP_GRACE_MINUTES` (default 15), sent marked `(late, N min)`; if overdue by `> CATCHUP_GRACE_MINUTES`, marked `skipped` with reason `catchup_grace_exceeded`. Delay seconds and scheduled time recorded in `notification_log`.
  - Suppression: cancels and suppresses deadline reminders when state is completed/cancelled OR when `source_status` is `TURNED_IN`. Acknowledging stops escalation repeats but never cancels `latest_start` or final submission nudge. Any deadline, duration, or state change replaces pending schedule.
  - Ntfy Publisher: HTTP POST of minimal generic plain-text with 5s timeout and ASCII headers. Never logs or exposes `NTFY_TOPIC`.
- **Frontend Dashboard**:
  - React + Vite + TypeScript application with priority tabs, snooze presets, full CRUD lifecycle, manual paste modal, and re-analysis controls.
  - Clear source badges across all item cards and detail view: `Demo`, `Manual`, `Gmail`, and `Classroom`.
  - Prominent top warning banner displayed whenever demo data is active.
  - "Reset Demo Data" action (`POST /api/demo/reset`) clearing and reloading fresh relative-date demo items (including same-day 45-min assessment and same-day 5h assignment).
  - Diagnostic "System Status" panel modal (`GET /api/system/status`) reporting local database health, token presence booleans, ntfy status, and scheduler state with zero secrets exposed.
  - Item edit modal supporting duration (minutes) editing and tracking in `user_edited_fields`.
  - Assessment cards showing "Start by 1:15 PM" (or "Add duration to get a start-time reminder") and "Duration 45 min".
  - Item detail view featuring a live "Next reminders" list with exact scheduled times and kinds (via `GET /api/items/{id}/reminders`).
  - Notification Center modal displaying scheduled time and delivery delay (`on-time` vs `+{N}s`) for each notification entry.

## Mocked vs Real
- **Database & Settings**: Real SQLite database (`backend/data/classmail.db`), additive schema migrations, and persistent `settings` table.
- **Scheduler**: Real APScheduler `BackgroundScheduler` in FastAPI lifespan with real threading locks and exact `DateTrigger` one-shot jobs.
- **Gmail Integration**: Real Gmail API client wrapper; unit tests use a `MockGmailClient` and `FakeClock` clearly documented as mocks (unit tests do not prove real Gmail works).
- **Classroom Integration**: Real Classroom API client wrapper; unit tests use a `MockClassroomClient` clearly documented as a mock (unit tests do not prove real Google Classroom works).
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
- `ASSESSMENT_HEADSUP_MINUTES`
- `CATCHUP_GRACE_MINUTES`
- `REMINDER_OFFSETS_ASSIGNMENT_MINUTES`
- `REMINDER_OFFSETS_EVENT_MINUTES`

## Tests Last Run
- Backend Pytest (`cd backend; .\.venv\Scripts\python.exe -m pytest -q`): 110 passed across all 9 test suites (`test_e2e_integration.py`, `test_assessment_reminders.py`, `test_reminders_and_notifications.py`, `test_classroom_sync.py`, `test_background_sync.py`, `test_extraction_and_priority.py`, `test_gmail_sync.py`, `test_health_and_db.py`, `test_items_api.py`).
- Frontend Build (`cd frontend; npm run build`): TypeScript compile and Vite production build passed in 229ms with 0 errors.
- Frontend Security (`cd frontend; npm audit`): 0 vulnerabilities found.

## Known Blockers
- None.

## Next Step
- Complete live demonstration using `DEMO_SCRIPT.md`.
