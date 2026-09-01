"""Tests for the shared transient + durable transcript cache."""

import os
import tempfile
import unittest
from unittest.mock import Mock

from companion import corpus, transcript_store


class TranscriptStoreTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.remove(self.db_path)
        self.conn = corpus.get_connection(self.db_path)
        self._saved_memory_cache = transcript_store._memory_cache
        transcript_store._memory_cache = {}

    def tearDown(self):
        self.conn.close()
        os.remove(self.db_path)
        transcript_store._memory_cache = self._saved_memory_cache

    def test_successful_fetch_is_persisted_without_becoming_a_corpus_video(self):
        fetched = {
            "transcript": "A cached transcript.",
            "reason": None,
            "title": "A video",
            "creator": "A creator",
            "published_at": "2026-09-01",
            "source": "captions",
        }
        first_fetcher = Mock(return_value=fetched)
        second_fetcher = Mock()

        self.assertEqual(transcript_store.get_transcript(self.conn, "video-id", first_fetcher), fetched)
        transcript_store._memory_cache = {}  # prove the durable layer is used after a restart.
        result = transcript_store.get_transcript(self.conn, "video-id", second_fetcher)

        first_fetcher.assert_called_once_with("video-id")
        second_fetcher.assert_not_called()
        self.assertEqual(result["transcript"], "A cached transcript.")
        self.assertEqual(result["source"], "captions")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0], 0)

    def test_failed_fetch_is_not_persisted(self):
        failed = {
            "transcript": None,
            "reason": "no captions available",
            "title": "A video",
            "creator": "A creator",
            "published_at": "2026-09-01",
            "source": None,
        }
        first_fetcher = Mock(return_value=failed)

        transcript_store.get_transcript(self.conn, "video-id", first_fetcher)
        transcript_store._memory_cache = {}
        second_fetcher = Mock(return_value=failed)
        transcript_store.get_transcript(self.conn, "video-id", second_fetcher)

        self.assertEqual(second_fetcher.call_count, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transcript_cache").fetchone()[0], 0)
