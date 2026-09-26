"""Shared two-tier storage for fetched transcripts.

The corpus is deliberately limited to videos the user watched. This module
keeps transcript reuse separate: successful transcript fetches live in the
same local SQLite database without embeddings or any effect on verdict
similarity search. A small in-memory layer makes immediate repeat requests
cheap; the durable layer makes revisiting a video cheap after a restart.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock

import apsw

from companion.transcript import TranscriptResult, fetch_transcript

_MEMORY_TTL_SECONDS = 10 * 60
_MEMORY_MAX_ENTRIES = 50
_memory_cache: dict[str, tuple[float, TranscriptResult]] = {}


@dataclass
class _InFlightFetch:
    """One external fetch in progress for a video ID."""

    complete: Event
    result: TranscriptResult | None = None
    error: BaseException | None = None


_inflight_lock = Lock()
_inflight: dict[str, _InFlightFetch] = {}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript_cache (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    creator TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    transcript_text TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_schema(conn: apsw.Connection) -> None:
    """Create the durable transcript cache without altering corpus membership."""
    conn.execute(_SCHEMA)


def _remember(video_id: str, result: TranscriptResult) -> TranscriptResult:
    if len(_memory_cache) >= _MEMORY_MAX_ENTRIES and video_id not in _memory_cache:
        oldest_video_id = min(_memory_cache, key=lambda vid: _memory_cache[vid][0])
        del _memory_cache[oldest_video_id]
    _memory_cache[video_id] = (time.monotonic(), result)
    return result


def _from_disk(conn: apsw.Connection, video_id: str) -> TranscriptResult | None:
    row = conn.execute(
        """
        SELECT title, creator, published_at, transcript_text, source
        FROM transcript_cache WHERE video_id = ?
        """,
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    title, creator, published_at, transcript_text, source = row
    return {
        "transcript": transcript_text,
        "reason": None,
        "title": title or None,
        "creator": creator or None,
        "published_at": published_at or None,
        "source": source,
    }


def _from_corpus(conn: apsw.Connection, video_id: str) -> TranscriptResult | None:
    """Promote an older watched-video transcript into the shared cache.

    Corpus rows created before the transcript cache already contain the raw
    text. Reusing them avoids a needless yt-dlp request when revisiting one
    of those videos; the original source was not recorded, hence "unknown".
    """
    row = conn.execute(
        """
        SELECT title, creator, published_at, transcript_text
        FROM videos WHERE video_id = ?
        """,
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    title, creator, published_at, transcript_text = row
    return {
        "transcript": transcript_text,
        "reason": None,
        "title": title or None,
        "creator": creator or None,
        "published_at": published_at or None,
        "source": "unknown",
    }


def _save_to_disk(conn: apsw.Connection, video_id: str, result: TranscriptResult) -> None:
    transcript = result["transcript"]
    if transcript is None:
        return
    conn.execute(
        """
        INSERT INTO transcript_cache (
            video_id, title, creator, published_at, transcript_text, source, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(video_id) DO UPDATE SET
            title = excluded.title,
            creator = excluded.creator,
            published_at = excluded.published_at,
            transcript_text = excluded.transcript_text,
            source = excluded.source,
            fetched_at = CURRENT_TIMESTAMP
        """,
        (
            video_id,
            result.get("title") or "",
            result.get("creator") or "",
            result.get("published_at") or "",
            transcript,
            result.get("source") or "unknown",
        ),
    )


def get_transcript(
    conn: apsw.Connection,
    video_id: str,
    fetcher: Callable[[str], TranscriptResult] = fetch_transcript,
) -> TranscriptResult:
    """Return a transcript from memory, disk, or the external fetcher.

    Successful results are persisted; failures remain in memory only, so a
    temporary network failure cannot become a durable negative result.
    """
    now = time.monotonic()
    cached = _memory_cache.get(video_id)
    if cached is not None:
        cached_at, result = cached
        if now - cached_at < _MEMORY_TTL_SECONDS:
            return result
        del _memory_cache[video_id]

    disk_result = _from_disk(conn, video_id)
    if disk_result is not None:
        return _remember(video_id, disk_result)

    corpus_result = _from_corpus(conn, video_id)
    if corpus_result is not None:
        _save_to_disk(conn, video_id, corpus_result)
        return _remember(video_id, corpus_result)

    with _inflight_lock:
        in_flight = _inflight.get(video_id)
        if in_flight is None:
            in_flight = _InFlightFetch(complete=Event())
            _inflight[video_id] = in_flight
            is_fetch_leader = True
        else:
            is_fetch_leader = False

    if not is_fetch_leader:
        in_flight.complete.wait()
        if in_flight.error is not None:
            raise in_flight.error
        assert in_flight.result is not None
        return in_flight.result

    try:
        result = fetcher(video_id)
        _save_to_disk(conn, video_id, result)
        in_flight.result = _remember(video_id, result)
        return in_flight.result
    except BaseException as error:
        in_flight.error = error
        raise
    finally:
        with _inflight_lock:
            _inflight.pop(video_id, None)
            in_flight.complete.set()
