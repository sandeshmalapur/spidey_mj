import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
backend_env = Path(__file__).resolve().parent.parent / ".env"
root_env = Path(__file__).resolve().parent.parent.parent / ".env"
if backend_env.exists():
    load_dotenv(backend_env)
if root_env.exists():
    load_dotenv(root_env)

DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "classmail.db")

DATABASE_PATH = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
TIMEZONE = os.getenv("TIMEZONE", "UTC")
DEFAULT_IST_MEANING = os.getenv("DEFAULT_IST_MEANING", "Asia/Kolkata")
APP_VERSION = "0.5.0"

# Priority engine demo defaults
URGENT_WITHIN_HOURS = int(os.getenv("URGENT_WITHIN_HOURS", "48"))  # demo default
IMPORTANT_WITHIN_DAYS = int(os.getenv("IMPORTANT_WITHIN_DAYS", "14"))  # demo default

# Google / Gmail Integration
GOOGLE_CLIENT_FILE = os.getenv(
    "GOOGLE_CLIENT_FILE",
    str(Path(__file__).resolve().parent.parent / "secrets" / "google_client.json"),
)
GMAIL_TOKEN_FILE = os.getenv(
    "GMAIL_TOKEN_FILE",
    str(Path(__file__).resolve().parent.parent / "secrets" / "token_gmail.json"),
)
GMAIL_QUERY = os.getenv("GMAIL_QUERY", "newer_than:7d in:inbox")
GMAIL_MAX_MESSAGES = int(os.getenv("GMAIL_MAX_MESSAGES", "25"))

# Background Sync Scheduler
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "5"))
SYNC_ENABLED = os.getenv("SYNC_ENABLED", "true").lower() in ("true", "1", "yes")

_raw_origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173,http://127.0.0.1:5173")
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in _raw_origins.split(",")
    if origin.strip() and origin.strip() != "*"
]
if not ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
