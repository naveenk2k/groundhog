"""Groundhog companion: corpus storage and retrieval.

The corpus is the local record of everything you've already watched: one row
per video, holding its metadata, the raw transcript text, and an embedding of
that text. companion/verdict_pipeline.py queries it for the top-K nearest
videos to a newly-opened one and sends their full transcripts to Gemini
alongside the new video's transcript; backfill.py inserts one row per video
from a Takeout watch-history export.

Raw transcript text is stored alongside the embedding (not instead of it) so
the whole corpus can be re-embedded later if the embedding model ever
changes, without re-fetching every transcript from YouTube - see
DECISIONS.md ("Corpus schema").

Storage is sqlite-vec: a `videos` table holds the metadata/text columns, and
a paired `videos_vec` virtual table holds the embedding, joined on rowid.
Two tables rather than embedding everything into one vec0 table because
vec0's own metadata-column support is more limited than plain SQLite columns
(e.g. no UNIQUE constraints), and keeping the human-readable data in an
ordinary table makes it trivial to inspect with any sqlite browser.

sqlite-vec is loaded as a runtime extension, which needs `load_extension`
support in the underlying SQLite library. Apple's system Python
(`/usr/bin/python3` on macOS, which is what a bare `python3 -m venv` picks
up unless Homebrew/python.org Python is on PATH first) ships a SQLite build
with that support stripped out, so `sqlite3.Connection.enable_load_extension`
doesn't exist there. `apsw` bundles its own SQLite amalgamation with
extension loading enabled and works the same everywhere, so this module uses
it instead of the stdlib `sqlite3` module to sidestep that platform gap.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

import apsw
import sqlite_vec

from companion.config import CORPUS_DB_FILE, EMBEDDING_DIMENSIONS, EMBEDDING_MODEL_NAME

logger = logging.getLogger(__name__)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    video_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    creator TEXT NOT NULL DEFAULT '',
    watched_at TEXT NOT NULL,
    transcript_text TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS videos_vec USING vec0(
    embedding float[{EMBEDDING_DIMENSIONS}]
);
"""

# `creator` was added after the schema first shipped. `CREATE TABLE IF NOT
# EXISTS` doesn't retrofit existing databases, so any corpus.db created
# before this change needs an explicit ALTER TABLE - this keeps a corpus
# someone already started building (e.g. via add_video.py) from breaking.
_MIGRATIONS = [
    "ALTER TABLE videos ADD COLUMN creator TEXT NOT NULL DEFAULT ''",
]


@dataclass
class CorpusMatch:
    """One row returned by a corpus similarity query.

    Carries everything needed to build the verdict prompt (companion/
    verdict.py): the matched video's title, creator, when it was watched,
    and its full transcript text (per DECISIONS.md, full transcripts are
    sent, not excerpts). Creator lets the model distinguish "same channel
    revisiting its own topic" from "several different creators independently
    covering the same ground" - two very different signals for judging
    novelty.
    """

    video_id: str
    title: str
    creator: str
    watched_at: str
    transcript_text: str
    distance: float


# --- Embedding -----------------------------------------------------------

_model = None  # lazy-loaded singleton; sentence-transformers is slow to import/init


def get_model():
    """Return the shared sentence-transformers model, loading it on first use.

    Loaded lazily (rather than at module import time) so importing this
    module - e.g. just to call insert_video with a precomputed embedding -
    doesn't pay the model-load cost if embed_text() is never called.

    Tries HF_HUB_OFFLINE first: even when the model is already fully cached
    locally, huggingface_hub still does a HEAD-request round trip per file
    to validate the cache against the hub (confirmed live: ~30s across a
    dozen-plus requests to huggingface.co on every companion restart, purely
    to re-confirm files that were already there). Offline mode skips all of
    that. Falls back to a normal (network-touching) load if offline mode
    fails with OSError - the error huggingface_hub raises when it can't find
    the model locally at all (e.g. this machine's very first run).
    """
    global _model
    if _model is None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer

        try:
            _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except OSError:
            logger.info(
                "%s not cached locally - falling back to an online load for this run",
                EMBEDDING_MODEL_NAME,
            )
            os.environ.pop("HF_HUB_OFFLINE", None)
            _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    """Embed a piece of text (e.g. a transcript) into a float vector."""
    vector = get_model().encode(text, normalize_embeddings=True)
    return vector.tolist()


# --- Connection / schema --------------------------------------------------


def get_connection(db_path: Optional[str] = None) -> apsw.Connection:
    """Open (creating if needed) the corpus database with sqlite-vec loaded.

    `db_path` defaults to the single on-disk corpus file (companion/config.py
    CORPUS_DB_FILE, overridable via GROUNDHOG_CORPUS_DB - tests pass a
    throwaway path so they never touch the real corpus).
    """
    path = db_path if db_path is not None else str(CORPUS_DB_FILE)
    conn = apsw.Connection(path)
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)
    conn.execute(_SCHEMA)
    _apply_migrations(conn)
    return conn


def _apply_migrations(conn: apsw.Connection) -> None:
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
    if "creator" not in existing_columns:
        for statement in _MIGRATIONS:
            conn.execute(statement)


# --- watched_at formatting --------------------------------------------------

