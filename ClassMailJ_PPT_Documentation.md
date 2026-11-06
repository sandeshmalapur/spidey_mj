# ClassMail J — Complete Project Documentation for PPT

> **Purpose**: This document contains all content, data, and information needed to build a professional PowerPoint presentation for ClassMail J. Use this as source material in Claude or any AI tool.

---

## PROJECT IDENTITY

- **Project Name**: ClassMail J
- **Tagline**: Local-First Academic AI Assistant for Students
- **Version**: v0.5.0
- **Type**: Full-stack web application (single-owner, local deployment)
- **Target Users**: Students and educators who use Gmail and Google Classroom

---

## 1. PROBLEM STATEMENT

Students and educators face a critical productivity problem every day:

- **Deadlines are buried** in long email threads and Classroom announcements — easy to miss.
- **No single unified view** exists across Gmail, Google Classroom, and other academic sources.
- **Manual tracking** via spreadsheets or notes is error-prone and time-consuming.
- **No proactive alerts** — students discover missed deadlines only after they have passed.
- **Privacy concerns** — sending sensitive academic emails to cloud-based AI tools is risky.
- **Assessment timing** — a reminder at 2:00 PM for a 2:00 PM exam is useless; students need to be reminded to *start* on time.

**Result**: Missed deadlines, lower grades, unnecessary stress, and loss of academic standing.

---

## 2. SOLUTION OVERVIEW

ClassMail J is a **local-first, deterministic AI academic assistant** that runs 100% on the student's own machine. It:

1. **Reads** — Syncs Gmail and Google Classroom using read-only OAuth. Never modifies or deletes anything.
2. **Extracts** — Deterministically parses deadlines, durations, and task types from email text using a rules engine (and optionally a local LLM via Ollama).
3. **Prioritises** — Computes Urgent / Important / Normal priority using a pure, testable engine. Recalculates every 60 seconds as time passes.
4. **Alerts** — Dispatches push notifications via ntfy (phone/desktop) and optional voice calls via Sarvam AI for urgent items.

**Key principle**: All data stays on the user's machine. No email content, tokens, or personal data ever leaves the local SQLite database.

---

## 3. SYSTEM ARCHITECTURE

### Deployment Topology
```
Browser (localhost:5173)
        |
        | HTTP REST API
        v
FastAPI Backend (127.0.0.1:8000)
        |
        |--- SQLite DB (backend/data/classmail.db)
        |--- OAuth Tokens (backend/secrets/)
        |--- APScheduler (sync + reminders, runs in-process)
        |
        |---> Gmail API (read-only)
        |---> Google Classroom API (read-only)
        |---> ntfy.sh (push notifications)
        |---> Sarvam AI API (voice calls, optional)
        |---> Ollama (local LLM, optional)
```

### Key Design Decisions
- **Single host, loopback only**: TrustedHostMiddleware restricts all traffic to `127.0.0.1` / `localhost`.
- **No ORM**: Raw SQLite3 (Python stdlib) for complete schema control and zero extra dependencies.
- **No cloud database**: All storage is local SQLite — no Firebase, no Supabase, no Postgres.
- **APScheduler embedded**: Background sync and reminder jobs run inside the FastAPI lifespan — no Celery, no Redis, no separate worker process.
- **Single-owner design**: One person's local machine. Multi-user isolation is intentionally out of scope.

---

## 4. TECHNOLOGY STACK

| Layer | Technology | Version / Notes |
|---|---|---|
| Backend Language | Python | 3.14 |
| Backend Framework | FastAPI + Uvicorn | ASGI, async-ready |
| Background Jobs | APScheduler | In-process scheduler |
| HTTP Client | httpx | Async (Gmail, Sarvam, Ollama calls) |
| Google Auth | google-auth + google-auth-oauthlib | OAuth 2.0 + OIDC verification |
| Database | SQLite (Python stdlib) | No ORM, raw sql3 |
| Data Validation | Pydantic v2 | Strict models and enums |
| Frontend Language | TypeScript | Strict mode |
| Frontend Framework | React 19 | Hooks, functional components |
| Frontend Build | Vite 8 | Production build in ~150ms |
| Styling | Plain CSS | No Tailwind, no component library |
| Testing | pytest + FastAPI TestClient | 129+ tests |
| Push Notifications | ntfy.sh | HTTP publish API |
| Voice Calls | Sarvam AI | Optional, urgent items only |
| Local LLM | Ollama | Optional extraction provider |

