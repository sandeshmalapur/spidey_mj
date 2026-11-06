# ClassMail J - Live Demo Script

A structured 6-minute presentation script and pre-demo checklist for demonstrating ClassMail J.

---

## Pre-Demo Checklist (3 Minutes Before Presentation)

- [ ] **Python Backend Running**: Ensure Uvicorn is active on `http://127.0.0.1:8000`:
  ```powershell
  cd backend
  .\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
  ```
- [ ] **React Frontend Running**: Ensure Vite dev server is running on `http://localhost:5173`:
  ```powershell
  cd frontend
  npm run dev
  ```
- [ ] **Tokens Ready**: Verify `backend/secrets/token_gmail.json` and `backend/secrets/token_classroom.json` exist. (If expired or missing, run `python -m app.integrations.google_auth [gmail|classroom]`).
- [ ] **Browser Open**: Navigate to `http://localhost:5173`.
- [ ] **Reset Demo Fixtures**: Click **Reset Demo Data** in the top toolbar to seed clean relative-date academic items.

---

## 6-Minute Live Walkthrough

### Minute 0:00 - 1:00: Problem Statement & Local-First Philosophy
- **Speaker**:
  > *"Students and educators receive dozens of emails and announcements every day across Gmail and Google Classroom. Deadlines are buried in dense text, emails arrive at odd hours, and critical exams slip through the cracks.
  > ClassMail J is a local-first, deterministic AI academic assistant. It runs 100% locally on your machine, extracts deadlines and durations without sending your emails to third-party clouds, computes priorities deterministically, and dispatches privacy-preserving push reminders."*
- **Visual**: Show the ClassMail J dashboard at `http://localhost:5173`. Point out the header with the **Backend Online (ok)** pill and **Auto-sync every 5 min** indicator.

---

### Minute 1:00 - 2:00: System Diagnostics & Security Hardening
- **Action**: Click the **System Status** button in the top right header.
- **Speaker**:
  > *"Before showing data, notice our diagnostic System Status panel. ClassMail J employs defense-in-depth:
  > First, our backend uses TrustedHostMiddleware restricting traffic strictly to local loopback hosts.
  > Second, we validate incoming Origins on all state-changing endpoints, preventing CSRF or DNS rebinding from malicious websites.
  > Third, our Google integrations use strictly read-only OAuth scopes—ClassMail J can never alter or delete your academic work.
  > Notice that tokens and credentials are confirmed present without ever leaking secret strings or keys into the browser."*
- **Action**: Close the System Status modal.

---

### Minute 2:00 - 3:00: Relative Demo Fixtures & Assessment Latest-Start Calculation
- **Action**: Point out the prominent top amber banner:
  `⚠️ Demo data active: Simulated academic items are loaded alongside real data.`
- **Speaker**:
  > *"ClassMail J clearly separates simulated demo data from real imported accounts. Every item displays a distinct badge: Demo, Manual, Gmail, or Classroom.
  > Let's look at the first task: `MATH 201 Midterm Exam`.
  > It is categorized as an Assessment with a confirmed deadline today at 2:00 PM. Notice the badge: `Duration 45 min`.
  > Because an exam takes 45 minutes, a reminder at 2:00 PM is useless—you would already be late. ClassMail J calculates the exact **latest-start time**: `Start by 1:15 PM`!
  > It schedules an exact one-shot APScheduler job at 1:15 PM to ensure you sit down and begin on time."*
- **Action**: Click on the `MATH 201 Midterm Exam` card to open the detail panel. Scroll down to show the planned reminders list (`latest_start` scheduled for 1:15 PM).

---

### Minute 3:00 - 4:00: Live Ingest & Deterministic Rules Extraction
- **Action**: Click **+ Paste Email / Text** in the toolbar.
- **Action**: Paste the following real-world example into the text box:
  ```
  Subject: Final Project Submission & Peer Review
  From: prof.allen@university.edu
  Text: Dear class, please submit your final team project report by Friday, November 13, 2026 at 11:59 PM EST to the course portal. Late submissions will receive a 10% penalty per day.
  ```
- **Action**: Click **Ingest & Analyze Text**.
- **Speaker**:
  > *"Notice what happened immediately:
  > 1. ClassMail J extracted the deadline: `Friday, November 13, 2026 at 11:59 PM EST` and converted it accurately to UTC.
  > 2. It classified the item as an Assignment task.
  > 3. It evaluated our priority engine: because the deadline is next week, it assigned `Important` priority. If it were within 48 hours, it would escalate to `Urgent`.
  > 4. Notice the `📝 Manual` badge cleanly identifying its provenance."*

---

### Minute 4:00 - 5:00: Reminders, Push Notifications & Snooze/Acknowledge Lifecycle
- **Action**: Select the newly ingested item or an existing assignment.
- **Action**: Click **Snooze** -> **2 hours**.
- **Speaker**:
  > *"When an item is snoozed, ClassMail J respects your focus: background reminders are deferred until the snooze window expires.
  > Furthermore, clicking **Acknowledge** signals that you are aware of the task, which automatically stops repetitive urgent escalation while preserving time-critical submission reminders.
  > And marking the task **Completed** instantly cancels all remaining pending reminders in the SQLite database."*
- **Action**: Click the **🔔 Notifications** button in the toolbar.
- **Speaker**:
  > *"Our notification log displays scheduled delivery times, delay tracking (`on-time` vs `+{N}s`), and delivery channel. For real mobile push, we support ntfy. Notice that demo items are strictly isolated and never transmit to external servers."*
- **Action**: Click **Send Test Notification** to demonstrate live alert dispatch.

---

### Minute 5:00 - 6:00: Google Classroom Sync, Submission Suppression & Wrap-up
- **Action**: Click **Sync Classroom now** or point out the Classroom Sync status row.
- **Speaker**:
  > *"When syncing with Google Classroom:
  > - Coursework assignments and announcements are ingested automatically.
  > - We inspect the student's submission state. If an assignment is marked `TURNED_IN`, ClassMail J displays a blue `✓ Turned in per Classroom` badge and suppresses all future deadline reminders—because you've already handed it in!
  > - If authorization expires, the system displays a clear reconnection prompt with the exact terminal command.
  > 
  > To summarize: ClassMail J is private, local-first, deterministic, and built to keep students and educators effortlessly on top of academic deadlines."*
- **Action**: Click **Refresh** to show the live, updated state of the dashboard.

