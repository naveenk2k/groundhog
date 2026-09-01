"""Shared configuration for the Groundhog companion.

Keeping this in one place means install.sh, the FastAPI app, and (later)
launchd all agree on where things live without hardcoding paths in more than
one spot.
"""

import os
import shutil
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

# Off by default: emits Datadog APM spans for every /verdict and
# /videos/watched request when a local Datadog Agent is running (see
# companion/tracing.py). Same opt-in convention as DEBUG above. See
# docs/superpowers/specs/2026-08-23-datadog-tracing-design.md.
TRACING_ENABLED = os.environ.get("GROUNDHOG_TRACING_ENABLED", "").strip().lower() in ("1", "true", "yes")

# Optional local ASR fallback. The companion stays caption-only unless a
# whisper.cpp model exists at this path; installing the binary alone should
# not make a missing-model error appear in every verdict request.
WHISPER_CLI = os.environ.get("GROUNDHOG_WHISPER_CLI", shutil.which("whisper-cli") or "whisper-cli")
WHISPER_MODEL = Path(
    os.environ.get("GROUNDHOG_WHISPER_MODEL", str(REPO_ROOT / ".models" / "ggml-base.en.bin"))
)
WHISPER_THREADS = int(os.environ.get("GROUNDHOG_WHISPER_THREADS", "10"))
WHISPER_TIMEOUT_SECONDS = float(os.environ.get("GROUNDHOG_WHISPER_TIMEOUT_SECONDS", "30"))

# launchd's default PATH omits Homebrew, even when ffmpeg was installed there.
# Resolve the usual macOS locations explicitly so the local ASR fallback works
# identically from an interactive shell and the long-running companion.
FFMPEG = os.environ.get("GROUNDHOG_FFMPEG") or shutil.which(
    "ffmpeg", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
) or "ffmpeg"

# yt-dlp performs several network requests before it can expose a caption
# track. Bound each socket operation and disable retries so a temporarily
# stalled YouTube endpoint cannot keep the synchronous companion worker (and
# the visible overlay) waiting indefinitely. The overlay separately enforces
# the end-to-end 60-second user-facing deadline.
YTDLP_SOCKET_TIMEOUT_SECONDS = float(os.environ.get("GROUNDHOG_YTDLP_SOCKET_TIMEOUT_SECONDS", "15"))


def read_secret() -> str:
    """Read the shared secret from disk.

    Raises FileNotFoundError if install.sh hasn't been run yet - the server
    should fail loudly on startup rather than silently accept every request.
    """
    return SECRET_FILE.read_text().strip()