---

## 5. CORE FEATURES (DETAILED)

### Feature 1: Deterministic Deadline Extraction Engine

The extraction engine uses pure rules — no AI required, no network, fully auditable.

**How extraction works:**
- Pre-normalizes raw text: strips HTML tags, markdown, HTML entities, zero-width characters.
- Preserves original text as evidence for audit.
- Parses complex date formats: ISO dates, multi-format calendar dates, weekday names, ranges.
- Parses 12h/24h times, timezone abbreviations (IST → Asia/Kolkata by default).
- Detects duration phrases: "45 minutes", "1.5 hours", "1h 30m", "time limit 30 min", "duration: 45 min".
- **Duration is NEVER treated as a deadline** — this is a strict, explicit rule.
- Deadline confidence levels:
  - **Confirmed**: explicit date + time + timezone → stored as exact UTC.
  - **Tentative**: date only → time inferred as 23:59 local.
  - **None**: vague phrases like "soon", "upcoming" → no deadline stored.

**Re-analyze**: Users can re-run extraction on any item without overwriting fields they manually edited. The system tracks `user_edited_fields` per item.

---

### Feature 2: Pure Priority Engine

**Three priority tiers:**
- **Urgent** — deadline within 48 hours (configurable via `URGENT_WITHIN_HOURS`)
- **Important** — deadline within 14 days (configurable via `IMPORTANT_WITHIN_DAYS`)
- **Normal** — deadline beyond 14 days or no confirmed deadline

**Key properties:**
- Pure functions with an injectable clock — 100% deterministically testable.
- Priority recalculated every 60 seconds by the reminder tick (time passes, urgency increases automatically).
- Priority reasons displayed in the UI: "Due in 2h", "Due in 3 days", "No confirmed deadline".
- Items TURNED_IN in Classroom → reminders suppressed regardless of priority.

---

### Feature 3: Gmail & Google Classroom Integration

**Gmail:**
- OAuth 2.0 PKCE flow, scope: `gmail.readonly` only.
- Fetches last 7 days from inbox (configurable query).
- Maximum 25 messages per sync run (configurable).
- Deduplication by Gmail message ID — no duplicate items created.
- Sync every 5 minutes automatically (configurable interval).
- Tokens stored in `backend/secrets/token_gmail.json` (gitignored).

**Google Classroom:**
- OAuth 2.0 PKCE flow, four read-only scopes:
  - `classroom.courses.readonly`
  - `classroom.coursework.me.readonly`
  - `classroom.student-submissions.me.readonly`
  - `classroom.announcements.readonly`
- Fetches active courses, upcoming coursework, and announcements.
- **TURNED_IN detection**: Checks submission state. If student already submitted → badge "Turned in per Classroom" → all reminders cancelled.
- Accurate UTC conversion of Classroom's `dueDate` + `dueTime` fields.
- Tokens stored in `backend/secrets/token_classroom.json` (gitignored).

**In-app connect**: "Connect Gmail" and "Connect Classroom" buttons in the UI trigger the full PKCE OAuth flow — no command line needed after initial setup.

**Fallback**: If tokens expire, ClassMail J shows a "Reconnect Needed" banner with the exact CLI command to re-authenticate.

---

### Feature 4: Autonomous Reminders via ntfy

**How reminders work:**
- APScheduler tick runs every 60 seconds.
- Each tick: recomputes priority for all active items, then plans and dispatches reminders.
- One-shot `DateTrigger` jobs for time-critical reminders — fires at an exact UTC time.

