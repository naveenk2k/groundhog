"""Tests for companion/app.py's CORS preflight handling (issue #23).

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


if __name__ == "__main__":
    unittest.main()
