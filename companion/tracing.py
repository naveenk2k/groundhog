"""Datadog APM tracing on/off switch for the companion.

ddtrace's own DD_AGENT_HOST / DD_TRACE_AGENT_PORT environment variables
(both already defaulting to localhost:8126) control where spans actually
get sent - this module's only job is the GROUNDHOG_TRACING_ENABLED on/off
switch (companion/config.py) and a loud startup callout when tracing is
turned on but there's no Agent there to receive spans, so that failure
mode is a warning in .logs/companion.error.log instead of silence.

The Datadog API key never appears here or anywhere else in this repo - it
belongs to the Agent's own config (datadog.yaml) outside the repo. See
docs/superpowers/specs/2026-08-23-datadog-tracing-design.md.
"""

from __future__ import annotations

import logging
import os

import httpx
from ddtrace.trace import tracer

from companion import config

logger = logging.getLogger(__name__)


def configure_tracing() -> None:
    """Enables or disables the global ddtrace tracer per GROUNDHOG_TRACING_ENABLED.

    When enabling, also checks that a Datadog Agent is actually reachable at
    the configured host/port, logging a warning (not raising) if not -
    tracing stays enabled regardless, since the Agent may simply not have
    started yet.
    """
    tracer.enabled = config.TRACING_ENABLED
    if not config.TRACING_ENABLED:
        return

    import ddtrace

    ddtrace.patch_all()

    agent_host = os.environ.get("DD_AGENT_HOST", "localhost")
    agent_port = os.environ.get("DD_TRACE_AGENT_PORT", "8126")
    info_url = f"http://{agent_host}:{agent_port}/info"
    try:
        httpx.get(info_url, timeout=1.0).raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(
            "GROUNDHOG_TRACING_ENABLED is set, but no Datadog Agent is reachable at %s (%s) - "
            "traces will be silently dropped until the Agent is running. See "
            "docs/superpowers/specs/2026-08-23-datadog-tracing-design.md for setup.",
            info_url,
            e,
        )