**Reminder kinds:**
| Kind | When Sent | Notes |
|---|---|---|
| `standard` | 24h, 3h, 45min before deadline (assignments) | Configurable offsets |
| `standard` | 24h, 1h, 15min before deadline (events) | Configurable offsets |
| `latest_start` | `deadline_at − duration_minutes` exactly | Assessment-specific |
| `assessment_headsup` | `latest_start − 30min` | Optional heads-up before latest start |
| `submission` | 15min before deadline | Final nudge |
| `urgent_repeat` | Every 30min for urgent items | Max 3 repeats |
| `digest` | Daily at 8:00 AM | For Normal priority items |

**Safety mechanisms:**
- **Quiet hours**: 22:00 – 07:00 (configurable). Urgent items can bypass.
- **Rate limit**: Max 5 notifications per rolling hour.
- **Dedup key**: Prevents sending the same reminder twice across ticks.
- **Demo isolation**: Demo items never reach ntfy (`NOTIFY_DEMO_ITEMS=false` default).
- **Runtime toggle**: `NOTIFICATIONS_ENABLED` stored in settings table, changeable without restart.

**ntfy publish format**: `POST https://ntfy.sh/{TOPIC}` with headers for title, priority, and tags. Message body is generic ("ClassMail J: 1 submission due soon, upload now") — no personal data in notification body.

---

### Feature 5: Assessment Latest-Start Reminder (Smart Timing)

This is one of the most innovative features:

**Problem**: A reminder at exam time is useless — you need to start *before* the deadline.

**Solution**: For any item with:
- A confirmed `deadline_at` (e.g., 2:00 PM)
- A `duration_minutes` (e.g., 45 minutes)

ClassMail J schedules a `latest_start` reminder at exactly `deadline_at − duration_minutes` = **1:15 PM**.

This is scheduled as an exact one-shot APScheduler `DateTrigger` job. If the latest-start time has already passed when first seen, ClassMail J sends one immediate message: "Latest start has passed, N minutes remain."

**UI display**: Card shows "Start by 1:15 PM" and "Duration: 45 min". Detail view shows the planned reminder at exactly 1:15 PM.

**Duration extraction examples** (all correctly parsed):
- "45 minutes", "45-minute quiz", "45 mins"
- "1 hour", "1.5 hours", "1h 30m"
- "duration: 45 min", "time limit 30 min"
- Sanity range: 5 to 600 minutes only.

---

### Feature 6: Sarvam AI Voice Call for Urgent Alerts (NEW)

For the most critical deadlines, ClassMail J can place an **outbound voice phone call** using Sarvam AI.

- **Sarvam AI**: Indian voice AI platform (sarvam.ai), supports Hindi and English.
- **Trigger**: When an item reaches `urgent` priority.
- **API**: `POST https://api.sarvam.ai/v1/calls` with caller number, recipient number, and message.
- **Rate limit**: Max 3 calls per hour (configurable) — prevents call spam.
- **Demo isolation**: Demo items are never called.
- **Fully optional**: `SARVAM_VOICE_ENABLED=false` (default) — the app works identically without it.
- **Test endpoint**: `POST /api/sarvam/test-call` for manual testing.
- **Status endpoint**: `GET /api/sarvam/status` shows enabled state and call count.
- **UI**: "Voice Calls (Sarvam AI)" row in System Diagnostics modal with status badge and "Test Call" button.

**Configuration:**
```
SARVAM_VOICE_ENABLED=true
SARVAM_API_KEY=<your Sarvam API key>
SARVAM_FROM_NUMBER=+91226...  (Sarvam-provisioned number)
SARVAM_TO_NUMBER=+919876543210  (recipient phone, E.164 format)
SARVAM_AGENT_ID=  (optional pre-built agent)
SARVAM_MAX_CALLS_PER_HOUR=3
```

---

### Feature 7: Optional Ollama LLM Extraction

For richer natural language understanding, ClassMail J supports a local LLM via Ollama as an alternative extraction provider.

