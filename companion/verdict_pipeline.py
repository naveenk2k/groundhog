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
from typing import Optional, TypedDict

import apsw
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.trace import tracer

from companion import corpus, verdict
from companion.transcript import fetch_transcript
from companion.transcript_store import get_transcript
from companion.transcript_status import set_status
from companion.verdict import Verdict, VerdictErrorResult

logger = logging.getLogger(__name__)


class WatchedResult(TypedDict):
    added: bool
    video_id: str
    title: Optional[str]
    reason: Optional[str]


def _traced_stage(name: str):
    """tracer.trace(name), marked "measured" so Datadog computes latency/
    error/hit metrics for this span. Datadog only auto-generates those
    metrics for top-level spans by default - the fastapi.request root gets
    them for free, but these pipeline-stage spans are nested underneath it
    and were invisible to metric queries (e.g. a dashboard) until tagged
    explicitly."""
    span = tracer.trace(name)
    span.set_tag(_SPAN_MEASURED_KEY, 1)
    return span


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
    # Start a fresh UI timing window even if the transcript itself comes from
    # the short-lived cache. Otherwise a reload of the same video inherits
    # the previous request's start time.
    set_status(video_id, "checking_captions")

    with _traced_stage("transcript_fetch"):
        fetched = get_transcript(conn, video_id, fetch_transcript)
    if fetched["transcript"] is None:
        logger.error("no transcript for video %s: %s", video_id, fetched["reason"])
        set_status(video_id, "complete")
        return {"error": "No transcript available for this video.", "code": "no_transcript"}

    set_status(video_id, "evaluating")
    with _traced_stage("embed"):
        embedding = corpus.embed_text(fetched["transcript"])

    with _traced_stage("vector_search"):
        matches = corpus.query_similar(conn, embedding, k)
        # Exclude the video's own corpus row. A video already in the corpus
        # - e.g. re-checked via the reload button (issue #47), which
        # deliberately bypasses the normal already-watched dedupe - would
        # otherwise match itself with near-zero distance, surfacing as a
        # "very similar" match (or comparison target) against itself.
        matches = [match for match in matches if match.video_id != video_id]

    new_video = verdict.NewVideo(
        title=fetched["title"] or "",
        creator=fetched["creator"] or "",
        transcript=fetched["transcript"],
        published_at=fetched.get("published_at") or "",
    )

    verdict_kwargs = {"model": model} if model else {}
    with _traced_stage("gemini_call"):
        result = verdict.get_verdict(new_video, matches, **verdict_kwargs)

    set_status(video_id, "complete")
    return result


def add_watched_video(conn: apsw.Connection, video_id: str) -> WatchedResult:
    """Fetch `video_id`'s transcript and add it to the corpus as watched now.

    A transcript fetch failure (no captions, deleted video, etc.) is a
    normal, expected outcome, not an error - it's reported as
    `{"added": False, "reason": "..."}` rather than raising, so a video
    with no transcript just doesn't get added.

    An already-corpused video is reported as such without fetching or
    rewriting it. This keeps the endpoint genuinely idempotent: a delayed
    watch-threshold request cannot turn a rewatch into a fresh add or replace
    the original `watched_at` timestamp.
    """
    logger.info("watched-video add requested for video %s", video_id)
    existing = corpus.find_video(conn, video_id)
    if existing is not None:
        set_status(video_id, "complete")
        return {
            "added": False,
            "video_id": video_id,
            "title": existing["title"],
            "reason": "already_watched",
        }

    with _traced_stage("transcript_fetch"):
        fetched = get_transcript(conn, video_id, fetch_transcript)
    if fetched["transcript"] is None:
        set_status(video_id, "complete")
        return {"added": False, "video_id": video_id, "title": None, "reason": fetched["reason"]}

    set_status(video_id, "evaluating")
    with _traced_stage("corpus_insert"):
        corpus.insert_video(
            conn,
            video_id=video_id,
            title=fetched["title"] or video_id,
            creator=fetched["creator"] or "",
            watched_at=corpus.now_watched_at(),
            transcript_text=fetched["transcript"],
            published_at=fetched.get("published_at") or "",
        )

    set_status(video_id, "complete")
    return {"added": True, "video_id": video_id, "title": fetched["title"], "reason": None}
