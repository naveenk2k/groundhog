"""Tests for companion/verdict_pipeline.py.

Exercises run_verdict_pipeline and add_watched_video directly - no FastAPI
app, no HTTP request - mocking only the two external calls (transcript
fetch, Gemini call) that would otherwise need network access. corpus.py's
own embed/insert/query behavior is exercised for real, same as
test_corpus.py, since it's fast (local CPU embedding model, no network).
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from companion import corpus, verdict_pipeline


class VerdictPipelineTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.remove(self.db_path)  # let apsw create it fresh
        self.conn = corpus.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    @patch("companion.verdict_pipeline.verdict.get_verdict")
    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_run_verdict_pipeline_returns_verdict_on_success(self, mock_fetch, mock_get_verdict):
        mock_fetch.return_value = {
            "transcript": "a transcript",
            "title": "A Title",
            "creator": "A Creator",
            "reason": None,
        }
        mock_get_verdict.return_value = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }

        result = verdict_pipeline.run_verdict_pipeline(self.conn, "vid123", k=3)

        self.assertEqual(result["novelty"], 7)
        mock_fetch.assert_called_once_with("vid123")
        # The corpus matches passed to get_verdict come from a real (empty)
        # corpus query, not a mock - just confirm it was called with the
        # new video built from the mocked transcript.
        args, _ = mock_get_verdict.call_args
        new_video = args[0]
        self.assertEqual(new_video.title, "A Title")
        self.assertEqual(new_video.transcript, "a transcript")

    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_run_verdict_pipeline_no_transcript_returns_error(self, mock_fetch):
        mock_fetch.return_value = {
            "transcript": None,
            "title": None,
            "creator": None,
            "reason": "no captions available",
        }

        result = verdict_pipeline.run_verdict_pipeline(self.conn, "vid123")

        self.assertEqual(result, {"error": "No transcript available for this video."})

    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_add_watched_video_inserts_into_corpus(self, mock_fetch):
        mock_fetch.return_value = {
            "transcript": "a transcript about bread baking",
            "title": "Bread Baking",
            "creator": "Bread Channel",
            "reason": None,
        }

        result = verdict_pipeline.add_watched_video(self.conn, "vid123")

        self.assertEqual(result, {"added": True, "video_id": "vid123", "title": "Bread Baking", "reason": None})
        rows = self.conn.execute("SELECT video_id, title FROM videos").fetchone()
        self.assertEqual(rows, ("vid123", "Bread Baking"))

    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_add_watched_video_no_transcript_does_not_insert(self, mock_fetch):
        mock_fetch.return_value = {
            "transcript": None,
            "title": None,
            "creator": None,
            "reason": "deleted video",
        }

        result = verdict_pipeline.add_watched_video(self.conn, "vid123")

        self.assertEqual(
            result, {"added": False, "video_id": "vid123", "title": None, "reason": "deleted video"}
        )
        count = self.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