- **Provider interface**: `RulesProvider` and `OllamaProvider` both implement `ExtractionProvider`. Switching is one env var change.
- **Extraction flow**: Rules baseline computed first → Ollama enriches title/summary/actions → Strict merge policy (Ollama can NEVER set deadline_at or priority).
- **Hallucination guardrail**: `deadline_phrase` returned by LLM must be an exact substring of the original email text. Hallucinated phrases are discarded.
- **Prompt security**: Email content placed inside `<UNTRUSTED_DATA>` delimiters with explicit instruction to treat as untrusted data.
- **Fallback**: If Ollama is offline, item is processed by rules — no errors, no crashes.
- **Per-sync cap**: Max 5 items per sync run use Ollama (configurable). Rest use rules.
- **Never auto-downloads models** — model must exist locally.
- **Remote safety**: Non-localhost Ollama URLs rejected unless `OLLAMA_ALLOW_REMOTE=true`.
- **Status**: `GET /api/ollama/status` returns connection state, model availability, and list of installed models.

---

### Feature 8: Optional Google OIDC Single-Owner Sign-In

When `AUTH_ENABLED=true`:

- Full OpenID Connect flow with PKCE (`code_verifier` + `code_challenge` via S256).
- Single-use `state` and `nonce` stored server-side in SQLite `auth_states` table with TTL.
- **Unified consent**: One browser popup grants identity (openid, email, profile) + Gmail + Classroom access simultaneously.
- **Owner-only access**: Only `OWNER_EMAIL` (case-insensitive) is allowed. Any other Google account gets HTTP 403 — nothing stored.
- **ID token verification**: Signature, issuer (`accounts.google.com`), audience, expiry, nonce match, `email_verified` flag — all checked.
- **Session security**: HttpOnly, SameSite=Lax cookie with HMAC-SHA256 signed session ID. Session stored in SQLite `sessions` table.
- **Startup validation**: Refuses to start with `AUTH_ENABLED=true` if `SESSION_SECRET` is missing or shorter than 32 characters.

When `AUTH_ENABLED=false` (default): The app runs with no authentication — identical to previous behavior.

---

### Feature 9: Security & Privacy Architecture

| Control | Implementation |
|---|---|
| Local-only network | `TrustedHostMiddleware` — only `localhost` and `127.0.0.1` |
| CSRF protection | Origin validation middleware — rejects POST/PATCH/DELETE from unapproved origins (HTTP 403) |
| Read-only Gmail | Scope: `gmail.readonly` only — cannot read drafts, send, or delete |
| Read-only Classroom | Four read-only scopes only — cannot modify assignments or grades |
| No XSS | Zero `dangerouslySetInnerHTML` or raw `innerHTML` in React |
| External links | All `rel="noopener noreferrer"` |
| Secrets in git | Zero committed `.env`, tokens, or database files (gitignored) |
| NTFY privacy | Topic never logged or printed. Notification body is generic — no email content |
| Demo isolation | Demo items never reach ntfy or Sarvam AI |
| No telemetry | Zero analytics, no outbound calls except Gmail, Classroom, ntfy, Sarvam (all opt-in) |
| npm audit | 0 vulnerabilities |

---

## 6. ITEM LIFECYCLE

```
Email/Classroom/Manual Text
        |
        v
Ingest & Extract (Rules or Ollama)
        |
        v
Item Created in SQLite
  - title, summary, deadline_at, deadline_status
  - item_type (task/event), kind (assignment/assessment/etc.)
  - duration_minutes (if found)
  - source (gmail/classroom/manual/demo)
  - priority (urgent/important/normal)
        |
        v
Priority Recalculated every 60s tick
        |
        v
Reminders Planned & Dispatched (ntfy / Sarvam AI)
        |
        v
User Actions:
  - Acknowledge → stops escalation repeats
  - Snooze → defers reminders (2h, 4h, tomorrow, etc.)
  - Start → item moves to in_progress state
  - Complete → all reminders cancelled
  - Reopen → restores active state
  - Edit → update title, deadline, duration (tracked in user_edited_fields)
  - Re-analyze → re-runs extraction without overwriting user edits
```

