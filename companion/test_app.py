"""Tests for companion/app.py's CORS preflight handling.

Reproduces the actual failure: a browser sends a CORS preflight OPTIONS
request (no secret header - browsers never attach custom headers to
preflights) before the real POST /verdict. Without CORSMiddleware
answering it first, SecretAuthMiddleware 401s the preflight and the real
request never gets sent at all.
"""

import os
import tempfile
import unittest

os.environ.setdefault("GROUNDHOG_SECRET_FILE", tempfile.mktemp())
os.environ.setdefault("GROUNDHOG_CORPUS_DB", tempfile.mktemp(suffix=".sqlite"))

from starlette.testclient import TestClient

from companion import corpus
from companion.app import app
from companion.config import SECRET_FILE, SECRET_HEADER

SECRET_FILE.write_text("test-secret")


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


if __name__ == "__main__":
    unittest.main()
