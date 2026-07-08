"""Groundhog companion: FastAPI app.

Boot, health check, and secret-header authentication, applied as ASGI
middleware so it gates every route below (except /health) with no extra
per-route wiring needed. Route handlers are thin adapters over
companion/verdict_pipeline.py; transcript fetching lives in
companion/transcript.py and corpus storage in companion/corpus.py.
"""

import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from companion import corpus
from companion.auth import SecretAuthMiddleware
from companion.transcript import fetch_transcript
from companion.verdict_pipeline import add_watched_video, run_verdict_pipeline

# This is the single entry point for the companion process (uvicorn imports
# this module to get `app`), so it's the one place we can be sure runs
# before any other module's `logging.getLogger(__name__)` calls emit
# anything - without this, unconfigured loggers (e.g. verdict.py's,
# verdict_pipeline.py's) fall through to logging's bare `lastResort`
# handler, which prints only the raw message with no timestamp, level, or
# logger name, making .logs/companion.log nearly useless for correlating
# events.
#
# Note: this configures *our own* loggers only. Uvicorn is launched as a
# bare `uvicorn companion.app:app ...` CLI process (see install.sh's
# launchd plist), not via a Python entry point calling `uvicorn.run()`, so
# there's no equally simple place here to also inject a custom
# `log_config` for uvicorn's own access log (e.g. the timestamp-less
# `INFO:     127.0.0.1:52437 - "GET /health HTTP/1.1" 200 OK` lines).
# Deliberately left as-is for now: the video-ID-tagged application logs
# added in verdict_pipeline.py cover most of the actual debugging value
# (answering "did this video's request reach the companion?"), and wiring
# custom uvicorn log config through the launchd CLI invocation is a
# separate, install.sh-scoped concern.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Groundhog companion")
app.add_middleware(SecretAuthMiddleware)
# Added after SecretAuthMiddleware so it wraps *outside* it (Starlette runs
# middleware added later first) and can answer a CORS preflight OPTIONS
# request before the secret check ever runs. Preflights are sent by the
# browser itself and never carry custom headers, so without this,
# SecretAuthMiddleware 401s every preflight and the browser refuses to send
# the real request behind it - this is what actually happened in Safari.
# Wildcard origin/headers are fine here: there's no cookie/credential auth
# to leak, and the secret header still gates every real (non-preflight)
# request exactly as before.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    """Placeholder authenticated route, useful for verifying the secret works end-to-end."""
    return {"status": "ok"}


@app.get("/transcript/{video_id}")
async def transcript(video_id: str) -> dict:
    """Fetch a YouTube video's transcript by ID.

    Takes ~2-4 seconds per call (three sequential HTTPS round trips via
    yt-dlp's android_vr client - see companion/transcript.py) - that's an
    accepted cost, not something to optimize here.

    Always returns 200. A missing transcript (deleted/private video, no
    captions, non-English audio) is an expected outcome, represented as
    `{"transcript": None, "reason": "..."}` rather than an error status -
    the extension's overlay treats this the same as any other "can't
    evaluate" case.
    """
    return fetch_transcript(video_id)


class VerdictRequest(BaseModel):
    video_id: str
    # See DECISIONS.md "Claude call: prompt content and tunables" for why
    # this is a per-request parameter (an options-page slider) rather than
    # a hardcoded constant.
    k: int = 5
    # Overrides the default model (companion/verdict.py) for this one call.
    model: Optional[str] = None


@app.post("/verdict")
async def verdict_endpoint(body: VerdictRequest) -> dict:
    """Judge whether a video says anything new, given the rest of the pipeline.

    Thin adapter over companion/verdict_pipeline.py's run_verdict_pipeline:
    parse the request, run the pipeline, return its result. Always returns
    200: a missing transcript, an empty corpus, or a failed/timed-out
    verdict call all come back as `{"error": "..."}` rather than a non-2xx
    status or a hang.
    """
    return run_verdict_pipeline(_get_corpus_conn(), body.video_id, body.k, body.model)


class WatchedVideoRequest(BaseModel):
    video_id: str


@app.post("/videos/watched")
async def videos_watched(payload: WatchedVideoRequest) -> dict:
    """Add a watched video to the corpus.

    Thin adapter over companion/verdict_pipeline.py's add_watched_video. The
    content script's `WatchThresholdTracker` (extension/watch-tracker.js)
    fires exactly one request here per video, once playback crosses 70%
    watched or 5 minutes, whichever comes first - see PLAN.md "Corpus
    policy".

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
    result = add_watched_video(_get_corpus_conn(), payload.video_id)
    if result["added"]:
        return {"added": True, "video_id": result["video_id"], "title": result["title"]}
    return {"added": False, "video_id": result["video_id"], "reason": result["reason"]}
