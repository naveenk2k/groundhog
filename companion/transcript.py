"""Transcript retrieval for the Groundhog companion.

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
third host). That's an accepted, load-bearing cost - see DECISIONS.md
"Transcript retrieval" - not something to optimize away.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import TypedDict

import yt_dlp

from companion import config
from companion.local_transcriber import transcribe_audio
from companion.transcript_status import set_status

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
    title: str | None
    creator: str | None
    published_at: str | None
    source: str | None


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
        "subtitleslangs": _SUBTITLE_LANGS,
        "subtitlesformat": "vtt",
        "socket_timeout": config.YTDLP_SOCKET_TIMEOUT_SECONDS,
        "retries": 0,
        "extractor_retries": 0,
        # This is the load-bearing option: android_vr is currently exempt
        # from YouTube's PO-token requirement (see module docstring).
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
    }


def _media_ydl_opts(output_template: str) -> dict:
    """Build options for media acquisition, independent of caption clients.

    Caption discovery currently needs ``android_vr``. YouTube media URLs from
    that client can return 403s, though, so audio acquisition deliberately
    uses yt-dlp's normal client selection and lets yt-dlp perform the
    download itself rather than replaying a URL from another extraction.
    """
    return {
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "socket_timeout": config.YTDLP_SOCKET_TIMEOUT_SECONDS,
        "retries": 0,
        "extractor_retries": 0,
    }


def _try_local_transcription(video_id: str, info: dict) -> str | None:
    """Download audio and run local ASR when the optional model is installed."""
    if not config.WHISPER_MODEL.is_file():
        return None

    duration = float(info.get("duration") or 0)
    try:
        set_status(video_id, "downloading_audio")
        with tempfile.TemporaryDirectory(prefix="groundhog-audio-") as temp_dir:
            output_dir = Path(temp_dir)
            output_template = str(output_dir / "audio.%(ext)s")
            with yt_dlp.YoutubeDL(_media_ydl_opts(output_template)) as ydl:
                media_info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            duration = duration or float(media_info.get("duration") or 0)
            downloaded = [path for path in output_dir.glob("audio.*") if path.is_file()]
            if not downloaded:
                raise RuntimeError("yt-dlp downloaded no audio file")
            set_status(video_id, "transcribing")
            return transcribe_audio(
                downloaded[0],
                duration_seconds=duration,
                model_path=config.WHISPER_MODEL,
                threads=config.WHISPER_THREADS,
                timeout_seconds=config.WHISPER_TIMEOUT_SECONDS,
                executable=config.WHISPER_CLI,
                ffmpeg_executable=config.FFMPEG,
            )
    except Exception as e:  # noqa: BLE001 - ASR is an optional fallback
        logging.getLogger(__name__).warning("local transcription failed for %s: %s", video_id, e)
        return None


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


def _extract_creator(info: dict) -> str | None:
    """Pick the creator name out of yt-dlp's info dict.

    Prefers `uploader`, falling back to `channel` - both point at the same
    thing depending on extractor path. For some videos, the `android_vr`
    client (see module docstring) leaves both of those empty even though
    `uploader_id`/`channel_id` are populated, so those are tried next.
    `uploader_id` is typically an `@handle`-style string (e.g.
    `'@BigTechnologyPodcast'`); the leading `@` is stripped since it's not
    part of the display name elsewhere in this codebase. `channel_id` (a
    raw channel ID, not a display name) is the last resort - better than
    nothing, but expect it to look different from a normal creator name."""
    creator = info.get("uploader") or info.get("channel")
    if creator:
        return creator

    uploader_id = info.get("uploader_id")
    if uploader_id:
        return uploader_id.lstrip("@")

    return info.get("channel_id")


def _extract_published_at(info: dict) -> str | None:
    """Pick the video's own publish date out of yt-dlp's info dict, as
    `YYYY-MM-DD`.

    This is when the *video* went up, not when you watched it - `watched_at`
    (companion/corpus.py) already covers that. Distinguishing the two lets
    the verdict prompt tell a genuinely new event (e.g. this week's match)
    from a rehash of an old one, for recurring topics (sports, news) where
    the same discussion shape recurs around different underlying events.
    `upload_date` comes back as a bare `YYYYMMDD` string; reformatted here so
    every caller downstream sees the same shape `watched_at` already uses.
    """
    raw = info.get("upload_date")
    if not raw or len(raw) != 8:
        return None
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


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
    set_status(video_id, "checking_captions")

    # The API already exposes caption URLs in the info dict. The
    # writesubtitles/writeautomaticsub options are only needed when yt-dlp is
    # asked to write files, so keep this path in-memory and reuse one
    # YoutubeDL instance for metadata and caption content.
    try:
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)

            if info is None:
                return {
                    "transcript": None,
                    "reason": "video unavailable or private",
                    "title": None,
                    "creator": None,
                    "published_at": None,
                    "source": None,
                }

            title = info.get("title")
            creator = _extract_creator(info)
            published_at = _extract_published_at(info)

            subtitle_url = _pick_subtitle_url(info)
            if subtitle_url is None:
                local_transcript = _try_local_transcription(video_id, info)
                if local_transcript:
                    set_status(video_id, "complete")
                    return {
                        "transcript": local_transcript,
                        "reason": None,
                        "title": title,
                        "creator": creator,
                        "published_at": published_at,
                        "source": "local_transcription",
                    }
                return {
                    "transcript": None,
                    "reason": "no English captions available",
                    "title": title,
                    "creator": creator,
                    "published_at": published_at,
                    "source": None,
                }

            try:
                vtt_text = ydl.urlopen(subtitle_url).read().decode("utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001
                return {
                    "transcript": None,
                    "reason": f"failed to download caption content: {e}",
                    "title": title,
                    "creator": creator,
                    "published_at": published_at,
                    "source": None,
                }
    except yt_dlp.utils.DownloadError as e:
        return {
            "transcript": None,
            "reason": f"video unavailable: {e}",
            "title": None,
            "creator": None,
            "published_at": None,
            "source": None,
        }
    except Exception as e:  # noqa: BLE001 - deliberately broad, see docstring
        return {
            "transcript": None,
            "reason": f"unexpected error fetching video info: {e}",
            "title": None,
            "creator": None,
            "published_at": None,
            "source": None,
        }

    transcript = _vtt_to_text(vtt_text)
    if not transcript:
        return {
            "transcript": None,
            "reason": "caption track was empty after parsing",
            "title": title,
            "creator": creator,
            "published_at": published_at,
            "source": None,
        }

    set_status(video_id, "complete")
    return {
        "transcript": transcript,
        "reason": None,
        "title": title,
        "creator": creator,
        "published_at": published_at,
        "source": "captions",
    }