---

## 7. DATABASE SCHEMA (TABLES)

| Table | Purpose |
|---|---|
| `items` | All ingested tasks/events with full metadata |
| `sources` | Deduplication tracking by external message ID |
| `item_events` | Audit log of all changes to items |
| `sync_history` | Record of every sync run (Gmail/Classroom) |
| `settings` | Runtime toggles (notifications_enabled, auto_sync_enabled) |
| `reminders` | Planned reminders (id, item_id, kind, scheduled_for, status, dedupe_key) |
| `notification_log` | Record of every dispatch with timing and delay |
| `auth_states` | PKCE state+nonce storage with TTL (AUTH_ENABLED only) |
| `sessions` | Server-side session storage (AUTH_ENABLED only) |

---

## 8. API ENDPOINTS REFERENCE

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Health check — database status and version |
| GET | `/api/items` | List items with priority/state/type filters |
| POST | `/api/ingest/manual` | Ingest raw text (email paste) |
| PATCH | `/api/items/{id}` | Update item fields (title, deadline, duration, etc.) |
| POST | `/api/items/{id}/acknowledge` | Mark aware — stops escalation |
| POST | `/api/items/{id}/snooze` | Snooze until a given UTC time |
| POST | `/api/items/{id}/start` | Move to in_progress |
| POST | `/api/items/{id}/complete` | Complete — cancels all reminders |
| POST | `/api/items/{id}/reopen` | Reopen from completed/cancelled |
| POST | `/api/items/{id}/reanalyze` | Re-run extraction preserving user edits |
| GET | `/api/items/{id}/reminders` | List scheduled reminders for an item |
| POST | `/api/reanalyze` | Re-analyze all active items |
| POST | `/api/sync/gmail` | Trigger manual Gmail sync |
| POST | `/api/sync/classroom` | Trigger manual Classroom sync |
| GET | `/api/sync/status` | Sync state and last-run timestamps |
| POST | `/api/sync/settings` | Toggle auto-sync on/off |
| GET | `/api/notifications` | Notification log (last 50) |
| GET | `/api/notifications/status` | Notification settings and stats |
| POST | `/api/notifications/test` | Send a test ntfy notification |
| POST | `/api/notifications/settings` | Toggle notifications on/off |
| GET | `/api/system/status` | Full system diagnostics |
| GET | `/api/ollama/status` | Ollama connection and model status |
| GET | `/api/sarvam/status` | Sarvam AI voice call status |
| POST | `/api/sarvam/test-call` | Place a test voice call |
| POST | `/api/demo/seed` | Seed demo data |
| POST | `/api/demo/reset` | Clear and re-seed demo data |
| DELETE | `/api/demo` | Delete all demo items |
| GET | `/api/auth/google/start` | Start OIDC sign-in flow |
| GET | `/api/auth/google/callback` | OIDC callback (PKCE validation) |
| GET | `/api/auth/me` | Current session identity |
| POST | `/api/auth/logout` | Invalidate session |
| GET | `/api/connections` | Gmail/Classroom connection status |
| POST | `/api/connections/disconnect` | Revoke a connection token |

---

## 9. FRONTEND FEATURES

The frontend is a **single-page React application** with no routing library — all state managed in one `App.tsx`.

**Dashboard panels:**
- **Priority Tabs**: Urgent / Important / Normal / Completed — click to filter.
- **Item Cards**: Source badge (Demo/Manual/Gmail/Classroom), priority badge, deadline, "Start by" time for assessments, "Duration" chip, state controls.
- **Item Detail Modal**: Full metadata, reminder schedule, re-analyze button, edit form.
- **System Diagnostics Modal**: Database health, Gmail/Classroom token status, ntfy status, Ollama status, Sarvam AI status, scheduler state — no secrets exposed.
- **Notifications Modal**: Notification log with scheduled times and delivery delay tracking.
- **Manual Ingest Modal**: Paste any email or announcement text for immediate extraction and ingestion.
- **Snooze Presets**: 1h, 2h, 4h, Tonight, Tomorrow, Next Week.
- **Demo Banner**: Prominent warning when demo data is active ("Demo data active: Simulated academic items are loaded").

