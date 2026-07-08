"""Groundhog companion: the LLM call that turns a transcript into a verdict.

Given a newly-opened video's transcript and its top-K corpus matches from
vector search, asks Gemini to judge whether the new video says anything
substantively new, and returns a structured result (novelty, execution,
depth, explanation, recommendation) via forced schema output rather than
parsed free text.

See DECISIONS.md for the reasoning behind the choices that live in this
module: "Provider swap: Gemini instead of Claude" (why Gemini, and why a
wholesale swap rather than a multi-provider abstraction), "Claude call:
prompt content and tunables" (full transcripts rather than excerpts, K as a
caller-supplied parameter), and "Verdict tone: second person, and naming
specific matched videos" (why the system prompt below addresses the viewer
directly and references matched videos by title/date).

Never raises: a timeout, a non-2xx API error, a connection failure, or an
unparseable response all come back as `{"error": "..."}` instead of
propagating, so a slow or broken call can't hang or crash the caller.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TypedDict

import httpx
from google import genai
from google.genai import errors, types

from companion.corpus import CorpusMatch

# Errors are logged here with full technical detail (exception text, status
# codes) so they're available for debugging in the companion's own log file
# (see install.sh's StandardErrorPath), while the `{"error": "..."}" shape
# returned to callers stays a short, calm, user-facing message - the two
# audiences need different amounts of detail, and a raw exception string
# has no business ending up rendered in the on-page overlay.
logger = logging.getLogger(__name__)

# Gemini's free tier covers Flash models with generous rate limits - see
# module docstring for why this replaced Claude/Haiku. Configurable via env
# var or the `model` kwarg below.
DEFAULT_MODEL = os.environ.get("GROUNDHOG_GEMINI_MODEL", "gemini-2.5-flash")

# Generous but bounded: transcripts are long (tens of KB each, times K+1),
# so the model may genuinely take a while to read and reason over all of
# them. The point isn't a tight budget, it's making sure the request always
# terminates - see module docstring.
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("GROUNDHOG_GEMINI_TIMEOUT_SECONDS", "45"))

# HTTP codes Gemini uses for its own transient overload/rate-limit signals
# (confirmed live: a 503 "This model is currently experiencing high demand"
# response) - distinct from genuine unreachability (DNS failure, connection
# refused, etc.), so these get their own "try again shortly" message instead
# of the generic "couldn't reach the verdict service" bucket.
_TRANSIENT_GEMINI_CODES = {429, 503}

# A select subset of an OpenAPI 3.0 schema object - what Gemini's
# response_schema accepts. No `additionalProperties` (not part of that
# subset); `required` + per-field `minimum`/`maximum` are enough to
# constrain the shape.
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "novelty": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": (
                "1-10: how much this video says that the viewer's watch "
                "history (the matched videos) doesn't already cover. 1 "
                "means it's a rehash of ground already covered; 10 means "
                "it's substantively new territory."
            ),
        },
        "execution": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": (
                "1-10: how well-made the new video is on its own terms - "
                "clarity, rigor, production quality - independent of "
                "whether the topic itself is novel."
            ),
        },
        "depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": (
                "1-10: how deep the new video goes on its subject, as "
                "opposed to a shallow or filler treatment."
            ),
        },
        "explanation": {
            "type": "string",
            "description": (
                "A short (1-3 sentence) explanation grounding the scores "
                "in specifics from the transcripts, written directly to "
                "the viewer in second person ('you', 'your watch "
                "history') rather than about them in the third person. "
                "When the comparison really centers on one particular "
                "watched video, name it directly (e.g. 'this covers much "
                "of the same ground as the road trip video you watched') "
                "instead of only gesturing at 'your watch history' in "
                "the abstract. Mention when that video was watched only "
                "if it adds something useful to say - not as a "
                "mechanical timestamp on every sentence."
            ),
        },
        "recommendation": {
            "type": "string",
            "description": (
                "A short, holistic, plain-language take on whether the "
                "video is worth watching, given everything above - "
                "written directly to the viewer ('you'), not about them. "
                "Not a formula on the scores - your own judgment call."
            ),
        },
    },
    "required": ["novelty", "execution", "depth", "explanation", "recommendation"],
}

_SYSTEM_PROMPT = """\
You are talking directly to someone deciding whether a YouTube video they \
just opened is worth their time, by comparing it to videos they've \
already watched on similar topics. Speak to them directly, in second \
person - "you", "your watch history" - not about them in the third \
person ("the viewer", "the user").

You will be given the new video's full transcript, plus the full \
transcripts of the videos from your watch history that are most similar \
in topic (found via vector search - they are not random, they are the \
closest matches to the new video). Each transcript is labeled with its \
title, creator, and - when available - the date you watched it.

Judge substance, not just topic overlap. Two videos on the same subject \
can be totally different in value: one might be lazy filler restating the \
obvious, the other might be genuinely rigorous and go somewhere new. Read \
the actual text and decide whether the new video is adding something you \
haven't already gotten from the matched videos.

Pay attention to creator: the same channel revisiting its own topic is a \
different signal from several different creators independently covering \
the same ground - the former is more likely to be repetitive, the latter \
suggests the topic is worth multiple treatments and the new video should \
be judged on whether it adds its own angle.

Be specific and personable, not generic. When the comparison really \
centers on one particular watched video, name it directly by title \
("this is similar to the road trip video you watched") instead of only \
referencing "your watch history" as an abstract whole. Mention when you \
watched it only if that's useful context - e.g. it was recent enough to \
be a real rewatch-risk, or long enough ago that a refresher might be \
welcome - not as a mechanical timestamp bolted onto every sentence. If a \
date isn't given for a video, or mentioning it wouldn't add anything, \
just don't bring it up.

