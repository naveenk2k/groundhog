"""Groundhog companion: the verdict and watched-video pipelines.

The "fetch transcript -> embed -> query corpus -> call Gemini" and "fetch
transcript -> embed -> insert into corpus" sequences, each callable
directly - by a test, by a manual mark-as-watched trigger, or by anything
else - without going through FastAPI/HTTP. `app.py`'s routes are thin
adapters: parse the request, call one of the two functions here, serialize
the result.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, TypedDict

import apsw

from companion import corpus, verdict
from companion.transcript import TranscriptResult, fetch_transcript
from companion.verdict import Verdict, VerdictErrorResult

logger = logging.getLogger(__name__)


class WatchedResult(TypedDict):
    added: bool
    video_id: str
    title: Optional[str]
    reason: Optional[str]


# Dedupe the two independent `fetch_transcript` calls (one from `/verdict`
# when the video opens, one from `/videos/watched` a few minutes later once
# the watch threshold is crossed) that the common open->watch path makes for
# the *same* video ID. Each fetch is a real cost (2-4s, three sequential
# HTTPS round trips - see transcript.py's module docstring) and a second,
# unnecessary chance at a transient failure for a video already fetched
# successfully once - so within a short TTL window, the second call reuses
# the first result instead of re-fetching.
#
# A 10-minute TTL comfortably covers the realistic open-to-watch-threshold
# gap without serving meaningfully stale data if the same video is opened
# again much later - though staleness barely matters anyway, since a
# video's transcript doesn't change after publication. The 50-entry cap
# (oldest evicted first) exists purely so the cache can't grow unbounded
# over a long-running companion process; TTL alone would already bound it
# in practice, but a hard cap is cheap insurance against a burst of
# distinct video IDs within one TTL window.
#
# A failed fetch (no transcript available) is cached too: a "no captions"/
# "deleted video" outcome is generally stable, and there's no reason to
# force yt-dlp through the same three-round-trip failure twice in a row for
# a video that just failed - see add_watched_video's docstring for the
# related point that a missing transcript is a normal, expected outcome,
# not the kind of transient error you'd want to retry quickly.
_TRANSCRIPT_CACHE_TTL_SECONDS = 10 * 60
_TRANSCRIPT_CACHE_MAX_ENTRIES = 50
_transcript_cache: dict[str, tuple[float, TranscriptResult]] = {}


def _cached_fetch_transcript(video_id: str) -> TranscriptResult:
    """Fetch `video_id`'s transcript, reusing a recent result if one exists.

    See the module-level `_transcript_cache*` constants above for the TTL,
    size cap, and failure-caching rationale.
    """
    now = time.monotonic()
    cached = _transcript_cache.get(video_id)
    if cached is not None:
        cached_at, result = cached
        if now - cached_at < _TRANSCRIPT_CACHE_TTL_SECONDS:
            return result
        del _transcript_cache[video_id]

    result = fetch_transcript(video_id)

    if len(_transcript_cache) >= _TRANSCRIPT_CACHE_MAX_ENTRIES:
        oldest_video_id = min(_transcript_cache, key=lambda vid: _transcript_cache[vid][0])
        del _transcript_cache[oldest_video_id]
    _transcript_cache[video_id] = (now, result)

    return result


def run_verdict_pipeline(
    conn: apsw.Connection,
    video_id: str,
    k: int = 5,
    model: Optional[str] = None,
) -> Verdict | VerdictErrorResult:
    """Judge whether `video_id` says anything new, given the rest of the pipeline.

    Fetches the video's transcript, embeds it and queries the corpus for its
    top-K nearest neighbors, then calls an LLM for a structured verdict.
    Always returns a plain result, never raises: a missing transcript, an
    empty corpus, or a failed/timed-out verdict call all come back as
    `{"error": "..."}` - see companion/verdict.py's module docstring for why
    the LLM call itself never raises.
    """
    logger.info("verdict requested for video %s", video_id)
    fetched = _cached_fetch_transcript(video_id)
    if fetched["transcript"] is None:
        logger.error("no transcript for video %s: %s", video_id, fetched["reason"])
        return {"error": "No transcript available for this video."}

    embedding = corpus.embed_text(fetched["transcript"])
    matches = corpus.query_similar(conn, embedding, k)

    new_video = verdict.NewVideo(
        title=fetched["title"] or "",
        creator=fetched["creator"] or "",
        transcript=fetched["transcript"],
    )

    verdict_kwargs = {"model": model} if model else {}
    return verdict.get_verdict(new_video, matches, **verdict_kwargs)


def add_watched_video(conn: apsw.Connection, video_id: str) -> WatchedResult:
    """Fetch `video_id`'s transcript and add it to the corpus as watched now.

    A transcript fetch failure (no captions, deleted video, etc.) is a
    normal, expected outcome, not an error - it's reported as
    `{"added": False, "reason": "..."}` rather than raising, so a video
    with no transcript just doesn't get added.

    Re-watching an already-corpused video is not a special case here:
    `corpus.insert_video` already upserts by `video_id`, so calling this
    again for the same video is naturally a no-op duplicate-wise.
    """
    logger.info("watched-video add requested for video %s", video_id)
    fetched = _cached_fetch_transcript(video_id)
    if fetched["transcript"] is None:
        return {"added": False, "video_id": video_id, "title": None, "reason": fetched["reason"]}

    corpus.insert_video(
        conn,
        video_id=video_id,
        title=fetched["title"] or video_id,
        creator=fetched["creator"] or "",
        watched_at=corpus.now_watched_at(),
        transcript_text=fetched["transcript"],
    )

    return {"added": True, "video_id": video_id, "title": fetched["title"], "reason": None}
