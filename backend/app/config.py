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
APP_VERSION = "0.2.0"

_raw_origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173,http://127.0.0.1:5173")
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in _raw_origins.split(",")
    if origin.strip() and origin.strip() != "*"
]
if not ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
