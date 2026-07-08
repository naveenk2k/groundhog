"""Tests for companion/corpus.py.

Inserts a handful of made-up transcripts covering distinctly different
topics, then confirms a query with a new embedding returns the expected
nearest neighbor(s) in the right order - the acceptance criterion for #3.

Run directly: python -m companion.test_corpus
(also discoverable by unittest/pytest as usual)
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from companion import corpus

TRANSCRIPTS = {
    "sourdough": (
        "sourdough_101",
        "Sourdough Baking 101",
        "Bread Baking Channel",
        "2026-01-05T10:00:00Z",
        "Today we're making sourdough bread from scratch. First you need a "
        "healthy starter that's been fed flour and water for a week. Mix "
        "the starter with bread flour, water, and salt, then let the dough "
        "rest during a long bulk fermentation. Shape the loaf, let it proof "
        "overnight in the fridge, then bake it in a hot dutch oven until "
        "the crust is deep brown and crackling.",
    ),
    "focaccia": (
        "focaccia_basics",
        "Easy Focaccia at Home",
        "Bread Baking Channel",
        "2026-01-06T10:00:00Z",
        "This focaccia recipe starts with a wet, high-hydration dough made "
        "from flour, water, yeast, and olive oil. After a couple of rises "
        "you dimple the dough with your fingers, drizzle it with more olive "
        "oil, and top it with flaky salt and rosemary before baking it hot "
        "and fast until golden and crisp on the bottom.",
    ),
    "kubernetes": (
        "k8s_intro",
        "Kubernetes Basics for Beginners",
        "DevOps Toolbox",
        "2026-01-07T10:00:00Z",
        "Kubernetes is a container orchestration system that schedules "
        "workloads across a cluster of machines. Pods are the smallest "
        "deployable unit, and a deployment manages a set of replica pods "
        "so your application stays available even if a node fails. "
        "Services provide stable networking so pods can find each other "
        "even as they're rescheduled.",
    ),
    "docker": (
        "docker_deep_dive",
        "Docker Deep Dive",
        "DevOps Toolbox",
        "2026-01-08T10:00:00Z",
        "Docker packages an application and its dependencies into a "
        "container image, built in layers from a Dockerfile. Containers "
        "share the host kernel but are isolated from each other via "
        "namespaces and cgroups, which is why they start in milliseconds "
        "instead of the seconds or minutes a full virtual machine takes.",
    ),
    "astronomy": (
        "black_holes",
        "How Black Holes Actually Work",
        "Deep Space Explained",
        "2026-01-09T10:00:00Z",
        "A black hole forms when a massive star collapses under its own "
        "gravity after running out of fuel to fuse. Nothing that crosses "
        "the event horizon can escape, not even light, which is why the "
        "region appears completely black. Near the singularity, our "
        "current physics breaks down entirely.",
    ),
}


class CorpusTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.remove(self.db_path)  # let apsw create it fresh
        self.conn = corpus.get_connection(self.db_path)

        for key, (video_id, title, creator, watched_at, text) in TRANSCRIPTS.items():
            corpus.insert_video(self.conn, video_id, title, creator, watched_at, text)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_insert_persists_rows(self):
        rows = self.conn.execute("SELECT COUNT(*) FROM videos").fetchone()
        self.assertEqual(rows[0], len(TRANSCRIPTS))
        vec_rows = self.conn.execute("SELECT COUNT(*) FROM videos_vec").fetchone()
        self.assertEqual(vec_rows[0], len(TRANSCRIPTS))

    def test_query_returns_nearest_topic_neighbor_first(self):
        # A new, unseen bread-baking transcript should match the two baking
        # videos ahead of the two infra videos and the one astronomy video.
        new_transcript = (
            "This no-knead bread recipe just needs flour, water, salt, and "
            "yeast mixed together and left to rise for many hours before "
            "a short hot bake in a covered pot to get a crisp crust."
        )
        embedding = corpus.embed_text(new_transcript)

        top2 = corpus.query_similar(self.conn, embedding, k=2)
        self.assertEqual(len(top2), 2)
        top2_ids = {m.video_id for m in top2}
        self.assertEqual(top2_ids, {"sourdough_101", "focaccia_basics"})

        # Distances should be non-decreasing (closest first).
        self.assertLessEqual(top2[0].distance, top2[1].distance)

        # Full-corpus query should rank both baking videos ahead of both
        # infra videos and the astronomy video.
        all_matches = corpus.query_similar(self.conn, embedding, k=len(TRANSCRIPTS))
        self.assertEqual(len(all_matches), len(TRANSCRIPTS))
        ranked_ids = [m.video_id for m in all_matches]
        self.assertLess(
            max(ranked_ids.index(v) for v in ("sourdough_101", "focaccia_basics")),
            min(
                ranked_ids.index(v)
                for v in ("k8s_intro", "docker_deep_dive", "black_holes")
            ),
        )

    def test_query_returns_fields_needed_for_claude_prompt(self):
        embedding = corpus.embed_text(TRANSCRIPTS["kubernetes"][4])
        [top] = corpus.query_similar(self.conn, embedding, k=1)
        self.assertEqual(top.video_id, "k8s_intro")
        self.assertEqual(top.title, "Kubernetes Basics for Beginners")
        self.assertEqual(top.creator, "DevOps Toolbox")
        self.assertEqual(top.watched_at, "2026-01-07T10:00:00Z")
        self.assertEqual(top.transcript_text, TRANSCRIPTS["kubernetes"][4])

    def test_query_k_zero_returns_nothing(self):
        embedding = corpus.embed_text("anything")
        self.assertEqual(corpus.query_similar(self.conn, embedding, k=0), [])

    def test_reinsert_replaces_existing_row(self):
        video_id, title, creator, watched_at, text = TRANSCRIPTS["docker"]
        updated_text = text + " Updated with a note about BuildKit."
        corpus.insert_video(self.conn, video_id, title, creator, watched_at, updated_text)

        count = self.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        self.assertEqual(count, len(TRANSCRIPTS))  # no duplicate row

        embedding = corpus.embed_text(updated_text)
        [top] = corpus.query_similar(self.conn, embedding, k=1)
        self.assertEqual(top.video_id, video_id)
        self.assertEqual(top.transcript_text, updated_text)

    def test_insert_video_computes_embedding_when_omitted(self):
        # The single embed+insert entry point every caller (verdict_pipeline,
        # add_video.py, backfill.py) relies on: no caller should need to
        # precompute and pass its own embedding.
        text = "A video about growing tomatoes in a home garden."
        corpus.insert_video(self.conn, "garden_101", "Growing Tomatoes", "Garden Channel", "2026-01-10T00:00:00Z", text)

        embedding = corpus.embed_text(text)
        [top] = corpus.query_similar(self.conn, embedding, k=1)
        self.assertEqual(top.video_id, "garden_101")

    def test_insert_video_uses_explicit_embedding_when_given(self):
        # An explicit embedding is still honored as-is (useful for tests that
        # want a specific vector without loading the real model) rather than
        # always recomputing from transcript_text.
        fake_embedding = [0.0] * corpus.EMBEDDING_DIMENSIONS
        fake_embedding[0] = 1.0
        corpus.insert_video(
            self.conn,
            "fake_embedding_video",
            "Title",
            "Creator",
            "2026-01-10T00:00:00Z",
            "irrelevant text",
            embedding=fake_embedding,
        )
        [top] = corpus.query_similar(self.conn, fake_embedding, k=1)
        self.assertEqual(top.video_id, "fake_embedding_video")


class CorpusMigrationTest(unittest.TestCase):
    """A corpus.db created before the `creator` column existed must keep
    working - this project's own corpus.db already had a real row in it
    when this column was added, so this isn't a hypothetical."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.remove(self.db_path)

        # Build the pre-`creator` schema by hand and seed one row, simulating
        # a corpus.db that predates this migration.
        import apsw
        import sqlite_vec

        old_conn = apsw.Connection(self.db_path)
        old_conn.enable_load_extension(True)
        old_conn.load_extension(sqlite_vec.loadable_path())
        old_conn.enable_load_extension(False)
        old_conn.execute(
            f"""
            CREATE TABLE videos (
                id INTEGER PRIMARY KEY,
                video_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                watched_at TEXT NOT NULL,
                transcript_text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE videos_vec USING vec0(
                embedding float[{corpus.EMBEDDING_DIMENSIONS}]
            );
            """
        )
        old_conn.execute(
            "INSERT INTO videos (video_id, title, watched_at, transcript_text) "
            "VALUES ('pre_migration', 'Old Video', '2025-01-01T00:00:00Z', 'some text')"
        )
        embedding = corpus.embed_text("some text")
        old_conn.execute(
            "INSERT INTO videos_vec (rowid, embedding) VALUES (1, ?)",
            (sqlite_vec.serialize_float32(embedding),),
        )
        old_conn.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_opening_pre_migration_db_adds_creator_column(self):
        conn = corpus.get_connection(self.db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
        self.assertIn("creator", columns)

        row = conn.execute(
            "SELECT video_id, title, creator FROM videos WHERE video_id = 'pre_migration'"
        ).fetchone()
        self.assertEqual(row, ("pre_migration", "Old Video", ""))
        conn.close()

    def test_pre_migration_db_still_queryable(self):
        conn = corpus.get_connection(self.db_path)
        embedding = corpus.embed_text("some text")
        [top] = corpus.query_similar(conn, embedding, k=1)
        self.assertEqual(top.video_id, "pre_migration")
        self.assertEqual(top.creator, "")
        conn.close()


class GetModelOfflineFallbackTest(unittest.TestCase):
    """get_model() tries HF_HUB_OFFLINE first (skips huggingface_hub's
    per-file cache-validation round trip) and falls back to a normal,
    network-touching load if the model isn't cached locally at all."""

    def setUp(self):
        self._saved_model = corpus._model
        corpus._model = None
        self._saved_offline_env = os.environ.pop("HF_HUB_OFFLINE", None)

    def tearDown(self):
        corpus._model = self._saved_model
        if self._saved_offline_env is not None:
            os.environ["HF_HUB_OFFLINE"] = self._saved_offline_env
        else:
            os.environ.pop("HF_HUB_OFFLINE", None)

    def test_falls_back_to_online_load_when_not_cached(self):
        fake_model = object()
        with patch(
            "sentence_transformers.SentenceTransformer",
            side_effect=[OSError("not cached locally"), fake_model],
        ) as mock_st:
            result = corpus.get_model()

        self.assertIs(result, fake_model)
        self.assertEqual(mock_st.call_count, 2)
        # The failed offline attempt's env var is cleared before the retry,
        # so it doesn't linger and affect anything else in the process.
        self.assertNotIn("HF_HUB_OFFLINE", os.environ)

    def test_succeeds_offline_on_first_try_when_cached(self):
        fake_model = object()
        with patch(
            "sentence_transformers.SentenceTransformer", return_value=fake_model
        ) as mock_st:
            result = corpus.get_model()

        self.assertIs(result, fake_model)
        mock_st.assert_called_once()
        self.assertEqual(os.environ.get("HF_HUB_OFFLINE"), "1")


class WatchedAtFormattingTest(unittest.TestCase):
    """Both write paths (verdict_pipeline.py's live path via now_watched_at,
    backfill.py's Takeout-import path via normalize_watched_at) must produce
    the same canonical shape."""

    def test_now_watched_at_matches_canonical_shape(self):
        value = corpus.now_watched_at()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_normalize_watched_at_converts_takeout_format(self):
        # Takeout's raw export format: "Z"-suffixed, millisecond precision.
        self.assertEqual(
            corpus.normalize_watched_at("2026-07-07T10:30:29.831Z"),
            "2026-07-07T10:30:29Z",
        )

    def test_normalize_watched_at_converts_offset_format(self):
        # The old live-path format this replaces: "+00:00" offset, microsecond
        # precision - confirms both historical shapes converge on one.
        self.assertEqual(
            corpus.normalize_watched_at("2026-07-08T15:58:00.123456+00:00"),
            "2026-07-08T15:58:00Z",
        )

    def test_normalize_watched_at_converts_non_utc_offset(self):
        self.assertEqual(
            corpus.normalize_watched_at("2026-07-08T10:58:00-05:00"),
            "2026-07-08T15:58:00Z",
        )

    def test_normalize_watched_at_falls_back_to_raw_on_unparseable_input(self):
        self.assertEqual(corpus.normalize_watched_at(""), "")
        self.assertEqual(corpus.normalize_watched_at("not a date"), "not a date")


if __name__ == "__main__":
    unittest.main()
