#!/usr/bin/env python3
"""Seed the corpus in bulk from a Google Takeout watch-history export.

This is the bulk version of add_video.py: same fetch-transcript -> embed ->
insert_video pipeline, run once per unique video ID found in a Takeout
`watch-history.json` instead of once for a single video passed on the
command line.

Usage:
    python backfill.py <watch-history.json>            # full run
    python backfill.py <watch-history.json> --limit 20  # smoke test first

Takeout export format (verified against a real 6,400-entry export): a JSON
array of objects like:

    {
        "header": "YouTube",
        "title": "Watched <video title>",
        "titleUrl": "https://www.youtube.com/watch?v=VIDEO_ID",
        "subtitles": [{"name": "<channel name>", "url": "..."}],
        "time": "2026-07-07T10:30:29.831Z"
    }

Some entries have no `titleUrl` at all (the video has since been removed from
YouTube) - those are unrecoverable and are skipped without error rather than
failing the whole run.

Sequential, not parallelized: see DECISIONS.md "Backfill" for why (abuse
detection risk on a personal IP, for a workflow that only ever runs once).
Live-tested reference: 5,561 unique videos took roughly 3-6 hours sequential
at 2-4s/video - an overnight job, not a quick script.

Checkpointing: every processed video ID (whether inserted or skipped for
lack of a transcript) is appended to a local JSONL checkpoint file as soon
as it's processed, and video IDs already present in the corpus DB are also
treated as done. Re-running after a crash/interruption resumes instead of
reprocessing - it does not re-fetch transcripts for anything already
recorded either way.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from companion.config import REPO_ROOT
from companion.corpus import get_connection, insert_video, normalize_watched_at
from companion.transcript import fetch_transcript

# One JSON object per line: {"video_id": ..., "status": "inserted"|"skipped"|"error", "reason": ...}
# Overridable via env var for tests, same pattern as GROUNDHOG_CORPUS_DB /
# GROUNDHOG_SECRET_FILE in companion/config.py - tests point this at a
# throwaway path so they never touch a real checkpoint file.
CHECKPOINT_FILE = Path(
    os.environ.get("GROUNDHOG_BACKFILL_CHECKPOINT", str(REPO_ROOT / ".backfill-checkpoint.jsonl"))
)

# How often to print a progress line while working through the backlog.
_PROGRESS_INTERVAL = 10


def extract_video_id(title_url: str) -> str | None:
    """Pull the `v` query parameter (the video ID) out of a Takeout `titleUrl`.

    Returns None if the URL doesn't parse into a recognizable watch URL -
    treated the same as a missing `titleUrl` by the caller (skip, don't
    crash).
    """
    try:
        parsed = urlparse(title_url)
    except ValueError:
        return None
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    return query_id


def load_watch_history(path: str) -> list[dict]:
    """Load and parse the Takeout watch-history.json array."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dedupe_entries(entries: list[dict]) -> tuple[list[tuple[str, str, str]], int]:
    """Dedupe Takeout entries down to one row per unique video ID.

    Keeps the most recent `time` for a video watched more than once (ties
    broken by "last one wins" in file order, which is an arbitrary but
    harmless choice - Takeout timestamps are unique to the millisecond in
    practice). Returns (list of (video_id, creator, watched_at) in first-seen
    order, count of entries skipped for having no recoverable video ID).
    """
    best: dict[str, tuple[str, str]] = {}  # video_id -> (creator, watched_at)
    order: list[str] = []
    skipped_no_url = 0

    for entry in entries:
        title_url = entry.get("titleUrl")
        if not title_url:
            # Video has been removed from YouTube since being watched - unrecoverable.
            skipped_no_url += 1
            continue

        video_id = extract_video_id(title_url)
        if not video_id:
            skipped_no_url += 1
            continue

        subtitles = entry.get("subtitles") or []
        creator = subtitles[0].get("name", "") if subtitles else ""
        watched_at = entry.get("time", "")

        if video_id not in best:
            order.append(video_id)
            best[video_id] = (creator, watched_at)
        else:
            _, existing_watched_at = best[video_id]
            # ISO 8601 timestamps with a shared "Z" suffix sort correctly as
            # plain strings, so a lexical max is enough - no need to parse.
            if watched_at > existing_watched_at:
                best[video_id] = (creator, watched_at)

    return [(vid, best[vid][0], best[vid][1]) for vid in order], skipped_no_url


