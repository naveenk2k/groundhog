"""Transcript retrieval for the Groundhog companion (issue #2).

Fetches a YouTube video's transcript by video ID using yt-dlp's Python API
(not the CLI - we import yt_dlp directly and call it in-process).

Why `player_client=android_vr`: plain InnerTube calls via the `web`, `ios`,
and `android` clients were all live-tested during design and blocked by a
PO-token wall or outright 400s. `android_vr` was, at the time of writing,
exempt from YouTube's PO-token requirement and pulled full transcripts
cleanly with no browser, cookies, or token exchange. See DECISIONS.md
("Transcript retrieval") for the full writeup.

This is inherently fragile: YouTube's list of PO-token-exempt clients moves
over time. yt-dlp is pinned as a real dependency (not hand-rolled HTTP) so
that churn gets picked up via yt-dlp upgrades. If `android_vr` ever stops
working, the next thing to try is `bgutil-ytdlp-pot-provider` (a local
sidecar that solves the real PO-token challenge) - not implemented here.

Latency note: a successful fetch takes ~2-4 seconds (three sequential HTTPS
round trips: webpage, player API, then the actual caption content from a
third host). That's an accepted, load-bearing cost - see PLAN.md - not
something to optimize away.
"""

from __future__ import annotations

import re
from typing import TypedDict

import yt_dlp

# Prefer English captions; fall back to auto-generated English if no manual
# ones exist. yt-dlp's `subtitleslangs` matches both `en` and regional
# variants like `en-US` via this pattern.
_SUBTITLE_LANGS = ["en", "en-*"]

# Strips the numeric cues / timestamps / index lines that VTT captions carry,
# collapsing the file down to plain spoken text.
_VTT_TIMING_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3} --> .*$")
_VTT_TAG_RE = re.compile(r"<[^>]+>")


class TranscriptResult(TypedDict):
    transcript: str | None
    reason: str | None


class _SilentLogger:
    """Swallows yt-dlp's own error/warning output. `quiet: True` alone still
    lets error lines through to stderr before the exception propagates - we
    handle failures ourselves and don't want yt-dlp double-reporting them."""

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def _ydl_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": _SUBTITLE_LANGS,
        "subtitlesformat": "vtt",
        # This is the load-bearing option: android_vr is currently exempt
        # from YouTube's PO-token requirement (see module docstring).
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
    }


def _pick_subtitle_url(info: dict) -> str | None:
    """Pick a caption track URL from yt-dlp's info dict, preferring manually
    authored subtitles over auto-generated ones, and English over anything
    else that slipped through the language filter."""
    for key in ("subtitles", "automatic_captions"):
        tracks = info.get(key) or {}
        for lang in tracks:
            if lang == "en" or lang.startswith("en-") or lang.startswith("en_"):
                formats = tracks[lang]
                vtt_formats = [f for f in formats if f.get("ext") == "vtt"]
                chosen = vtt_formats[0] if vtt_formats else formats[0]
                return chosen.get("url")
    return None


def _vtt_to_text(vtt: str) -> str:
    """Collapse a WebVTT caption file down to plain spoken text, deduplicating
    consecutive repeated lines (auto-captions often repeat the same line
    across adjacent cues as words are appended one at a time)."""
    lines_out: list[str] = []
    last_line = None
    for raw_line in vtt.splitlines():
        line = raw_line.strip()
        if not line or line == "WEBVTT":
            continue
        if line.isdigit():
            continue
        if _VTT_TIMING_RE.match(line) or "-->" in line:
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
        line = _VTT_TAG_RE.sub("", line).strip()
        if not line:
            continue
        if line != last_line:
            lines_out.append(line)
            last_line = line
    return " ".join(lines_out)


def fetch_transcript(video_id: str) -> TranscriptResult:
    """Fetch the transcript for a YouTube video by ID.

    Returns a dict with either a non-empty `transcript` string and
    `reason: None`, or `transcript: None` and a human-readable `reason`
    explaining why no transcript was available. Never raises for the
    "expected" failure modes (deleted/private video, no captions, no
    English track) - callers can treat this as a normal, non-exceptional
    result. Unexpected errors (network failures, yt-dlp internal errors)
    are also caught and surfaced the same way, since a transcript miss
    should never crash the caller.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        return {"transcript": None, "reason": f"video unavailable: {e}"}
    except Exception as e:  # noqa: BLE001 - deliberately broad, see docstring
        return {"transcript": None, "reason": f"unexpected error fetching video info: {e}"}

    if info is None:
        return {"transcript": None, "reason": "video unavailable or private"}

    subtitle_url = _pick_subtitle_url(info)
    if subtitle_url is None:
        return {"transcript": None, "reason": "no English captions available"}

    try:
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            vtt_text = ydl.urlopen(subtitle_url).read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return {"transcript": None, "reason": f"failed to download caption content: {e}"}

    transcript = _vtt_to_text(vtt_text)
    if not transcript:
        return {"transcript": None, "reason": "caption track was empty after parsing"}

    return {"transcript": transcript, "reason": None}