# Canonical on-disk shape for `watched_at`: UTC, whole seconds, "Z" suffix
# (e.g. "2026-07-08T15:58:00Z"). Both write paths - the live watch-threshold
# path in verdict_pipeline.py and the Takeout-import path in backfill.py -
# go through the helpers below instead of building the string inline, so
# every row has the same shape rather than one using a "+00:00" offset with
# microsecond precision (Python's default isoformat()) and the other a raw
# "Z"-suffixed, millisecond-precision string straight from Takeout. Existing
# rows written before this may still be in either of those older shapes;
# companion/verdict.py's _format_watched_at keeps its defensive parsing for
# exactly that reason.
_WATCHED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_watched_at() -> str:
    """Current UTC time in the canonical watched_at storage format."""
    return datetime.now(timezone.utc).strftime(_WATCHED_AT_FORMAT)


def normalize_watched_at(raw: str) -> str:
    """Reformat an arbitrary ISO 8601 watched_at string (e.g. Takeout's
    millisecond-precision, "Z"-suffixed export format) into the canonical
    storage format.

    Falls back to returning `raw` unchanged if it doesn't parse - better to
    store something than crash an hours-long backfill run over one entry's
    formatting quirk.
    """
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime(_WATCHED_AT_FORMAT)


# --- Insert ----------------------------------------------------------------


def insert_video(
    conn: apsw.Connection,
    video_id: str,
    title: str,
    creator: str,
    watched_at: str,
    transcript_text: str,
    embedding: Optional[Sequence[float]] = None,
) -> None:
    """Add a video to the corpus - the single embed+insert entry point every
    caller (companion/verdict_pipeline.py, add_video.py, backfill.py) goes
    through, rather than each computing its own embedding first.

    `embedding` is an optional override (mainly useful for tests that want a
    specific vector without loading the real model); omitted, it's computed
    here from `transcript_text` via the shared embedding model - `get_model()`
    is a lazy-loaded singleton, so this costs nothing extra even across a
    backfill run inserting thousands of rows.

    Re-inserting an existing `video_id` replaces its row (metadata,
    transcript, and embedding) rather than erroring - re-running a fetch for
    a video already in the corpus is a normal occurrence, not a bug.
    """
    if embedding is None:
        embedding = embed_text(transcript_text)

    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"embedding has {len(embedding)} dimensions, expected {EMBEDDING_DIMENSIONS}"
        )

    cursor = conn.cursor()
    cursor.execute("BEGIN")
    try:
        existing = cursor.execute(
            "SELECT id FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if existing is not None:
            row_id = existing[0]
            cursor.execute(
                """
                UPDATE videos
                SET title = ?, creator = ?, watched_at = ?, transcript_text = ?
                WHERE id = ?
                """,
                (title, creator, watched_at, transcript_text, row_id),
            )
            cursor.execute("DELETE FROM videos_vec WHERE rowid = ?", (row_id,))
        else:
            cursor.execute(
                """
                INSERT INTO videos (video_id, title, creator, watched_at, transcript_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (video_id, title, creator, watched_at, transcript_text),
            )
            row_id = conn.last_insert_rowid()

        cursor.execute(
            "INSERT INTO videos_vec (rowid, embedding) VALUES (?, ?)",
            (row_id, sqlite_vec.serialize_float32(list(embedding))),
        )
        cursor.execute("COMMIT")
    except Exception:
        cursor.execute("ROLLBACK")
        raise


# --- Query -----------------------------------------------------------------


def find_video(conn: apsw.Connection, video_id: str) -> Optional[dict]:
    """Look up a single video by ID, with none of query_similar's
    embedding/similarity-search cost - used to answer "is this video already
    in my watch history" up front, before doing any of the real /verdict
    work (see the extension's GROUNDHOG_VIDEO_LOOKUP), so that check is
    cheap enough to run on every video-opened navigation.

    Returns None if not found, else {"video_id", "title", "watched_at"}.
    """
    row = conn.execute(
        "SELECT video_id, title, watched_at FROM videos WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    found_video_id, title, watched_at = row
    return {"video_id": found_video_id, "title": title, "watched_at": watched_at}


def query_similar(
    conn: apsw.Connection, embedding: Sequence[float], k: int
) -> list[CorpusMatch]:
    """Return the top-`k` corpus rows nearest to `embedding`, closest first.

    `k` is a plain parameter (not hardcoded) - a later issue exposes it as a
    UI slider (see PLAN.md / DECISIONS.md); this function just needs to
    accept whatever value it's given.
    """
    if k <= 0:
        return []

    rows = conn.execute(
        """
        SELECT v.video_id, v.title, v.creator, v.watched_at, v.transcript_text, vv.distance
        FROM videos_vec AS vv
        JOIN videos AS v ON v.id = vv.rowid
        WHERE vv.embedding MATCH ? AND k = ?
        ORDER BY vv.distance
        """,
        (sqlite_vec.serialize_float32(list(embedding)), k),
    ).fetchall()

    return [
        CorpusMatch(
            video_id=video_id,
            title=title,
            creator=creator,
            watched_at=watched_at,
            transcript_text=transcript_text,
            distance=distance,
        )
        for video_id, title, creator, watched_at, transcript_text, distance in rows
    ]
