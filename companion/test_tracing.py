"""Tests for companion/tracing.py: the GROUNDHOG_TRACING_ENABLED on/off
switch and the startup callout when tracing is enabled but no Datadog
Agent is actually reachable.

Run directly: python -m companion.test_tracing
"""

import unittest
from unittest.mock import patch

import httpx
from ddtrace.trace import tracer

from companion import config, tracing


class ConfigureTracingTest(unittest.TestCase):
    def setUp(self):
        # tracer is a process-wide singleton (ddtrace refuses to construct a
        # second Tracer instance) shared with every other test file in the
        # same `unittest discover` run - save/restore both mutated pieces of
        # state so this file can't leak tracing on/off into unrelated tests.
        self._saved_enabled = tracer.enabled
        self._saved_flag = config.TRACING_ENABLED

    def tearDown(self):
        tracer.enabled = self._saved_enabled
        config.TRACING_ENABLED = self._saved_flag

    def test_disabled_by_default_makes_no_network_call(self):
        config.TRACING_ENABLED = False
        with patch("companion.tracing.httpx.get") as mock_get:
            tracing.configure_tracing()
        self.assertFalse(tracer.enabled)
        mock_get.assert_not_called()

    def test_enabled_with_reachable_agent_enables_tracing_and_logs_nothing(self):
        config.TRACING_ENABLED = True
        with patch("companion.tracing.httpx.get") as mock_get:
            with patch("ddtrace.patch"):
                mock_get.return_value.raise_for_status.return_value = None
                with self.assertNoLogs("companion.tracing", level="WARNING"):
                    tracing.configure_tracing()
        self.assertTrue(tracer.enabled)
        mock_get.assert_called_once()

    def test_enabled_with_unreachable_agent_still_enables_but_warns(self):
        config.TRACING_ENABLED = True
        with patch("companion.tracing.httpx.get", side_effect=httpx.ConnectError("refused")):
            with patch("ddtrace.patch"):
                with self.assertLogs("companion.tracing", level="WARNING") as cm:
                    tracing.configure_tracing()
        # Enabled either way - the Agent might start after the companion
        # does, so this is a callout, not a reason to give up on tracing.
        self.assertTrue(tracer.enabled)
        self.assertTrue(any("Datadog Agent" in message for message in cm.output))


if __name__ == "__main__":
    unittest.main()
