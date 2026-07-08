#!/usr/bin/env python3
"""Manually add a single watched video to the persistent corpus.

For adding one specific video outside of the extension's automatic
watch-threshold add (extension/watch-tracker.js) or a bulk backfill.py run.
Fetches the transcript and title via companion.transcript, embeds it, and
inserts it into the corpus DB at its default, persistent path
(companion/config.py's CORPUS_DB_FILE - repo root, gitignored).

Usage:
    python add_video.py https://www.youtube.com/watch?v=VIDEO_ID
    python add_video.py VIDEO_ID
"""

from __future__ import annotations

import re
import sys
from urllib.parse import parse_qs, urlparse

from companion.corpus import get_connection, insert_video, now_watched_at
from companion.transcript import fetch_transcript

_BARE_ID_RE = re.compile(r"^[\w-]{11}$")


def extract_video_id(url_or_id: str) -> str | None:
    """Accept either a bare 11-character video ID or a full watch URL."""
    if _BARE_ID_RE.match(url_or_id):
        return url_or_id
    parsed = urlparse(url_or_id)
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return query_id
    return None


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <youtube-url-or-video-id>", file=sys.stderr)
        sys.exit(1)

    video_id = extract_video_id(sys.argv[1])
    if video_id is None:
        print(f"Could not extract a video ID from {sys.argv[1]!r}", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching transcript for {video_id}...")
    result = fetch_transcript(video_id)
    if result["transcript"] is None:
        print(f"No transcript available: {result['reason']}", file=sys.stderr)
        sys.exit(1)

    title = result["title"] or video_id
    creator = result["creator"] or ""
    print(f"Embedding '{title}' by '{creator or 'unknown creator'}'...")

    conn = get_connection()
    insert_video(
        conn,
        video_id=video_id,
        title=title,
        creator=creator,
        watched_at=now_watched_at(),
        transcript_text=result["transcript"],
    )
    print(f"Added '{title}' ({video_id}) to the corpus.")


if __name__ == "__main__":
    main()
