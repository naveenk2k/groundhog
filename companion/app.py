"""Groundhog companion: FastAPI app.

This is the foundation piece (issue #1): boot, health check, and secret-header
authentication - which, as ASGI middleware, gates every route below
(except /health) with no extra per-route wiring needed. Transcript fetching
(#2), corpus storage (#3), and the verdict call (#5, currently Gemini -
see companion/verdict.py) build on top of it.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from companion import corpus, verdict
from companion.auth import SecretAuthMiddleware
from companion.transcript import fetch_transcript

app = FastAPI(title="Groundhog companion")
app.add_middleware(SecretAuthMiddleware)

# Lazy singleton: opening the corpus connection (and, via embed_text, loading
# the sentence-transformers model on first use) is expensive enough that we
# don't want to pay it on every request - see companion/corpus.py.
_corpus_conn = None


def _get_corpus_conn():
    global _corpus_conn
    if _corpus_conn is None:
        _corpus_conn = corpus.get_connection()
    return _corpus_conn


@app.get("/health")
async def health() -> dict:
    """Liveness check. Deliberately unauthenticated - see auth.py."""
    return {"status": "ok"}


@app.get("/")
async def root() -> dict:
    """Placeholder authenticated route, useful for verifying the secret works
    end-to-end until #3/#5 add the real pipeline endpoints."""
    return {"status": "ok"}


@app.get("/transcript/{video_id}")
async def transcript(video_id: str) -> dict:
    """Fetch a YouTube video's transcript by ID (issue #2).

    Takes ~2-4 seconds per call (three sequential HTTPS round trips via
    yt-dlp's android_vr client - see companion/transcript.py) - that's an
    accepted cost, not something to optimize here.

    Always returns 200. A missing transcript (deleted/private video, no
    captions, non-English audio) is an expected outcome, represented as
    `{"transcript": None, "reason": "..."}` rather than an error status -
    the extension's overlay treats this the same as any other "can't
    evaluate" case (see PLAN.md).
    """
    return fetch_transcript(video_id)


class VerdictRequest(BaseModel):
    video_id: str
    # Not hardcoded to a fixed 5-10 - a later issue (#9) exposes this as an
    # options-page slider; this endpoint just accepts and passes through
    # whatever K it's given. See DECISIONS.md "Claude call: prompt content
    # and tunables" (predates the Gemini swap; the reasoning still applies).
    k: int = 5
    # Overrides the default model (companion/verdict.py) for this one call -
    # a future model picker (PLAN.md) would set this per-request.
    model: Optional[str] = None


@app.post("/verdict")
async def verdict_endpoint(body: VerdictRequest) -> dict:
    """Judge whether a video says anything new, given the rest of the pipeline.

    Fetches the video's transcript (#2), embeds it and queries the corpus
    for its top-K nearest neighbors (#3), then calls an LLM for a
    structured verdict (#5). Always returns 200: a missing transcript, an
    empty corpus, or a failed/timed-out verdict call all come back as
    `{"error": "..."}` rather than a non-2xx status or a hang - consistent
    with how /transcript already treats "can't evaluate" as a normal,
    representable outcome rather than an exception.
    """
    fetched = fetch_transcript(body.video_id)
    if fetched["transcript"] is None:
        return {"error": f"no transcript available: {fetched['reason']}"}

    embedding = corpus.embed_text(fetched["transcript"])
    matches = corpus.query_similar(_get_corpus_conn(), embedding, body.k)

    new_video = verdict.NewVideo(
        title=fetched["title"] or "",
        creator=fetched["creator"] or "",
        transcript=fetched["transcript"],
    )

    verdict_kwargs = {"model": body.model} if body.model else {}
    return verdict.get_verdict(new_video, matches, **verdict_kwargs)


class WatchedVideoRequest(BaseModel):
    video_id: str


@app.post("/videos/watched")
async def videos_watched(payload: WatchedVideoRequest) -> dict:
    """Add a watched video to the corpus (issue #7).

    The content script's `WatchThresholdTracker` (extension/watch-tracker.js)
    fires exactly one request here per video, once playback crosses 70%
    watched or 5 minutes, whichever comes first - see PLAN.md "Corpus
    policy". This endpoint just does the same fetch-embed-insert pattern
    `add_video.py` already does manually (issue #3's pattern, reused rather
    than reinvented): fetch the transcript, embed it, and upsert it into the
    corpus with `watched_at` set to now.

    A transcript fetch failure (no captions, deleted video, etc.) is a
    normal, expected outcome - not a server error. It's reported as
    `{"added": False, "reason": "..."}` with a 200, rather than a 500, so a
    video you watched but that has no transcript just doesn't get added,
    without the extension treating it as a companion failure.

    Re-watching an already-corpused video is not a special case here:
    `corpus.insert_video` already upserts by `video_id` (replacing the
    existing row rather than erroring or duplicating it), so calling this
    endpoint again for the same video is naturally a no-op duplicate-wise.
    """
    result = fetch_transcript(payload.video_id)
    if result["transcript"] is None:
        return {
            "added": False,
            "video_id": payload.video_id,
            "reason": result["reason"],
        }

    embedding = corpus.embed_text(result["transcript"])

    insert_video_kwargs = dict(
        video_id=payload.video_id,
        title=result["title"] or payload.video_id,
        creator=result["creator"] or "",
        watched_at=datetime.now(timezone.utc).isoformat(),
        transcript_text=result["transcript"],
        embedding=embedding,
    )
    corpus.insert_video(_get_corpus_conn(), **insert_video_kwargs)

    return {
        "added": True,
        "video_id": payload.video_id,
        "title": result["title"],
    }