**Design language (Auralis SaaS-inspired):**
- Off-white background (`#F7F7F5`)
- Black primary typography, muted grey secondary text
- Blue accent color
- Rounded cards, pill-shaped buttons
- Subtle borders and soft shadows
- Clean SVG stroke icons — zero emojis
- Geist font family

---

## 10. TESTING

| Suite | Coverage |
|---|---|
| `test_e2e_integration.py` | End-to-end: ingest → extract → priority → reminder → acknowledge/snooze/complete |
| `test_assessment_reminders.py` | Latest-start calculation, duration extraction, assessment timing |
| `test_reminders_and_notifications.py` | Quiet hours, rate cap, dedup, ntfy dispatch |
| `test_classroom_sync.py` | Classroom API mock, TURNED_IN suppression |
| `test_background_sync.py` | Scheduler concurrency, backoff, failure isolation |
| `test_extraction_and_priority.py` | Rules parser, deadline confidence, priority engine |
| `test_gmail_sync.py` | Gmail dedup, body parsing, event recording |
| `test_health_and_db.py` | Database migrations, health endpoint |
| `test_items_api.py` | CRUD lifecycle, state machine, PATCH validation |
| `test_ollama_provider.py` | OllamaProvider, hallucination guardrail, fallback |

**Total: 129 passed. 0 failures. 0 vulnerabilities (npm audit).**

**Test infrastructure:**
- `FakeClock` — injectable clock for deterministic time-based tests.
- `MockGmailClient`, `MockClassroomClient` — documented as mocks (do not prove real API connectivity).
- `MockNtfyClient` — captures dispatch calls without network.
- Temporary in-memory SQLite for each test — no shared state.

---

## 11. CONFIGURATION REFERENCE

All configuration via environment variables. Copy `.env.example` to `.env`.

**Core:**
```
DATABASE_PATH=backend/data/classmail.db
TIMEZONE=Asia/Kolkata
FRONTEND_ORIGIN=http://localhost:5173,http://127.0.0.1:5173
```

**Google / OAuth:**
```
GOOGLE_CLIENT_FILE=backend/secrets/google_client.json
GOOGLE_WEB_CLIENT_FILE=backend/secrets/google_web_client.json
AUTH_ENABLED=false
SESSION_SECRET=<32+ chars random secret>
OWNER_EMAIL=you@gmail.com
```

**Sync:**
```
SYNC_INTERVAL_MINUTES=5
SYNC_ENABLED=true
SCHEDULER_ENABLED=true
GMAIL_MAX_MESSAGES=25
```

**Reminders & Notifications:**
```
REMINDER_TICK_SECONDS=60
NOTIFICATIONS_ENABLED=true
NTFY_SERVER=https://ntfy.sh
NTFY_TOPIC=<secret topic>
QUIET_HOURS_START=22:00
QUIET_HOURS_END=07:00
MAX_NOTIFICATIONS_PER_HOUR=5
URGENT_MAX_REPEATS=3
URGENT_REPEAT_MINUTES=30
REMINDER_OFFSETS_ASSIGNMENT_MINUTES=1440,180,45
REMINDER_OFFSETS_EVENT_MINUTES=1440,60,15
ASSESSMENT_HEADSUP_MINUTES=30
```

**Sarvam AI Voice Calls:**
```
SARVAM_VOICE_ENABLED=false
SARVAM_API_KEY=
SARVAM_FROM_NUMBER=
SARVAM_TO_NUMBER=
SARVAM_MAX_CALLS_PER_HOUR=3
```

**Ollama LLM (optional):**
```
EXTRACTION_PROVIDER=rules
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3:latest
OLLAMA_MAX_ITEMS_PER_SYNC=5
OLLAMA_ALLOW_REMOTE=false
```

