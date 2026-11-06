# ClassMail J - Project State

## Current Milestone
- Milestone 1: Project scaffold, SQLite database schema initialization, read-only health check, and frontend health dashboard.

## Implemented Features
- **Backend Scaffold**: FastAPI app with CORS restricted explicitly to `http://localhost:5173` and `http://127.0.0.1:5173` (no wildcard).
- **SQLite Database**: Standard library `sqlite3` setup with startup schema initialization for `sources` and `items` tables (`backend/data/classmail.db` default).
- **Health Check & DB Health**:
  - `GET /api/health` returning `{"status": "ok", "db": "ok", "version": "0.1.0"}` when healthy and 503 error status when DB is down.
  - Read-only health check: strictly does not create directories or files, returning `False` when the database cannot be opened or queried.
- **Automated Tests**: Pytest test suite testing DB initialization, schema integrity, read-only health checks (missing paths and missing files without creating directories/files), and API health success/error responses.
- **Frontend Dashboard**: React + Vite + TypeScript application (no Tailwind, custom responsive CSS) on port 5173 displaying "ClassMail J" and real-time backend health check status with live error handling.

## Mocked vs Real
- **Database**: Real SQLite database file (`backend/data/classmail.db` by default or via `DATABASE_PATH`).
- **Health Check**: Real HTTP and SQLite query check (`SELECT 1;`).
- **Items / Sources CRUD**: Not yet implemented (scoped for Milestone 2+).
- **AI / Classroom / Gmail integration**: Not yet implemented (scoped for future milestones).

## Stack and Run Commands
- **Stack**:
  - Python 3.14.0 (FastAPI, Pydantic, Uvicorn, Pytest, stdlib sqlite3)
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

## Tests Last Run
- Backend Pytest (`cd backend; .\.venv\Scripts\python.exe -m pytest -v`): 4 passed (`test_db_initialization`, `test_db_health_check`, `test_api_health_endpoint`, `test_api_health_endpoint_error`).
- Frontend Build (`cd frontend; npm run build`): Completed successfully (exit code 0).

## Known Blockers
- None.

## Next Step
- Milestone 2: Data models, ingestion pipelines for Classroom & Gmail, and items CRUD endpoints.

## Remaining Time
to be supplied by user
