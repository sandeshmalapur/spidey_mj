# ClassMail J

Local-first, deterministic AI email and academic assistant for students and educators. ClassMail J ingests messages and notices from Gmail and Google Classroom, deterministically extracts deadlines and task details, computes actionable priorities, and dispatches privacy-preserving push notifications via ntfy.

---

## Capabilities & Architecture

- **Rules-Based Extraction Engine**: Pre-normalizes raw text (strips markdown, HTML entities, zero-width characters) and parses complex dates, multi-day ranges, 12h/24h times, and durations ("45 minutes", "1.5 hours"). Durations never become deadlines.
- **Pure Priority Engine**: Computes effective priorities (`urgent`, `important`, `normal`) using deterministic thresholds (due within 48h, due within 14 days, ambiguous deadlines) with pure functions and an injectable clock.
- **Gmail Read-Only Integration**: Loopback OAuth 2.0 flow granting strictly read-only Gmail access (`https://www.googleapis.com/auth/gmail.readonly`). Deduplicates messages, extracts threads, and syncs history.
- **Google Classroom Read-Only Integration**: Loopback OAuth 2.0 flow querying active courses, coursework, announcements, and student submission state (`https://www.googleapis.com/auth/classroom.courses.readonly`, `https://www.googleapis.com/auth/classroom.coursework.me.readonly`, `https://www.googleapis.com/auth/classroom.announcements.readonly`, `https://www.googleapis.com/auth/classroom.student-submissions.me.readonly`). Correctly handles student `TURNED_IN` submission states.
- **Automatic Background Sync**: APScheduler job in the FastAPI lifespan running on a configurable interval with thread-safe concurrency locks, failure isolation, and exponential backoff (capped at 30 minutes).
- **Deterministic Reminders & Notifications**:
  - Time-critical one-shot `DateTrigger` reminders for assessment latest-start times (`deadline_at - duration_minutes`) and final submission nudges.
  - Periodic reminder tick with configurable quiet hours, hourly rate limits, and catch-up grace windows.
  - Push notifications dispatched via `ntfy` with generic, privacy-preserving alerts.
- **Security Hardening**:
  - `TrustedHostMiddleware` enforcing loopback-only connections (`localhost`, `127.0.0.1`, `testserver`).
  - Origin validation middleware rejecting state-changing (`POST`, `PATCH`, `DELETE`, `PUT`) cross-origin requests (HTTP 403 Forbidden).
  - Strict plain-text rendering: zero `dangerouslySetInnerHTML` or raw `innerHTML`. External links enforce `rel="noopener noreferrer"`.
  - Zero stored credentials in the codebase; secrets and tokens gitignored.

---

## Non-Goals & Privacy Guarantees

- **No Destructive Operations**: ClassMail J never modifies, sends, deletes, or alters messages, emails, or classroom assignments. All external Google API scopes are strictly read-only.
- **No Third-Party Cloud Data Leakage**: Ingested messages, extracted tasks, and user schedules reside entirely in your local SQLite database (`backend/data/classmail.db`).
- **Notification Privacy**: Outbound ntfy alerts contain only minimal, non-sensitive action prompts (e.g. "ClassMail J: 1 submission due soon, upload now"). Specific message bodies, full tokens, client secrets, and personal identifiers are never transmitted or logged.

---

## Prerequisites

- **Python**: 3.14+ (or 3.11+)
- **Node.js**: 20+ (Node 24/25 supported)
- **npm**: 10+
- **Google Cloud Project**: With Gmail API and Google Classroom API enabled and an OAuth 2.0 Desktop Client JSON file.
- **ntfy Account / Topic** (Optional): Any public or self-hosted ntfy topic.

---

## Project Structure

```
classmail-j/
├── backend/
│   ├── app/
│   │   ├── integrations/     # Google OAuth, Gmail client, Classroom client
│   │   ├── config.py         # Configuration & environment variables
│   │   ├── db.py             # SQLite connection & schema initialization
│   │   ├── demo_data.py      # Relative-date academic demo fixtures
│   │   ├── extraction.py     # Deterministic rules-based extraction
│   │   ├── main.py           # FastAPI application, security middlewares & API routes
│   │   ├── models.py         # Pydantic schemas & enums
│   │   ├── priority.py       # Deterministic priority engine
│   │   ├── reminders.py      # Reminders planner, tick runner & ntfy client
│   │   └── scheduler.py      # APScheduler manager & background sync jobs
│   ├── data/                 # SQLite database storage (gitignored)
│   ├── secrets/              # Google OAuth client & token files (gitignored)
│   ├── tests/                # Comprehensive unit and integration test suites
│   ├── requirements.txt      # Python dependencies
│   └── .venv/                # Python virtual environment
├── frontend/
│   ├── src/
│   │   ├── api.ts            # Typed backend API client
│   │   ├── App.tsx           # Dashboard UI, filters, modals & notifications
│   │   ├── App.css           # Styling
│   │   ├── main.tsx          # React application entry
│   │   └── types.ts          # TypeScript type definitions
│   ├── package.json
│   └── vite.config.ts
├── .env.example              # Environment variables template
├── .gitignore                # Git exclusions
├── DEMO_SCRIPT.md            # Step-by-step live demonstration guide
├── PROJECT_STATE.md          # Current system state & test audit
└── README.md
```

