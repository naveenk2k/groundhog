"""Groundhog companion: corpus storage and retrieval (issue #3).

The corpus is the local record of everything you've already watched: one row
per video, holding its metadata, the raw transcript text, and an embedding of
that text. Two things build on top of this module later:

  - #4 (Claude call) queries the corpus for the top-K nearest videos to a
    newly-opened one, and sends their full transcripts to Claude alongside
    the new video's transcript.
  - #9 (backfill script) inserts one row per video from a Takeout watch
    history export.

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

from dataclasses import dataclass
from typing import Optional, Sequence

import apsw
import sqlite_vec

from companion.config import CORPUS_DB_FILE, EMBEDDING_DIMENSIONS, EMBEDDING_MODEL_NAME

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

    Carries everything #4 needs to build the Claude prompt: the matched
    video's title, creator, when it was watched, and its full transcript
    text (per DECISIONS.md, full transcripts are sent to Claude, not
    excerpts). Creator lets Claude distinguish "same channel revisiting its
    own topic" from "several different creators independently covering the
    same ground" - two very different signals for judging novelty.
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
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

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
    """Add a video to the corpus.

    `embedding` can be precomputed (e.g. batch backfill reusing one model
    load across thousands of videos) or omitted, in which case it's computed
    here from `transcript_text` via the shared embedding model.

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