Return your scores and a short, concrete explanation as JSON matching the \
required schema. There is no scoring formula behind these numbers - give \
your own honest judgment, grounded in specifics from the transcripts, not \
a generic summary."""


class Verdict(TypedDict):
    novelty: int
    execution: int
    depth: int
    explanation: str
    recommendation: str


class VerdictErrorResult(TypedDict):
    error: str


@dataclass
class NewVideo:
    """The newly-opened video, as passed into get_verdict."""

    title: str
    creator: str
    transcript: str


def _format_watched_at(watched_at: str) -> Optional[str]:
    """Turn a corpus row's ISO 8601 `watched_at` into a readable date for the
    prompt (e.g. "July 7, 2026"), or None if it's missing/unparseable.

    Older corpus rows (backfilled before this field was reliably populated)
    or a bare timestamp format mismatch shouldn't break the prompt - the
    system prompt is told to only mention a date when one is given, so
    omitting it here just means Gemini talks about that video without a
    date, not a crash.
    """
    if not watched_at:
        return None
    try:
        # Python's fromisoformat only accepts a "Z" suffix (rather than an
        # explicit "+00:00" offset) starting in 3.11 - Takeout's own
        # watch-history export timestamps are "Z"-suffixed (see backfill.py),
        # so without normalizing this, every backfilled video would silently
        # never get a date shown, even on newer Python where it'd parse fine.
        normalized = watched_at.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).strftime("%B %-d, %Y")
    except ValueError:
        return None


def _format_video_block(
    label: str, title: str, creator: str, transcript: str, watched_at: Optional[str] = None
) -> str:
    lines = [
        f"--- {label} ---",
        f"Title: {title or '(untitled)'}",
        f"Creator: {creator or '(unknown)'}",
    ]
    if watched_at:
        formatted = _format_watched_at(watched_at)
        if formatted:
            lines.append(f"Watched on: {formatted}")
    lines.append(f"Transcript:\n{transcript}\n")
    return "\n".join(lines)


def _build_user_message(new_video: NewVideo, matches: list[CorpusMatch]) -> str:
    parts = [_format_video_block("NEW VIDEO", new_video.title, new_video.creator, new_video.transcript)]

    if matches:
        parts.append(
            f"\nHere are the {len(matches)} closest videos from your watch "
            "history, ordered by topical similarity (closest first):\n"
        )
        for i, match in enumerate(matches, start=1):
            parts.append(
                _format_video_block(
                    f"WATCHED VIDEO {i}",
                    match.title,
                    match.creator,
                    match.transcript_text,
                    watched_at=match.watched_at,
                )
            )
    else:
        parts.append(
            "\nYour watch history has no videos similar enough to include here "
            "- judge the new video on its own terms.\n"
        )

    return "\n".join(parts)


def get_verdict(
    new_video: NewVideo,
    matches: list[CorpusMatch],
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: Optional[genai.Client] = None,
) -> Verdict | VerdictErrorResult:
    """Ask Gemini to judge the new video against the matched corpus videos.

    `client` is an injection point for tests (pass a fake/mock client to
    avoid real API calls); when omitted, a real `genai.Client()` is
    constructed, which reads `GEMINI_API_KEY` from the environment the same
    way the SDK always does - no custom key storage here.

    Never raises: a timeout, an API error, or a connection failure all come
    back as `{"error": "..."}` instead of propagating, so a slow or broken
    call can't hang or crash the caller (see module docstring).
    """
    try:
        active_client = client if client is not None else genai.Client()
    except Exception as e:  # noqa: BLE001 - e.g. no API key resolvable at all
        logger.error("could not create Gemini client: %s", e)
        return {"error": "Groundhog isn't configured correctly."}

    user_message = _build_user_message(new_video, matches)

    try:
        response = active_client.models.generate_content(
            model=model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=_VERDICT_SCHEMA,
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            ),
        )
    except httpx.TimeoutException as e:
        logger.error("Gemini call timed out after %ss: %s", timeout, e)
        return {"error": "Groundhog took too long to respond."}
    except errors.ClientError as e:
        logger.error("Gemini API client error (%s): %s", e.code, e.message)
        if e.code in _TRANSIENT_GEMINI_CODES:
            return {"error": "Gemini is busy right now - try again in a bit."}
        return {"error": "Couldn't reach the verdict service."}
    except errors.ServerError as e:
        logger.error("Gemini API server error (%s): %s", e.code, e.message)
        if e.code in _TRANSIENT_GEMINI_CODES:
            return {"error": "Gemini is busy right now - try again in a bit."}
        return {"error": "Couldn't reach the verdict service."}
    except errors.APIError as e:
        logger.error("Gemini API error (%s): %s", e.code, e.message)
        return {"error": "Couldn't reach the verdict service."}
    except Exception as e:  # noqa: BLE001 - e.g. a connection failure from the transport
        logger.error("Gemini call failed: %s", e)
        return {"error": "Couldn't reach the verdict service."}

    if not response.parsed:
        logger.error("Gemini did not return a parseable verdict: %r", response)
        return {"error": "Groundhog got an unexpected response from the verdict service."}

    data = response.parsed
    return {
        "novelty": data["novelty"],
        "execution": data["execution"],
        "depth": data["depth"],
        "explanation": data["explanation"],
        "recommendation": data["recommendation"],
    }