---

## Configuration & Environment Variables

Copy `.env.example` to `.env` in the root or `backend/` directory:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `DATABASE_PATH` | `backend/data/classmail.db` | Path to the SQLite database file |
| `FRONTEND_ORIGIN` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed frontend origins for CORS and state-changing request validation |
| `TIMEZONE` | `Asia/Kolkata` | Local timezone for date display and daily digest calculation |
| `DEFAULT_IST_MEANING` | `Asia/Kolkata` | Timezone resolution for ambiguous `IST` abbreviation |
| `AUTH_ENABLED` | `false` | Enable optional single-owner OpenID Connect authentication |
| `SESSION_SECRET` | `""` | 32+ character random secret used to sign session cookies |
| `OWNER_EMAIL` | `""` | Authorized Google account email permitted to access instance |
| `GOOGLE_WEB_CLIENT_FILE` | `backend/secrets/google_web_client.json` | Path to Google OAuth 2.0 Web application client JSON |
| `AUTH_REDIRECT_URI` | `http://127.0.0.1:8000/api/auth/google/callback` | OAuth callback URI configured in Google Cloud Console |
| `SESSION_TTL_HOURS` | `12` | Lifetime of server-side browser sessions in hours |
| `GOOGLE_CLIENT_FILE` | `backend/secrets/google_client.json` | Path to Google OAuth 2.0 Desktop client configuration |
| `GMAIL_TOKEN_FILE` | `backend/secrets/token_gmail.json` | Path to saved Gmail OAuth token file |
| `CLASSROOM_TOKEN_FILE` | `backend/secrets/token_classroom.json` | Path to saved Classroom OAuth token file |
| `GMAIL_QUERY` | `newer_than:7d in:inbox` | Query filter for fetching recent inbox emails |
| `GMAIL_MAX_MESSAGES` | `25` | Maximum messages fetched per Gmail sync run |
| `SYNC_INTERVAL_MINUTES` | `5` | Frequency of background synchronization runs |
| `SYNC_ENABLED` | `true` | Default enable state for automatic background sync |
| `SCHEDULER_ENABLED` | `true` | Enable/disable APScheduler background scheduler |
| `REMINDER_TICK_SECONDS` | `60` | Frequency of periodic reminder planning and dispatch ticks |
| `NOTIFICATIONS_ENABLED`| `true` | Master toggle for notification dispatch |
| `URGENT_MAX_REPEATS` | `3` | Maximum urgent escalation repeat notifications |
| `URGENT_REPEAT_MINUTES`| `30` | Interval between urgent escalation repeats |
| `DIGEST_HOUR` | `8` | Hour of day (0-23 in `TIMEZONE`) to dispatch daily digest for normal items |
| `QUIET_HOURS_START` | `22:00` | Start of quiet hours (local time) |
| `QUIET_HOURS_END` | `07:00` | End of quiet hours (local time) |
| `URGENT_BYPASS_QUIET` | `false` | Whether urgent reminders bypass quiet hours |
| `MAX_NOTIFICATIONS_PER_HOUR` | `5` | Global rate limit on notifications dispatched per rolling hour |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy server URL |
| `NTFY_TOPIC` | `""` | Secret topic name on ntfy (never committed or exposed) |
| `NTFY_INCLUDE_TITLE` | `false` | Include item title in ntfy push notification body |
| `ASSESSMENT_HEADSUP_MINUTES` | `30` | Advance heads-up notification before assessment latest-start time |
| `CATCHUP_GRACE_MINUTES` | `15` | Window to dispatch overdue reminders marked late before skipping |
| `REMINDER_OFFSETS_ASSIGNMENT_MINUTES` | `1440,180,45` | Minutes before deadline for assignment reminders (e.g. 24h, 3h, 45m) |
| `REMINDER_OFFSETS_EVENT_MINUTES` | `1440,60,15` | Minutes before start for event/interview reminders (e.g. 24h, 1h, 15m) |
| `EXTRACTION_PROVIDER` | `rules` | Extraction engine: `rules` (default) or `ollama` |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Local Ollama API server endpoint |
| `OLLAMA_MODEL` | `llama3:latest` | Local LLM model tag for extraction |
| `OLLAMA_TIMEOUT_SECONDS` | `60` | HTTP timeout for Ollama calls |
| `OLLAMA_MAX_INPUT_CHARS` | `6000` | Input character truncation limit for LLM prompt |
| `OLLAMA_MAX_ITEMS_PER_SYNC` | `5` | Maximum new items per sync processed with LLM (rest use rules) |
| `OLLAMA_ALLOW_REMOTE` | `false` | Security check: reject non-localhost URLs unless explicitly `true` |

---

## Setup & Google OAuth Authorization

### 1. Backend Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Google OAuth Desktop Client Configuration

