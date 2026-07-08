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

from companion import corpus, verdict
from companion.transcript import fetch_transcript
from companion.verdict import Verdict, VerdictErrorResult

logger = logging.getLogger(__name__)


class WatchedResult(TypedDict):
    added: bool
    video_id: str
    title: Optional[str]
    reason: Optional[str]


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
    fetched = fetch_transcript(video_id)
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
    fetched = fetch_transcript(video_id)
    if fetched["transcript"] is None:
        return {"added": False, "video_id": video_id, "title": None, "reason": fetched["reason"]}

    embedding = corpus.embed_text(fetched["transcript"])
    corpus.insert_video(
        conn,
        video_id=video_id,
        title=fetched["title"] or video_id,
        creator=fetched["creator"] or "",
        watched_at=corpus.now_watched_at(),
        transcript_text=fetched["transcript"],
        embedding=embedding,
    )

    return {"added": True, "video_id": video_id, "title": fetched["title"], "reason": None}
