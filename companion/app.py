"""Groundhog companion: FastAPI app.

This is the foundation piece (issue #1): boot, health check, and secret-header
authentication. Transcript fetching, embeddings, corpus storage, and the
Claude call are separate issues (#2, #3, #5) that will add routes here.
"""

from fastapi import FastAPI

from companion.auth import SecretAuthMiddleware
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