---

## 12. DEMO WALKTHROUGH (6 MINUTES)

### Setup (before presenting)
1. Run backend: `uvicorn app.main:app --host 127.0.0.1 --port 8000`
2. Run frontend: `npm run dev` → open `http://localhost:5173`
3. Click **Reset Demo Data** to seed clean fixtures.

### Minute 0:00–1:00: Problem & Vision
- Show dashboard. Point to "Backend Online" pill and "Auto-sync every 5 min".
- *"Students miss deadlines buried in email. ClassMail J runs locally, extracts deadlines deterministically, and alerts you at the right time — not just at deadline time."*

### Minute 1:00–2:00: Security & System Status
- Click **System Status**.
- *"All traffic is loopback-only. Read-only OAuth scopes only. Tokens are confirmed present without being shown. Demo data is isolated from real notifications."*

### Minute 2:00–3:00: Assessment Latest-Start Feature
- Point to **MATH 201 Midterm Exam** card.
- Shows: "Duration: 45 min" and "Start by 1:15 PM" (deadline 2:00 PM − 45 min).
- Click to open detail → show reminder scheduled for exactly 1:15 PM.
- *"A reminder at 2:00 PM for a 2:00 PM exam is useless. ClassMail J calculates that you must start by 1:15 PM and fires a reminder exactly then."*

### Minute 3:00–4:00: Live Text Ingest
- Click **+ Paste Email / Text**.
- Paste: *"Submit your final team project by Friday, November 13, 2026 at 11:59 PM EST to the portal."*
- Click **Ingest & Analyze Text**.
- *"Deadline extracted: Nov 13 at 11:59 PM EST → converted to UTC. Priority: Important (next week). Source badge: Manual."*

### Minute 4:00–5:00: Reminders, Snooze, Acknowledge
- Snooze a task → 2 hours.
- Acknowledge another task → escalation stops, submission reminders preserved.
- Complete a task → all reminders cancelled.
- Click **Notifications** → show log with scheduled times and delivery delay.

### Minute 5:00–6:00: Classroom Sync & TURNED_IN
- Click **Sync Classroom now**.
- *"Classroom assignments pulled. Already TURNED_IN? Badge shows 'Turned in per Classroom' and all reminders suppressed — no spam for work already done."*
- Summary: Local-first, deterministic, private, smart timing.

---

## 13. PROJECT FILE STRUCTURE

```
classmail-j/
├── backend/
│   ├── app/
│   │   ├── integrations/
│   │   │   ├── google_auth.py        # OAuth CLI flow
│   │   │   ├── gmail_client.py       # Gmail API wrapper
│   │   │   ├── classroom_sync.py     # Classroom API wrapper
│   │   │   ├── sync_service.py       # Orchestrates Gmail + Classroom sync
│   │   │   └── sarvam_voice.py       # Sarvam AI voice call client (NEW)
│   │   ├── auth.py                   # OIDC + PKCE + session management
│   │   ├── config.py                 # All env var config
│   │   ├── db.py                     # SQLite schema + migrations
│   │   ├── demo_data.py              # Relative-date academic demo fixtures
│   │   ├── extraction.py             # Rules engine + OllamaProvider
│   │   ├── main.py                   # FastAPI app + all API routes
│   │   ├── models.py                 # Pydantic schemas + enums
│   │   ├── priority.py               # Deterministic priority engine
│   │   ├── reminders.py              # Reminder planner + ntfy client
│   │   └── scheduler.py              # APScheduler manager
│   ├── data/                         # SQLite DB (gitignored)
│   ├── secrets/                      # OAuth tokens (gitignored)
│   ├── tests/                        # 129 tests across 10 suites
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.tsx                   # Full SPA — all UI logic
│       ├── App.css                   # Auralis SaaS design system
│       ├── api.ts                    # Typed REST API client
│       └── types.ts                  # TypeScript interfaces
├── .env.example                      # All env var names and defaults
├── DEMO_SCRIPT.md                    # 6-minute demo walkthrough
├── PROJECT_STATE.md                  # Milestone history and test audit
└── README.md                         # Full setup documentation
```