def load_checkpoint() -> dict[str, dict]:
    """Read the checkpoint file into {video_id: record}, if it exists."""
    if not CHECKPOINT_FILE.exists():
        return {}

    records: dict[str, dict] = {}
    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a truncated last line from a mid-write crash
            records[record["video_id"]] = record
    return records


def append_checkpoint(video_id: str, status: str, reason: str | None = None) -> None:
    """Append one record to the checkpoint file, flushing immediately so a
    crash right after this call still leaves the record on disk."""
    with open(CHECKPOINT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"video_id": video_id, "status": status, "reason": reason}) + "\n")
        f.flush()


def existing_corpus_video_ids(conn) -> set[str]:
    """Video IDs already present in the corpus DB - treated as done even if
    the checkpoint file is missing or was deleted."""
    rows = conn.execute("SELECT video_id FROM videos").fetchall()
    return {row[0] for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("history_json", help="Path to Takeout watch-history.json")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N unique videos (smoke test).",
    )
    args = parser.parse_args()

    print(f"Loading {args.history_json}...")
    entries = load_watch_history(args.history_json)
    print(f"Loaded {len(entries)} watch-history entries.")

    unique_videos, skipped_no_url = dedupe_entries(entries)
    print(
        f"Deduped to {len(unique_videos)} unique video IDs "
        f"({skipped_no_url} entries skipped - no recoverable video URL)."
    )

    if args.limit is not None:
        unique_videos = unique_videos[: args.limit]
        print(f"--limit {args.limit}: processing only the first {len(unique_videos)} unique videos.")

    checkpoint = load_checkpoint()
    conn = get_connection()
    already_in_db = existing_corpus_video_ids(conn)
    already_done = set(checkpoint) | already_in_db

    to_process = [v for v in unique_videos if v[0] not in already_done]
    already_done_count = len(unique_videos) - len(to_process)
    print(
        f"{already_done_count} already done (checkpointed or already in corpus), "
        f"{len(to_process)} left to process."
    )

    inserted = 0
    skipped_no_transcript = 0
    errors = 0
    total = len(to_process)

    for i, (video_id, creator, watched_at) in enumerate(to_process, start=1):
        try:
            result = fetch_transcript(video_id)
            if result["transcript"] is None:
                skipped_no_transcript += 1
                append_checkpoint(video_id, "skipped", result["reason"])
                print(f"  [{i}/{total}] {video_id}: skipped - {result['reason']}")
                continue

            title = result["title"] or video_id
            # Prefer the creator name from Takeout's subtitles field (per
            # DECISIONS.md "Corpus schema"); fall back to whatever yt-dlp
            # reports if Takeout didn't have one for this entry.
            resolved_creator = creator or result["creator"] or ""
            insert_video(
                conn,
                video_id=video_id,
                title=title,
                creator=resolved_creator,
                watched_at=normalize_watched_at(watched_at),
                transcript_text=result["transcript"],
                published_at=result.get("published_at") or "",
            )
            inserted += 1
            append_checkpoint(video_id, "inserted")
        except Exception as e:  # noqa: BLE001 - keep an hours-long run alive
            errors += 1
            append_checkpoint(video_id, "error", str(e))
            print(f"  [{i}/{total}] {video_id}: unexpected error - {e}", file=sys.stderr)

        if i % _PROGRESS_INTERVAL == 0 or i == total:
            print(
                f"progress: {i}/{total} processed "
                f"({inserted} inserted, {skipped_no_transcript} skipped, {errors} errors)"
            )

    print(
        f"Done. {inserted} inserted, {skipped_no_transcript} skipped (no transcript), "
        f"{errors} errors, {already_done_count} already done from a previous run."
    )


if __name__ == "__main__":
    main()
