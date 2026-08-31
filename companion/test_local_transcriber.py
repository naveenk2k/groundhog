"""Unit tests for the local transcription boundary."""

import unittest
from pathlib import Path
from unittest.mock import patch

from companion.local_transcriber import (
    ffmpeg_segment_command,
    run_whisper,
    sample_offsets,
    whisper_command,
)


class SampleOffsetsTest(unittest.TestCase):
    def test_short_video_uses_one_full_audio_window(self):
        self.assertEqual(sample_offsets(60), [0.0])

    def test_long_video_uses_intro_middle_and_conclusion(self):
        self.assertEqual(sample_offsets(180), [0.0, 80.0, 160.0])

    def test_short_video_never_has_negative_offset(self):
        self.assertEqual(sample_offsets(-1), [0.0])


class CommandTest(unittest.TestCase):
    def test_ffmpeg_normalizes_to_whisper_input(self):
        command = ffmpeg_segment_command(Path("source.m4a"), Path("segment.wav"), 12.5, 20)
        self.assertEqual(
            command,
            [
                "ffmpeg", "-y", "-loglevel", "error", "-ss", "12.5", "-i", "source.m4a",
                "-t", "20", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "segment.wav",
            ],
        )

    def test_whisper_command_uses_cpu_and_keeps_multiple_segments_in_one_process(self):
        self.assertEqual(
            whisper_command(Path("model.bin"), [Path("intro.wav"), Path("end.wav")], threads=8),
            ["whisper-cli", "-ng", "-t", "8", "-m", "model.bin", "-l", "en", "-nt", "-np", "intro.wav", "end.wav"],
        )


class RunWhisperTest(unittest.TestCase):
    @patch("companion.local_transcriber.subprocess.run")
    def test_returns_trimmed_stdout(self, mock_run):
        mock_run.return_value.stdout = "  hello world  \n"
        self.assertEqual(run_whisper(Path("model.bin"), [Path("audio.wav")]), "hello world")
        self.assertTrue(mock_run.call_args.kwargs["check"])

    @patch("companion.local_transcriber.subprocess.run")
    def test_empty_stdout_is_failure(self, mock_run):
        mock_run.return_value.stdout = "\n"
        with self.assertRaisesRegex(RuntimeError, "no text"):
            run_whisper(Path("model.bin"), [Path("audio.wav")])


if __name__ == "__main__":
    unittest.main()
