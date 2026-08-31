"""Short-lived progress state for a video transcript/verdict request.

The companion is a single local process, so an in-memory registry is enough:
the extension only needs progress for the request currently visible in its
tab. Entries are replaced when a new request starts and are intentionally not
persistent transcript data.
"""

from __future__ import annotations

import time
from typing import Literal, TypedDict


TranscriptStage = Literal["checking_captions", "downloading_audio", "transcribing", "evaluating", "complete"]


class TranscriptStatus(TypedDict):
    stage: TranscriptStage
    elapsed_seconds: float
    total_elapsed_seconds: float


# ``completed_at`` freezes the final duration. Without it, polling a finished
# request later reports time since an old request began instead of the time it
# actually took.
_statuses: dict[str, tuple[float, float, TranscriptStage, float | None]] = {}


def set_status(video_id: str, stage: TranscriptStage) -> None:
    """Record the latest progress stage for ``video_id``."""
    now = time.monotonic()
    previous = _statuses.get(video_id)
    if previous is None or stage == "checking_captions":
        _statuses[video_id] = (now, now, stage, None)
    elif previous[2] != stage:
        _statuses[video_id] = (previous[0], now, stage, now if stage == "complete" else None)


def get_status(video_id: str) -> TranscriptStatus:
    """Return the latest stage, or the initial stage for an unseen video."""
    previous = _statuses.get(video_id)
    if previous is None:
        return {"stage": "checking_captions", "elapsed_seconds": 0.0, "total_elapsed_seconds": 0.0}
    started_at, stage_started_at, stage, completed_at = previous
    now = completed_at if completed_at is not None else time.monotonic()
    return {
        "stage": stage,
        "elapsed_seconds": round(max(0.0, now - stage_started_at), 2),
        "total_elapsed_seconds": round(max(0.0, now - started_at), 2),
    }
