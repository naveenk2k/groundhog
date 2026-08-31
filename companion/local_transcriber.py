"""Local Whisper transcription for videos without usable captions.

This module deliberately owns only the local ASR boundary. YouTube audio
acquisition stays in transcript.py so the caption-first path remains cheap and
unchanged. The first production candidate is the Homebrew/upstream
whisper.cpp CLI in CPU mode; Metal currently crashes on this Mac's M5/macOS
26 combination (see the local transcription benchmark spec).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Sequence


DEFAULT_SAMPLE_WINDOW_SECONDS = 20.0
DEFAULT_SAMPLE_THRESHOLD_SECONDS = 90.0


def sample_offsets(
    duration_seconds: float,
    window_seconds: float = DEFAULT_SAMPLE_WINDOW_SECONDS,
    threshold_seconds: float = DEFAULT_SAMPLE_THRESHOLD_SECONDS,
) -> list[float]:
    """Return intro/middle/conclusion offsets, or [0] for short audio.

    Offsets are clamped so a video shorter than one window never produces an
    invalid seek. The caller uses the returned offsets to make one process
    invocation with several audio files, keeping the Whisper model warm for
    all three sampled windows.
    """
    duration = max(0.0, float(duration_seconds))
    window = max(1.0, float(window_seconds))
    if duration <= threshold_seconds:
        return [0.0]

    last_start = max(0.0, duration - window)
    middle_start = max(0.0, (duration - window) / 2.0)
    return [0.0, middle_start, last_start]


def ffmpeg_segment_command(input_path: Path, output_path: Path, offset: float, duration: float) -> list[str]:
    """Build the deterministic ffmpeg command used for one ASR segment."""
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        str(max(0.0, offset)),
        "-i",
        str(input_path),
        "-t",
        str(max(1.0, duration)),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]


def whisper_command(
    model_path: Path,
    audio_paths: Sequence[Path],
    threads: int = 10,
    executable: str = "whisper-cli",
) -> list[str]:
    """Build a CPU-safe whisper.cpp invocation for one or more audio files."""
    if not audio_paths:
        raise ValueError("at least one audio file is required")
    return [
        executable,
        "-ng",  # Metal is currently unstable on the target M5/macOS setup.
        "-t",
        str(max(1, int(threads))),
        "-m",
        str(model_path),
        "-l",
        "en",
        "-nt",
        "-np",
        *(str(path) for path in audio_paths),
    ]


def run_whisper(
    model_path: Path,
    audio_paths: Sequence[Path],
    threads: int = 10,
    timeout_seconds: float = 30.0,
    executable: str = "whisper-cli",
) -> str:
    """Transcribe one or more normalized WAV files with whisper.cpp."""
    command = whisper_command(model_path, audio_paths, threads, executable)
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as e:
        raise RuntimeError("whisper-cli is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"local transcription exceeded {timeout_seconds:g}s") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip()
        raise RuntimeError(f"local transcription failed{': ' + detail if detail else ''}") from e

    transcript = completed.stdout.strip()
    if not transcript:
        raise RuntimeError("local transcription returned no text")
    return transcript


def transcribe_audio(
    audio_path: Path,
    duration_seconds: float,
    model_path: Path,
    threads: int = 10,
    timeout_seconds: float = 30.0,
    executable: str = "whisper-cli",
) -> str:
    """Normalize audio, sample long videos, and transcribe in one process."""
    with tempfile.TemporaryDirectory(prefix="groundhog-asr-") as temp_dir:
        temp_path = Path(temp_dir)
        offsets = sample_offsets(duration_seconds)
        segment_paths: list[Path] = []
        for index, offset in enumerate(offsets):
            segment_path = temp_path / f"segment-{index}.wav"
            command = ffmpeg_segment_command(
                audio_path,
                segment_path,
                offset,
                DEFAULT_SAMPLE_WINDOW_SECONDS if len(offsets) > 1 else max(1.0, duration_seconds),
            )
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout_seconds)
            except FileNotFoundError as e:
                raise RuntimeError("ffmpeg is not installed or not on PATH") from e
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"audio normalization exceeded {timeout_seconds:g}s") from e
            except subprocess.CalledProcessError as e:
                detail = (e.stderr or "").strip()
                raise RuntimeError(f"audio normalization failed{': ' + detail if detail else ''}") from e
            segment_paths.append(segment_path)

        return run_whisper(
            model_path,
            segment_paths,
            threads=threads,
            timeout_seconds=timeout_seconds,
            executable=executable,
        )
