"""Tests for companion/app.py: CORS preflight handling and the video lookup
endpoint.

The CORS tests reproduce the actual failure: a browser sends a CORS
preflight OPTIONS request (no secret header - browsers never attach custom
headers to preflights) before the real POST /verdict. Without
CORSMiddleware answering it first, SecretAuthMiddleware 401s the preflight
and the real request never gets sent at all.
"""

import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from companion import config as companion_config
from companion import corpus
from companion.config import SECRET_HEADER

# Redirect the secret file and corpus DB to private temp paths by
# overwriting these modules' own attributes directly, rather than setting
# GROUNDHOG_SECRET_FILE/GROUNDHOG_CORPUS_DB env vars before importing
# companion.config and hoping the module picks them up on first import.
#
# That env-var approach is import-order dependent: SECRET_FILE/
# CORPUS_DB_FILE are each computed once, at whatever moment
# companion.config first gets imported by *any* test module in the same
# run - not necessarily this file. A previous version of this test relied
# on that timing and once actually overwrote the real repo
# .groundhog-secret file, because test_corpus.py happened to import
# companion.config (via `from companion import corpus`) before this file's
# env-var override could take effect.
#
# Direct attribute assignment works regardless of import order instead:
# companion.config.read_secret() looks up companion.config.SECRET_FILE by
# name at call time (not at import time), and companion.corpus.get_connection()
# looks up its own module's CORPUS_DB_FILE name the same way - so patching
# these attributes here, before any request in this file ever triggers a
# read, is always effective no matter which other test module has already
# imported companion.config/companion.corpus.
companion_config.SECRET_FILE = Path(tempfile.mktemp())
companion_config.SECRET_FILE.write_text("test-secret")
corpus.CORPUS_DB_FILE = Path(tempfile.mktemp(suffix=".sqlite"))

from companion.app import app  # noqa: E402 - must follow the patching above


class CorsPreflightTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_options_preflight_succeeds_without_a_secret_header(self):
        response = self.client.options(
            "/verdict",
            headers={
                "Origin": "chrome-extension://fakeextensionid",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": SECRET_HEADER,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access-control-allow-origin", response.headers)

    def test_real_request_still_requires_the_secret_header(self):
        response = self.client.post("/verdict", json={"video_id": "abc", "k": 5})
        self.assertEqual(response.status_code, 401)


class GetVideoLookupTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.conn = corpus.get_connection()
        corpus.insert_video(
            self.conn,
            "already_watched_id",
            "A Video I've Already Watched",
            "Some Creator",
            "2026-01-05T10:00:00Z",
            "irrelevant transcript text",
        )

    def test_found_video_reports_found_with_title_and_watched_at(self):
        response = self.client.get(
            "/videos/already_watched_id", headers={SECRET_HEADER: "test-secret"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "found": True,
                "title": "A Video I've Already Watched",
                "watched_at": "2026-01-05T10:00:00Z",
            },
        )

    def test_unknown_video_reports_not_found(self):
        response = self.client.get(
            "/videos/never_seen_this_one", headers={SECRET_HEADER: "test-secret"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"found": False})

    def test_requires_the_secret_header_like_every_other_route(self):
        response = self.client.get("/videos/already_watched_id")
        self.assertEqual(response.status_code, 401)


class DeleteVideoTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.conn = corpus.get_connection()
        corpus.insert_video(
            self.conn,
            "to_be_removed",
            "A Video I No Longer Want In My History",
            "Some Creator",
            "2026-01-05T10:00:00Z",
            "irrelevant transcript text",
        )

    def test_removes_an_existing_video(self):
        response = self.client.delete(
            "/videos/to_be_removed", headers={SECRET_HEADER: "test-secret"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"removed": True})
        self.assertIsNone(corpus.find_video(self.conn, "to_be_removed"))

    def test_reports_false_for_a_video_not_in_the_corpus(self):
        response = self.client.delete(
            "/videos/never_seen_this_one", headers={SECRET_HEADER: "test-secret"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"removed": False})

    def test_requires_the_secret_header_like_every_other_route(self):
        response = self.client.delete("/videos/to_be_removed")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
