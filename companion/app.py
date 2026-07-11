"""Groundhog companion: FastAPI app.

Boot, health check, and secret-header authentication, applied as ASGI
middleware so it gates every route below (except /health) with no extra
per-route wiring needed. Route handlers are thin adapters over
companion/verdict_pipeline.py; transcript fetching lives in
companion/transcript.py and corpus storage in companion/corpus.py.
"""

import json
import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from companion import config, corpus
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
# The trailing "\n" puts a blank line after every record (on top of the
# handler's own line break), so entries in .logs/companion.log stay visually
# separated instead of running together as one dense block.
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s\n",
)

# basicConfig's level applies to every unconfigured logger, not just ours.
# Quiet httpx's and google-genai's own per-request INFO noise, and (when
# GROUNDHOG_DEBUG bumps the root level to DEBUG) asyncio's internal
# selector-loop chatter, none of which is relevant to tracing a request.
for _noisy_logger in ("httpx", "google_genai", "asyncio"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

debug_logger = logging.getLogger("companion.debug")

# Full transcripts can run to tens of thousands of characters - logging one
# in full on every request would make .logs/companion.log both huge and
# mostly noise. A head/tail preview is enough to confirm the right text is
# flowing through the pipeline without paying that cost.
_TRANSCRIPT_PREVIEW_HEAD_CHARS = 200
_TRANSCRIPT_PREVIEW_TAIL_CHARS = 100


def _render_body_for_logging(body: bytes) -> str:
    """Renders a request/response body for the debug log, truncating a
    top-level "transcript" string field (if present) to a head/tail preview.

    Falls back to the raw decoded bytes for anything that isn't a JSON
    object with a "transcript" field - most bodies here (video IDs, verdict
    scores) are already short.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body.decode("utf-8", "replace")

    if isinstance(parsed, dict) and isinstance(parsed.get("transcript"), str):
        transcript = parsed["transcript"]
        preview_len = _TRANSCRIPT_PREVIEW_HEAD_CHARS + _TRANSCRIPT_PREVIEW_TAIL_CHARS
        if len(transcript) > preview_len:
            omitted = len(transcript) - preview_len
            parsed["transcript"] = (
                f"{transcript[:_TRANSCRIPT_PREVIEW_HEAD_CHARS]}"
                f" ...[{omitted} chars omitted]... "
                f"{transcript[-_TRANSCRIPT_PREVIEW_TAIL_CHARS:]}"
            )

    return json.dumps(parsed)


class DebugLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request/response body, with transcript fields truncated
    to a short preview (see _render_body_for_logging).

    Only mounted when config.DEBUG is set (see app setup below) - this is
    for tracing the data trail moving through the companion by hand, not
    something that should run by default.
    """

    async def dispatch(self, request, call_next):
        request_body = await request.body()
        debug_logger.debug(
            "--> %s %s\n%s", request.method, request.url.path, _render_body_for_logging(request_body)
        )

        response = await call_next(request)

        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk
        debug_logger.debug(
            "<-- %s %s %s\n%s",
            request.method,
            request.url.path,
            response.status_code,
            _render_body_for_logging(response_body),
        )

        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


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
# Added last so it's outermost (see the middleware-ordering note above) and
# sees every request/response, including ones CORS or auth would otherwise
# short-circuit.
if config.DEBUG:
    app.add_middleware(DebugLoggingMiddleware)

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


@app.get("/videos/{video_id}")
async def get_video(video_id: str) -> dict:
    """Look up whether a video is already in the corpus, with none of
    /verdict's embedding/similarity-search/Gemini cost.

    Lets the extension skip the full verdict check entirely for a video
    already watched (either auto-added or via "Mark as watched" - see
    /videos/watched below), and lets the overlay reflect that up front
    instead of always defaulting to "Mark as watched".
    """
    found = corpus.find_video(_get_corpus_conn(), video_id)
    if found is None:
        return {"found": False}
    return {"found": True, "title": found["title"], "watched_at": found["watched_at"]}


class WatchedVideoRequest(BaseModel):
    video_id: str


@app.post("/videos/watched")
async def videos_watched(payload: WatchedVideoRequest) -> dict:
    """Add a watched video to the corpus.

    Thin adapter over companion/verdict_pipeline.py's add_watched_video. The
    content script's `WatchThresholdTracker` (extension/watch-tracker.js)
    fires exactly one request here per video, once playback crosses 70%
    watched or 5 minutes, whichever comes first - see DECISIONS.md
    "Corpus policy: 70%/5-minute watch threshold".

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


@app.delete("/videos/{video_id}")
async def delete_video(video_id: str) -> dict:
    """Remove a video from the corpus entirely (issue #42): a real DELETE
    of its metadata row and embedding, not a soft-delete flag - see
    DECISIONS.md ("Removing a video from watch history: hard delete, not
    soft").

    `{"removed": False}` covers both "never in the corpus" and "already
    removed" - neither is an error, so this always returns 200.
    """
    removed = corpus.delete_video(_get_corpus_conn(), video_id)
    return {"removed": removed}
