"""
Central configuration for Sentinel AI paths and settings.

Detects if the project root is writeable (which may not be the case in
some read-only cloud deployment environments) and automatically falls
back to the system's temporary directory for SQLite database and trace event logs.
"""

import os
import tempfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def is_dir_writeable(path: str) -> bool:
    try:
        test_file = os.path.join(path, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except (IOError, OSError):
        return False

# Determine writeable directory for default files
if is_dir_writeable(PROJECT_ROOT):
    DATA_DIR = PROJECT_ROOT
else:
    # Fallback to system temp directory
    DATA_DIR = tempfile.gettempdir()

# Configured paths
EVENTS_PATH = os.path.join(DATA_DIR, "sentinel_events.jsonl")
DB_PATH = os.path.join(DATA_DIR, "sentinel.db")

def get_database_url() -> str:
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url
    # Use 3 slashes (sqlite:///) followed by absolute path.
    # SQLAlchemy handles absolute paths correctly across Windows and Linux this way.
    return f"sqlite:///{DB_PATH}"
