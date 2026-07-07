"""Groundhog companion: FastAPI app.

This is the foundation piece (issue #1): boot, health check, and secret-header
authentication. Transcript fetching, embeddings, corpus storage, and the
Claude call are separate issues (#2, #3, #5) that will add routes here.
"""

from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel

from companion.auth import SecretAuthMiddleware
from companion.corpus import embed_text, get_connection, insert_video
from companion.transcript import fetch_transcript

app = FastAPI(title="Groundhog companion")
app.add_middleware(SecretAuthMiddleware)


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

    embedding = embed_text(result["transcript"])

    conn = get_connection()
    try:
        insert_video(
            conn,
            video_id=payload.video_id,
            title=result["title"] or payload.video_id,
            creator=result["creator"] or "",
            watched_at=datetime.now(timezone.utc).isoformat(),
            transcript_text=result["transcript"],
            embedding=embedding,
        )
    finally:
        conn.close()

    return {
        "added": True,
        "video_id": payload.video_id,
        "title": result["title"],
    }
