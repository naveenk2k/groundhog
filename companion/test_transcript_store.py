"""Tests for the shared transient + durable transcript cache."""

import os
import tempfile
import unittest
from threading import Event, Thread
from unittest.mock import Mock

from companion import corpus, transcript_store


class TranscriptStoreTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.remove(self.db_path)
        self.conn = corpus.get_connection(self.db_path)
        self._saved_memory_cache = transcript_store._memory_cache
        self._saved_inflight = transcript_store._inflight
        transcript_store._memory_cache = {}
        transcript_store._inflight = {}

    def tearDown(self):
        self.conn.close()
        os.remove(self.db_path)
        transcript_store._memory_cache = self._saved_memory_cache
        transcript_store._inflight = self._saved_inflight

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

    def test_concurrent_requests_share_one_external_fetch(self):
        fetched = {
            "transcript": "A cached transcript.",
            "reason": None,
            "title": "A video",
            "creator": "A creator",
            "published_at": "2026-09-01",
            "source": "captions",
        }
        fetch_started = Event()
        release_fetch = Event()
        fetcher = Mock(side_effect=lambda _video_id: (fetch_started.set(), release_fetch.wait(2), fetched)[2])
        results = []

        first = Thread(target=lambda: results.append(transcript_store.get_transcript(self.conn, "video-id", fetcher)))
        second = Thread(target=lambda: results.append(transcript_store.get_transcript(self.conn, "video-id", fetcher)))
        first.start()
        self.assertTrue(fetch_started.wait(1))
        second.start()
        release_fetch.set()
        first.join(2)
        second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        fetcher.assert_called_once_with("video-id")
        self.assertEqual(results, [fetched, fetched])

    def test_existing_corpus_transcript_is_promoted_without_an_external_fetch(self):
        corpus.insert_video(
            self.conn,
            "watched-video",
            "Watched video",
            "Creator",
            "2026-09-01T00:00:00Z",
            "Transcript already in the corpus.",
            embedding=[0.0] * corpus.EMBEDDING_DIMENSIONS,
        )
        fetcher = Mock()

        result = transcript_store.get_transcript(self.conn, "watched-video", fetcher)

        fetcher.assert_not_called()
        self.assertEqual(result["transcript"], "Transcript already in the corpus.")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM transcript_cache WHERE video_id = 'watched-video'").fetchone()[0],
            1,
        )
