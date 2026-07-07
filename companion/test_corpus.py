"""Tests for companion/corpus.py (issue #3).

Inserts a handful of made-up transcripts covering distinctly different
topics, then confirms a query with a new embedding returns the expected
nearest neighbor(s) in the right order - the acceptance criterion for #3.

Run directly: python -m companion.test_corpus
(also discoverable by unittest/pytest as usual)
"""

import os
import tempfile
import unittest

from companion import corpus

TRANSCRIPTS = {
    "sourdough": (
        "sourdough_101",
        "Sourdough Baking 101",
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

        for key, (video_id, title, watched_at, text) in TRANSCRIPTS.items():
            corpus.insert_video(self.conn, video_id, title, watched_at, text)

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
        embedding = corpus.embed_text(TRANSCRIPTS["kubernetes"][3])
        [top] = corpus.query_similar(self.conn, embedding, k=1)
        self.assertEqual(top.video_id, "k8s_intro")
        self.assertEqual(top.title, "Kubernetes Basics for Beginners")
        self.assertEqual(top.watched_at, "2026-01-07T10:00:00Z")
        self.assertEqual(top.transcript_text, TRANSCRIPTS["kubernetes"][3])

    def test_query_k_zero_returns_nothing(self):
        embedding = corpus.embed_text("anything")
        self.assertEqual(corpus.query_similar(self.conn, embedding, k=0), [])

    def test_reinsert_replaces_existing_row(self):
        video_id, title, watched_at, text = TRANSCRIPTS["docker"]
        updated_text = text + " Updated with a note about BuildKit."
        corpus.insert_video(self.conn, video_id, title, watched_at, updated_text)

        count = self.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        self.assertEqual(count, len(TRANSCRIPTS))  # no duplicate row

        embedding = corpus.embed_text(updated_text)
        [top] = corpus.query_similar(self.conn, embedding, k=1)
        self.assertEqual(top.video_id, video_id)
        self.assertEqual(top.transcript_text, updated_text)


if __name__ == "__main__":
    unittest.main()
