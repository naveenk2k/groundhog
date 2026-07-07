"""Groundhog companion: the Claude call that turns a transcript into a verdict (issue #5).

Given a newly-opened video's transcript and the top-K corpus matches from
vector search (#3), this asks Claude to judge whether the new video says
anything substantively new - and returns a structured result (novelty,
execution, depth, explanation, recommendation) rather than parsed free text.

Structured tool-use, not free-text parsing
-------------------------------------------
Claude is given exactly one tool (`return_verdict`) with a strict JSON
schema and is forced to call it (`tool_choice`). That guarantees the
response shape instead of hoping a text response happens to contain
parseable JSON - see DECISIONS.md ("Claude call: prompt content and
tunables") for why this was chosen over free-text parsing.

Full transcripts, not excerpts
-------------------------------
The prompt includes the new video's full transcript plus the **full
transcripts** of the top-K matches, each labeled with its title and
creator. This is a deliberate, already-settled decision (not something to
"optimize" back down to excerpts) - see DECISIONS.md and PLAN.md
("Scoring", "Claude call: prompt content and tunables"). Creator is
included so Claude can distinguish "the same channel repeating itself"
from "different creators independently converging on the same topic" -
see DECISIONS.md ("Corpus schema").

Model and K are both parameters, not hardcoded
------------------------------------------------
`model` defaults to Haiku (speed-prioritized - this fires on every video
open, per PLAN.md) but is overridable via the `model` argument or the
GROUNDHOG_CLAUDE_MODEL env var; a model picker is noted in PLAN.md as a
reasonable future addition. `k` isn't this module's concern at all - the
caller (app.py) queries the corpus for however many matches it's asked
for and hands the resulting list here; this module just prompts with
whatever it's given.

Timeout handling
-----------------
The Claude call is wrapped with an explicit timeout (default 30s, also
overridable) so a slow or hanging API call can't hang the request
indefinitely. A timeout, a non-2xx API error, or a connection failure all
come back as `{"error": "..."}` rather than raising - callers get a clear
failure indicator instead of a hang or a crash. The full graceful-failure
UX (the on-page "can't evaluate" badge) is issue #10, out of scope here;
this module only needs to fail cleanly and quickly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, TypedDict

import anthropic

from companion.corpus import CorpusMatch

# Haiku is the default per PLAN.md ("speed-prioritized, since this fires on
# every single video you open"). Configurable via env var or the `model`
# kwarg below - a model picker is a documented future addition, not
# required for v1.
DEFAULT_MODEL = os.environ.get("GROUNDHOG_CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Generous but bounded: transcripts are long (tens of KB each, times K+1),
# so Haiku may genuinely take a while to read and reason over all of them.
# The point isn't a tight budget, it's making sure the request always
# terminates - see module docstring.
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("GROUNDHOG_CLAUDE_TIMEOUT_SECONDS", "45"))

_TOOL_NAME = "return_verdict"

_VERDICT_TOOL = {
    "name": _TOOL_NAME,
    "description": (
        "Return your structured judgment of whether the new video says "
        "anything substantively new compared to the videos the viewer has "
        "already watched."
    ),
    "input_schema": {
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
                    "in specifics from the transcripts - e.g. what the new "
                    "video covers that the matches didn't, or where it "
                    "overlaps with a specific matched video."
                ),
            },
            "recommendation": {
                "type": "string",
                "description": (
                    "A short, holistic, plain-language take on whether the "
                    "video is worth watching, given everything above. Not a "
                    "formula on the scores - your own judgment call."
                ),
            },
        },
        "required": ["novelty", "execution", "depth", "explanation", "recommendation"],
        "additionalProperties": False,
    },
    # Haiku 4.5 supports structured outputs / strict tool use - this
    # guarantees `tool_use.input` validates exactly against the schema
    # above rather than merely "usually does".
    "strict": True,
}

_SYSTEM_PROMPT = """\
You are helping someone decide whether a YouTube video they just opened is \
worth their time, by comparing it to videos they've already watched on \
similar topics.