1. Visit the [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project and enable both the **Gmail API** and **Google Classroom API**.
3. Under **OAuth consent screen**, set User Type to **External** and add your Google account email under **Test users**.
   > [!NOTE]
   > While in "Testing" mode, Google OAuth refresh tokens expire after **7 days**. When tokens expire, ClassMail J displays a "Reconnect Needed" prompt with the exact CLI command to re-authenticate.
4. Go to **Credentials** -> **Create Credentials** -> **OAuth Client ID** -> choose **Desktop app**.
5. Download the JSON credential file and save it as:
   `backend/secrets/google_client.json`

### 3. Optional Google Web OAuth Client (Sign-In & In-App Consent)

For optional browser sign-in and in-app connection management:
1. In Google Cloud Console under **Credentials** -> **Create Credentials** -> **OAuth Client ID**, choose **Web application**.
2. Add Authorized redirect URI:
   `http://127.0.0.1:8000/api/auth/google/callback`
3. Download the client JSON and save it as:
   `backend/secrets/google_web_client.json`
4. Set in your `.env`:
   ```bash
   AUTH_ENABLED=true
   SESSION_SECRET="your-random-secret-key-at-least-32-chars-long"
   OWNER_EMAIL="your-google-email@example.com"
   ```
   > [!IMPORTANT]
   > ClassMail J uses a **single-owner allowlist** design for local, self-hosted deployments. Multi-user data isolation is intentionally out of scope. If an account other than `OWNER_EMAIL` attempts to sign in, access is denied (HTTP 403) and no tokens or sessions are stored.

### 4. Optional Local LLM Extraction (Ollama)

To enable local LLM extraction enhancement:
1. Install [Ollama](https://ollama.ai) and pull your preferred model (e.g. `ollama run llama3:latest`).
2. Set in your `.env`:
   ```bash
   EXTRACTION_PROVIDER=ollama
   OLLAMA_MODEL=llama3:latest
   ```
3. ClassMail J will enrich titles, summaries, and action items using Ollama while deterministically enforcing rules deadlines and falling back to rules without disruption if Ollama is offline.

### 5. Run OAuth CLI Flows (Fallback)

Execute the loopback authentication CLI from your terminal (authentication never occurs inside a web request):

**For Gmail Read-Only Access:**
```powershell
cd backend
.\.venv\Scripts\python.exe -m app.integrations.google_auth gmail
```
*Opens your browser to sign in and grants read-only access to your Gmail inbox. Saves token to `backend/secrets/token_gmail.json`.*

**For Google Classroom Read-Only Access:**
```powershell
cd backend
.\.venv\Scripts\python.exe -m app.integrations.google_auth classroom
```
*Opens your browser to grant read-only access to your active courses, coursework, and announcements. Saves token to `backend/secrets/token_classroom.json`.*

---

## Running the Application

### 1. Start the Backend Server
Run Uvicorn directly without `--reload` for production stability (or with `--reload` during development):

```powershell
cd backend
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```
Backend API documentation is available at `http://127.0.0.1:8000/docs` and diagnostic health check at `http://127.0.0.1:8000/api/health`.

### 2. Start the Frontend Application

```powershell
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173` in your browser.

---

## Testing & Verification Commands

### 1. Run Complete Backend Test Suite
```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```
*Executes all 110 tests across 9 suites covering unit parsing, priority computation, Gmail sync, Classroom sync, scheduler concurrency, ntfy dispatch, and the end-to-end integration lifecycle.*

### 2. Run Single End-to-End Test Suite
```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -v tests/test_e2e_integration.py
```

### 3. Build Frontend
```powershell
cd frontend
npm run build
```

### 4. Frontend Security Audit
```powershell
cd frontend
npm audit
```

---

## Live Demo Quick-Start

1. **System Status**: Click the **System Status** button in the dashboard header to verify that database connectivity, OAuth tokens, and APScheduler are healthy.
2. **Reset Demo Data**: Click **Reset Demo Data** in the top toolbar to seed relative-date academic fixtures (midterm exam with 45 min duration and same-day assignment).
3. **Inspect Assessment**: Notice the "Start by" start-time badge (`deadline_at - 45 min`) and duration indicator on the card.
4. **Trigger Sync**: Click **Sync Gmail now** or **Sync Classroom now** to pull new live items.
5. Refer to [DEMO_SCRIPT.md](file:///d:/programs/Projects/classmail-j/DEMO_SCRIPT.md) for the full 6-minute walkthrough.

---

## Limitations & Future Scope

- **Google Cloud Testing Mode Expiry**: In developer testing mode, tokens must be refreshed every 7 days via the provided CLI command.
- **Local Network Scope**: The web dashboard is secured for local loopback use (`127.0.0.1`).
- **Optional Local LLM (Ollama) Provider**: ClassMail J currently uses a deterministic, rule-based extraction pipeline that requires no GPU or external API keys. An optional local Ollama LLM provider (e.g. `llama3.2`) for freeform natural language summarization is cataloged under future scope.
