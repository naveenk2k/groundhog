"""Shared configuration for the Groundhog companion.

Keeping this in one place means install.sh, the FastAPI app, and (later)
launchd all agree on where things live without hardcoding paths in more than
one spot.
"""

import os
from pathlib import Path

# Repo root: companion/ lives directly under it.
REPO_ROOT = Path(__file__).resolve().parent.parent

HOST = "127.0.0.1"
PORT = 8787

# The secret file's location can be overridden (useful for tests); by default
# it's a single dotfile at the repo root, generated once by install.sh and
# never committed (see .gitignore).
SECRET_FILE = Path(os.environ.get("GROUNDHOG_SECRET_FILE", str(REPO_ROOT / ".groundhog-secret")))

# Header the extension must send on every request (except /health).
SECRET_HEADER = "X-Groundhog-Secret"


def read_secret() -> str:
    """Read the shared secret from disk.

    Raises FileNotFoundError if install.sh hasn't been run yet - the server
    should fail loudly on startup rather than silently accept every request.
    """
    return SECRET_FILE.read_text().strip()