You will be given the new video's full transcript, plus the full \
transcripts of the videos from their watch history that are most similar \
in topic (found via vector search - they are not random, they are the \
closest matches to the new video). Each transcript is labeled with its \
title and creator.

Judge substance, not just topic overlap. Two videos on the same subject \
can be totally different in value: one might be lazy filler restating the \
obvious, the other might be genuinely rigorous and go somewhere new. Read \
the actual text and decide whether the new video is adding something the \
viewer hasn't already gotten from the matched videos.

Pay attention to creator: the same channel revisiting its own topic is a \
different signal from several different creators independently covering \
the same ground - the former is more likely to be repetitive, the latter \
suggests the topic is worth multiple treatments and the new video should \
be judged on whether it adds its own angle.

Call the return_verdict tool exactly once with your scores and a short, \
concrete explanation. There is no scoring formula behind these numbers - \
give your own honest judgment, grounded in specifics from the transcripts, \
not a generic summary."""


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


def _format_video_block(label: str, title: str, creator: str, transcript: str) -> str:
    return (
        f"--- {label} ---\n"
        f"Title: {title or '(untitled)'}\n"
        f"Creator: {creator or '(unknown)'}\n"
        f"Transcript:\n{transcript}\n"
    )


def _build_user_message(new_video: NewVideo, matches: list[CorpusMatch]) -> str:
    parts = [_format_video_block("NEW VIDEO", new_video.title, new_video.creator, new_video.transcript)]

    if matches:
        parts.append(
            f"\nHere are the {len(matches)} closest videos from the viewer's watch "
            "history, ordered by topical similarity (closest first):\n"
        )
        for i, match in enumerate(matches, start=1):
            parts.append(
                _format_video_block(
                    f"WATCHED VIDEO {i}", match.title, match.creator, match.transcript_text
                )
            )
    else:
        parts.append(
            "\nThe viewer's watch history has no videos similar enough to include "
            "here - judge the new video on its own terms.\n"
        )

    return "\n".join(parts)


def get_verdict(
    new_video: NewVideo,
    matches: list[CorpusMatch],
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: Optional[anthropic.Anthropic] = None,
) -> Verdict | VerdictErrorResult:
    """Ask Claude to judge the new video against the matched corpus videos.

    `client` is an injection point for tests (pass a fake/mock client to
    avoid real API calls); when omitted, a real `anthropic.Anthropic()` is
    constructed, which reads `ANTHROPIC_API_KEY` from the environment the
    same way the SDK always does - no custom key storage here, see issue #5.

    Never raises: a timeout, an API error, a connection failure, or a
    missing/invalid API key all come back as `{"error": "..."}` instead of
    propagating, so a slow or broken Claude call can't hang or crash the
    caller (see module docstring).
    """
    try:
        active_client = client if client is not None else anthropic.Anthropic()
    except Exception as e:  # noqa: BLE001 - e.g. no API key resolvable at all
        return {"error": f"could not create Claude client: {e}"}

    user_message = _build_user_message(new_video, matches)

    try:
        response = active_client.with_options(timeout=timeout).messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            tools=[_VERDICT_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APITimeoutError:
        return {"error": f"Claude call timed out after {timeout}s"}
    except anthropic.AuthenticationError:
        return {"error": "Claude API authentication failed - check ANTHROPIC_API_KEY"}
    except anthropic.RateLimitError:
        return {"error": "Claude API rate limited - try again shortly"}
    except anthropic.APIStatusError as e:
        return {"error": f"Claude API error ({e.status_code}): {e.message}"}
    except anthropic.APIConnectionError as e:
        return {"error": f"could not connect to Claude API: {e}"}

    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            data = block.input
            return {
                "novelty": data["novelty"],
                "execution": data["execution"],
                "depth": data["depth"],
                "explanation": data["explanation"],
                "recommendation": data["recommendation"],
            }

    return {"error": "Claude did not return a verdict via the return_verdict tool"}
