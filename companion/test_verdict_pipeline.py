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

from ddtrace.internal.writer import TraceWriter
from ddtrace.trace import tracer

from companion import corpus, verdict_pipeline


class _ListWriter(TraceWriter):
    """Collects finished spans in memory instead of sending them anywhere -
    ddtrace.internal.writer.DummyWriter isn't public in ddtrace==4.13.1, so
    this is a minimal stand-in implementing the same abstract interface."""

    def __init__(self):
        self.spans = []

    def recreate(self, appsec_enabled=None, llmobs_enabled=None):
        return self

    def stop(self, timeout=None):
        pass

    def write(self, spans=None):
        if spans:
            self.spans.extend(spans)

    def flush_queue(self):
        pass


class VerdictPipelineTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.remove(self.db_path)  # let apsw create it fresh
        self.conn = corpus.get_connection(self.db_path)
        # The transcript cache is module-level state shared across tests -
        # save/clear it here so cached results from one test can't leak into
        # another, then restore whatever was there (normally nothing).
        self._saved_transcript_cache = verdict_pipeline._transcript_cache
        verdict_pipeline._transcript_cache = {}
        self._saved_tracer_enabled = tracer.enabled
        self._saved_writer = tracer._span_aggregator.writer
        tracer.enabled = True
        self.trace_writer = _ListWriter()
        tracer._span_aggregator.writer = self.trace_writer

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        verdict_pipeline._transcript_cache = self._saved_transcript_cache
        tracer.enabled = self._saved_tracer_enabled
        tracer._span_aggregator.writer = self._saved_writer

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

        self.assertEqual(
            result, {"error": "No transcript available for this video.", "code": "no_transcript"}
        )

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

    @patch("companion.verdict_pipeline.verdict.get_verdict")
    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_run_verdict_pipeline_logs_video_id(self, mock_fetch, mock_get_verdict):
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

        with self.assertLogs("companion.verdict_pipeline", level="INFO") as cm:
            verdict_pipeline.run_verdict_pipeline(self.conn, "vid123", k=3)

        self.assertTrue(any("vid123" in message for message in cm.output))

    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_add_watched_video_logs_video_id(self, mock_fetch):
        mock_fetch.return_value = {
            "transcript": "a transcript about bread baking",
            "title": "Bread Baking",
            "creator": "Bread Channel",
            "reason": None,
        }

        with self.assertLogs("companion.verdict_pipeline", level="INFO") as cm:
            verdict_pipeline.add_watched_video(self.conn, "vid123")

        self.assertTrue(any("vid123" in message for message in cm.output))

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


    @patch("companion.verdict_pipeline.verdict.get_verdict")
    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_verdict_then_watched_for_same_video_fetches_transcript_once(self, mock_fetch, mock_get_verdict):
        mock_fetch.return_value = {
            "transcript": "a transcript about bread baking",
            "title": "Bread Baking",
            "creator": "Bread Channel",
            "reason": None,
        }
        mock_get_verdict.return_value = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }

        verdict_pipeline.run_verdict_pipeline(self.conn, "vid123", k=3)
        result = verdict_pipeline.add_watched_video(self.conn, "vid123")

        self.assertEqual(result["added"], True)
        mock_fetch.assert_called_once_with("vid123")

    @patch("companion.verdict_pipeline.verdict.get_verdict")
    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_different_video_ids_each_trigger_own_fetch(self, mock_fetch, mock_get_verdict):
        def fake_fetch(video_id):
            return {
                "transcript": f"transcript for {video_id}",
                "title": f"Title {video_id}",
                "creator": "Some Creator",
                "reason": None,
            }

        mock_fetch.side_effect = fake_fetch
        mock_get_verdict.return_value = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }

        verdict_pipeline.run_verdict_pipeline(self.conn, "vid123", k=3)
        verdict_pipeline.run_verdict_pipeline(self.conn, "vid456", k=3)

        self.assertEqual(mock_fetch.call_count, 2)
        mock_fetch.assert_any_call("vid123")
        mock_fetch.assert_any_call("vid456")

    @patch("companion.verdict_pipeline.verdict.get_verdict")
    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_run_verdict_pipeline_emits_a_span_per_stage(self, mock_fetch, mock_get_verdict):
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

        verdict_pipeline.run_verdict_pipeline(self.conn, "vid123", k=3)

        span_names = [s.name for s in self.trace_writer.spans]
        self.assertEqual(
            span_names, ["transcript_fetch", "embed", "vector_search", "gemini_call"]
        )

    @patch("companion.verdict_pipeline.verdict.get_verdict")
    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_run_verdict_pipeline_excludes_own_video_from_matches(self, mock_fetch, mock_get_verdict):
        # Simulate a video that's already in the corpus (e.g. from a prior
        # watch) being re-checked - the issue #47 reload/re-check button
        # deliberately re-fires /verdict for an already-watched video. Its
        # own corpus row must not come back as one of its "similar" matches.
        transcript_text = "a transcript about bread baking"
        corpus.insert_video(
            self.conn,
            video_id="vid123",
            title="Bread Baking",
            creator="Bread Channel",
            watched_at=corpus.now_watched_at(),
            transcript_text=transcript_text,
            published_at="",
        )
        mock_fetch.return_value = {
            "transcript": transcript_text,
            "title": "Bread Baking",
            "creator": "Bread Channel",
            "reason": None,
        }
        mock_get_verdict.return_value = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }

        verdict_pipeline.run_verdict_pipeline(self.conn, "vid123", k=3)

        args, _ = mock_get_verdict.call_args
        matches = args[1]
        self.assertFalse(any(match.video_id == "vid123" for match in matches))

    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_add_watched_video_emits_a_span_per_stage(self, mock_fetch):
        mock_fetch.return_value = {
            "transcript": "a transcript about bread baking",
            "title": "Bread Baking",
            "creator": "Bread Channel",
            "reason": None,
        }

        verdict_pipeline.add_watched_video(self.conn, "vid123")

        span_names = [s.name for s in self.trace_writer.spans]
        self.assertEqual(span_names, ["transcript_fetch", "corpus_insert"])


if __name__ == "__main__":
    unittest.main()
