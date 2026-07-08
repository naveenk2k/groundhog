"""Tests for companion/verdict.py's get_verdict error mapping.

Uses a fake genai.Client (the injection point get_verdict already supports)
so no real Gemini API calls happen. Covers: successful verdicts, Gemini's
own transient overload/rate-limit signals (429/503) getting a distinct
"try again shortly" message rather than the generic connectivity bucket,
non-transient client/server errors staying in that generic bucket, a
timeout, and an unparseable response getting its own distinct message.
"""

import unittest
from unittest.mock import MagicMock, patch

from google.genai import errors
import httpx

from companion import verdict

NEW_VIDEO = verdict.NewVideo(title="New Video", creator="Some Channel", transcript="a transcript")


def _fake_client(generate_content_result=None, generate_content_side_effect=None):
    client = MagicMock()
    if generate_content_side_effect is not None:
        client.models.generate_content.side_effect = generate_content_side_effect
    else:
        client.models.generate_content.return_value = generate_content_result
    return client


class GetVerdictTest(unittest.TestCase):
    def test_client_construction_failure_maps_to_misconfigured(self):
        with patch("companion.verdict.genai.Client", side_effect=Exception("no API key resolvable")):
            result = verdict.get_verdict(NEW_VIDEO, [])
        self.assertEqual(
            result,
            {"error": "Groundhog isn't configured correctly.", "code": "misconfigured"},
        )

    def test_returns_verdict_on_success(self):
        response = MagicMock()
        response.parsed = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }
        client = _fake_client(generate_content_result=response)

        result = verdict.get_verdict(NEW_VIDEO, [], client=client)

        self.assertEqual(result["novelty"], 7)
        self.assertNotIn("error", result)

    def test_gemini_429_maps_to_busy_message(self):
        client = _fake_client(
            generate_content_side_effect=errors.ClientError(429, {"message": "rate limited"})
        )
        result = verdict.get_verdict(NEW_VIDEO, [], client=client)
        self.assertEqual(
            result,
            {"error": "Gemini is busy right now - try again in a bit.", "code": "gemini_busy"},
        )

    def test_gemini_503_maps_to_busy_message(self):
        client = _fake_client(
            generate_content_side_effect=errors.ServerError(503, {"message": "overloaded"})
        )
        result = verdict.get_verdict(NEW_VIDEO, [], client=client)
        self.assertEqual(
            result,
            {"error": "Gemini is busy right now - try again in a bit.", "code": "gemini_busy"},
        )

    def test_non_transient_client_error_maps_to_generic_message(self):
        client = _fake_client(
            generate_content_side_effect=errors.ClientError(400, {"message": "bad request"})
        )
        result = verdict.get_verdict(NEW_VIDEO, [], client=client)
        self.assertEqual(
            result,
            {"error": "Couldn't reach the verdict service.", "code": "verdict_service_unreachable"},
        )

    def test_non_transient_server_error_maps_to_generic_message(self):
        client = _fake_client(
            generate_content_side_effect=errors.ServerError(500, {"message": "internal error"})
        )
        result = verdict.get_verdict(NEW_VIDEO, [], client=client)
        self.assertEqual(
            result,
            {"error": "Couldn't reach the verdict service.", "code": "verdict_service_unreachable"},
        )

    def test_timeout_maps_to_distinct_message(self):
        client = _fake_client(generate_content_side_effect=httpx.TimeoutException("timed out"))
        result = verdict.get_verdict(NEW_VIDEO, [], client=client, timeout=1.0)
        self.assertEqual(result, {"error": "Groundhog took too long to respond.", "code": "timeout"})

    def test_unparseable_response_maps_to_distinct_message(self):
        response = MagicMock()
        response.parsed = None
        client = _fake_client(generate_content_result=response)

        result = verdict.get_verdict(NEW_VIDEO, [], client=client)

        self.assertEqual(
            result,
            {
                "error": "Groundhog got an unexpected response from the verdict service.",
                "code": "unexpected_verdict_response",
            },
        )


if __name__ == "__main__":
    unittest.main()
