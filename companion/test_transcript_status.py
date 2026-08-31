import unittest
from unittest.mock import patch

from companion import transcript_status


class TranscriptStatusTest(unittest.TestCase):
    def setUp(self):
        transcript_status._statuses.clear()

    def test_unknown_video_starts_with_caption_check(self):
        self.assertEqual(
            transcript_status.get_status("new-video"),
            {"stage": "checking_captions", "elapsed_seconds": 0.0, "total_elapsed_seconds": 0.0},
        )

    def test_status_updates_for_a_video(self):
        transcript_status.set_status("video", "downloading_audio")
        status = transcript_status.get_status("video")
        self.assertEqual(status["stage"], "downloading_audio")
        self.assertGreaterEqual(status["elapsed_seconds"], 0.0)
        self.assertGreaterEqual(status["total_elapsed_seconds"], status["elapsed_seconds"])

    def test_new_stage_resets_stage_timer_but_keeps_total_timer(self):
        transcript_status.set_status("video", "downloading_audio")
        first = transcript_status.get_status("video")
        transcript_status.set_status("video", "transcribing")
        second = transcript_status.get_status("video")
        self.assertEqual(second["stage"], "transcribing")
        self.assertLessEqual(second["elapsed_seconds"], second["total_elapsed_seconds"])
        self.assertGreaterEqual(second["total_elapsed_seconds"], first["total_elapsed_seconds"])

    @patch("companion.transcript_status.time.monotonic", side_effect=[100.0, 105.0, 180.0])
    def test_completed_duration_does_not_keep_growing(self, monotonic):
        transcript_status.set_status("video", "checking_captions")
        transcript_status.set_status("video", "complete")

        status = transcript_status.get_status("video")

        self.assertEqual(status["total_elapsed_seconds"], 5.0)
