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

# Corpus DB location can also be overridden (tests use a throwaway path so
# they never touch a real corpus). Single sqlite file on disk, covered by
# .gitignore - see companion/corpus.py.
CORPUS_DB_FILE = Path(os.environ.get("GROUNDHOG_CORPUS_DB", str(REPO_ROOT / "corpus.db")))

# Embedding model: small and fast enough to run on CPU in milliseconds - see
# DECISIONS.md "Companion stack: Python, sentence-transformers, sqlite-vec".
# 384-dimensional output - corpus.py's schema is sized to match.
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384

# Off by default: request/response bodies include full video transcripts and
# LLM output, so logging them unconditionally would flood .logs/companion.log
# and write transcript text to disk on every request. Set
# GROUNDHOG_DEBUG=1 (e.g. `GROUNDHOG_DEBUG=1 uvicorn companion.app:app ...`)
# to trace the actual request/response bodies moving through the companion.
DEBUG = os.environ.get("GROUNDHOG_DEBUG", "").strip().lower() in ("1", "true", "yes")


def read_secret() -> str:
    """Read the shared secret from disk.

    Raises FileNotFoundError if install.sh hasn't been run yet - the server
    should fail loudly on startup rather than silently accept every request.
    """
    return SECRET_FILE.read_text().strip()
