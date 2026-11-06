# ClassMail J

Local-first AI Email & Google Classroom assistant hackathon MVP.

## Project Structure

```
classmail-j/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py       # Configuration & environment variables
│   │   ├── db.py           # SQLite connection & schema initialization
│   │   └── main.py         # FastAPI application & /api/health endpoint
│   ├── tests/
│   │   ├── __init__.py
│   │   └── test_health_and_db.py
│   ├── requirements.txt    # Python dependencies
│   └── .venv/              # Python virtual environment
├── frontend/
│   ├── src/
│   │   ├── App.tsx         # Dashboard showing ClassMail J & live health check
│   │   ├── App.css         # Responsive styling (no Tailwind)
│   │   └── main.tsx
│   ├── package.json
│   └── vite.config.ts
├── .gitignore
├── .env.example
├── pytest.ini
├── PROJECT_STATE.md
└── README.md
```

## Prerequisites

- Python 3.14+
- Node.js 24+ / 25+
- npm 11+

## Quick Start / Run Commands

### 1. Backend Setup & Run

In PowerShell, navigate to the `backend` directory:

```powershell
cd D:\programs\Projects\classmail-j\backend

# Activate virtual environment (optional)
.\.venv\Scripts\Activate.ps1

# Run tests
.\.venv\Scripts\python.exe -m pytest -v

# Start backend server on 127.0.0.1:8000
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

The backend health check is accessible at `http://127.0.0.1:8000/api/health` and API docs at `http://127.0.0.1:8000/docs`.

### 2. Frontend Setup & Run

In a separate PowerShell terminal:

```powershell
cd D:\programs\Projects\classmail-j\frontend

# Install dependencies (if not already installed)
npm install

# Build frontend
npm run build

# Start frontend dev server on port 5173
npm run dev
```

Open `http://localhost:5173` or `http://127.0.0.1:5173` in your browser to view the live dashboard connecting to the backend.
