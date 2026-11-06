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
GOOGLE_WEB_CLIENT_FILE = os.getenv(
    "GOOGLE_WEB_CLIENT_FILE",
    str(Path(__file__).resolve().parent.parent / "secrets" / "google_web_client.json"),
)
AUTH_REDIRECT_URI = os.getenv(
    "AUTH_REDIRECT_URI",
    "http://127.0.0.1:8000/api/auth/google/callback",
)
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() in ("true", "1", "yes")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "").strip().lower()
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "12"))


def validate_auth_config() -> None:
    """Validate authentication configuration when AUTH_ENABLED is True."""
    if AUTH_ENABLED:
        if not SESSION_SECRET or len(SESSION_SECRET) < 32:
            raise RuntimeError(
                "SESSION_SECRET must be set and at least 32 characters long when AUTH_ENABLED=true"
            )
        if not OWNER_EMAIL:
            raise RuntimeError(
                "OWNER_EMAIL must be set when AUTH_ENABLED=true"
            )


GMAIL_TOKEN_FILE = os.getenv(
    "GMAIL_TOKEN_FILE",
    str(Path(__file__).resolve().parent.parent / "secrets" / "token_gmail.json"),
)
CLASSROOM_TOKEN_FILE = os.getenv(
    "CLASSROOM_TOKEN_FILE",
    str(Path(__file__).resolve().parent.parent / "secrets" / "token_classroom.json"),
)
GMAIL_QUERY = os.getenv("GMAIL_QUERY", "newer_than:7d in:inbox")
GMAIL_MAX_MESSAGES = int(os.getenv("GMAIL_MAX_MESSAGES", "25"))

# Background Sync Scheduler
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "5"))
SYNC_ENABLED = os.getenv("SYNC_ENABLED", "true").lower() in ("true", "1", "yes")
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() in ("true", "1", "yes")

# Reminders & Notifications (Demo defaults)
REMINDER_TICK_SECONDS = int(os.getenv("REMINDER_TICK_SECONDS", "60"))
NOTIFICATIONS_ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "true").lower() in ("true", "1", "yes")
URGENT_MAX_REPEATS = int(os.getenv("URGENT_MAX_REPEATS", "3"))
URGENT_REPEAT_MINUTES = int(os.getenv("URGENT_REPEAT_MINUTES", "30"))
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "8"))
QUIET_HOURS_START = os.getenv("QUIET_HOURS_START", "22:00")
QUIET_HOURS_END = os.getenv("QUIET_HOURS_END", "07:00")
URGENT_BYPASS_QUIET = os.getenv("URGENT_BYPASS_QUIET", "false").lower() in ("true", "1", "yes")
MAX_NOTIFICATIONS_PER_HOUR = int(os.getenv("MAX_NOTIFICATIONS_PER_HOUR", "5"))
NOTIFY_DEMO_ITEMS = os.getenv("NOTIFY_DEMO_ITEMS", "false").lower() in ("true", "1", "yes")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_INCLUDE_TITLE = os.getenv("NTFY_INCLUDE_TITLE", "false").lower() in ("true", "1", "yes")

# Reminder Timing Refinements (Assessment & Submission)
ASSESSMENT_HEADSUP_MINUTES = int(os.getenv("ASSESSMENT_HEADSUP_MINUTES", "30"))
CATCHUP_GRACE_MINUTES = int(os.getenv("CATCHUP_GRACE_MINUTES", "15"))
REMINDER_OFFSETS_ASSIGNMENT_MINUTES = os.getenv("REMINDER_OFFSETS_ASSIGNMENT_MINUTES", "1440,180,45")
REMINDER_OFFSETS_EVENT_MINUTES = os.getenv("REMINDER_OFFSETS_EVENT_MINUTES", "1440,60,15")

# Ollama Extraction Provider Configuration
EXTRACTION_PROVIDER = os.getenv("EXTRACTION_PROVIDER", "rules").lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3:latest")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
OLLAMA_MAX_INPUT_CHARS = int(os.getenv("OLLAMA_MAX_INPUT_CHARS", "6000"))
OLLAMA_MAX_ITEMS_PER_SYNC = int(os.getenv("OLLAMA_MAX_ITEMS_PER_SYNC", "5"))
OLLAMA_ALLOW_REMOTE = os.getenv("OLLAMA_ALLOW_REMOTE", "false").lower() in ("true", "1", "yes")


def validate_ollama_url(url: str, allow_remote: bool = False) -> str:
    """Validate Ollama URL: reject non-localhost unless allow_remote is True."""
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not allow_remote and hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError(
            f"OLLAMA_URL '{url}' is not a localhost address. "
            "Set OLLAMA_ALLOW_REMOTE=true to allow remote Ollama endpoints."
        )
    return url


_raw_origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173,http://127.0.0.1:5173")
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in _raw_origins.split(",")
    if origin.strip() and origin.strip() != "*"
]
if not ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