---

## 14. ITEM TYPES & KINDS

**Item Types:**
- `task` — something the student must do (submit, complete, attend)
- `event` — time-based occurrence (lecture, seminar, office hours)

**Item Kinds:**
- `assignment` — regular coursework submission
- `assessment` — timed exam, quiz, or test (triggers latest-start reminder)
- `interview` — job/internship interview
- `meeting` — scheduled meeting or office hours
- `announcement` — informational, no action required
- `general` — catch-all for unclassified items

**Assessment detection cues**: words like "test", "quiz", "exam", "midterm", "final", "assessment", "timed" in combination with item_type=task.

---

## 15. FUTURE ROADMAP

**Near-term:**
- Multi-account support (multiple Google accounts)
- Google Calendar event integration
- Richer Ollama prompts for context-aware summarization
- Mobile-first PWA wrapper

**Mid-term:**
- Export to PDF / iCal format
- Webhook ingest (Canvas LMS, Moodle support)
- Customizable reminder offset presets in UI
- Peer study group shared reminders

**Long-term:**
- Fine-tuned small model for deadline extraction
- Full voice interface via Sarvam AI for query and snooze
- Encrypted local backup to NAS / external drive
- Open plugin API for third-party integrations

---

## 16. SUGGESTED PPT SLIDE STRUCTURE (15 SLIDES)

For Claude to build the PPT, use these slides in order:

1. **Title Slide** — "ClassMail J | Local-First Academic AI Assistant" + tech badges + date
2. **Problem Statement** — 5 pain points with icons (missed deadlines, no unified view, manual tracking, no proactive alerts, privacy risk)
3. **Solution Overview** — 4-step flow: Read → Extract → Prioritise → Alert
4. **System Architecture** — 3-box diagram: Frontend | Backend | Data + external integrations row
5. **Deadline Extraction Engine** — left: extraction rules, right: priority engine table
6. **Gmail & Classroom Integration** — 2 columns: Gmail (read-only scopes) | Classroom (TURNED_IN detection)
7. **Smart Assessment Timing** — visual: exam at 2:00 PM, duration 45 min → "Start by 1:15 PM" reminder — the killer feature
8. **Autonomous Reminders via ntfy** — reminder kinds table + safety mechanisms
9. **Sarvam AI Voice Calls** — how it works + config box + "urgent → voice call" flow
10. **Optional Ollama LLM** — provider interface, guardrails, fallback
11. **Security & Privacy** — table of all security controls
12. **Tech Stack** — full table with layers
13. **Testing** — 10 suites, 129 tests, FakeClock, mocks
14. **Live Demo Flow** — 5-step numbered boxes
15. **Future Roadmap + Q&A** — 3-column near/mid/long-term + thank you

---

## DESIGN INSTRUCTIONS FOR THE PPT

- **Background**: Pure white (#FFFFFF) on all slides
- **Font**: Calibri or Aptos (modern, clean, standard on all systems)
- **Heading color**: Dark navy or black (#0F0F0F or #1A2B4A)
- **Accent color**: Strong blue (#1A56DB) for heading bars, borders, and chips
- **Secondary text**: Muted grey (#556581)
- **NO emojis** — use simple geometric shapes, lines, and boxes instead
- **NO clip art** — no stock images, no cartoon illustrations
- **Bullet points**: Use clean dashes or squares, not fancy symbols
- **Tables**: Simple 2-3 column tables with alternating light grey rows
- **Diagrams**: Use simple rectangle boxes with arrows — no 3D shapes
- **Consistent left vertical accent bar** (thin blue bar on left edge of each slide)
- **Keep slides simple**: max 6-7 bullet points per slide
- **Slide 7 (Smart Timing)**: Make this visually prominent — it's the most unique feature

---

*Document version: November 2026 | ClassMail J v0.5.0 | 129 backend tests passing*

