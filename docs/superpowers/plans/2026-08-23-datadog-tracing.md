# Datadog APM Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-off manual timing instrumentation in `verdict_pipeline.py` with real Datadog APM spans, so per-request pipeline-stage latency (transcript fetch, embed, vector search, Gemini call) is visible as a waterfall trace going forward, with a loud callout if the local Datadog Agent isn't reachable.

**Architecture:** The companion process (already ddtrace-instrumented) sends spans to a local Datadog Agent over `localhost:8126`; the Agent batches and forwards them to Datadog's cloud. A single `GROUNDHOG_TRACING_ENABLED` flag (same convention as the existing `GROUNDHOG_DEBUG`) turns tracing on; when on, a startup check confirms the Agent is actually there and warns loudly if not.

**Tech Stack:** `ddtrace==4.13.1` (Datadog's Python tracing library), the existing `httpx` dependency for the Agent reachability check, `unittest`/`unittest.mock` for tests (matching the rest of `companion/`).

## Global Constraints

- Companion Python version: 3.12 (see `install.sh`).
- No new test dependency: `unittest.mock` and stdlib `unittest` only, matching every existing `companion/test_*.py` file.
- `DD_API_KEY` never appears anywhere in this repo - it belongs to the Datadog Agent's own config outside the repo (see "Manual setup" at the end of this plan).
- Tracing must default to fully off (`tracer.enabled == False`, zero network calls) when `GROUNDHOG_TRACING_ENABLED` is unset - existing tests and any user without a Datadog account must be unaffected.
- Verified against the actually-installed `ddtrace==4.13.1` API in this repo's `.venv` before writing this plan: `from ddtrace.trace import tracer` (global singleton - do not instantiate `Tracer()` directly, it warns "Initializing multiple Tracer instances is not supported"), `tracer.enabled = False` makes `tracer.trace(...)` return a real no-op span (safe to call unconditionally), and the writer test seam is `tracer._span_aggregator.writer` (an object implementing `ddtrace.internal.writer.TraceWriter`'s abstract `write(spans)`/`recreate()`/`stop()`/`flush_queue()` methods) - `ddtrace.internal.writer.DummyWriter` does **not** exist in this version, so tests define their own tiny collecting `TraceWriter` subclass.

---

### Task 1: `companion/tracing.py` - the on/off switch and Agent reachability callout

**Files:**
- Modify: `requirements.txt` (add `ddtrace==4.13.1`, right after the existing dependency comments' style)
- Modify: `companion/config.py` (add `TRACING_ENABLED`)
- Create: `companion/tracing.py`
- Create: `companion/test_tracing.py`
- Modify: `companion/app.py:20` (import) and around `companion/app.py:51` (call `configure_tracing()`)

**Interfaces:**
- Produces: `companion.config.TRACING_ENABLED: bool`, `companion.tracing.configure_tracing() -> None`. Task 2 imports `from ddtrace.trace import tracer` directly (the same global singleton this task configures) - it does not call anything from `tracing.py`.

- [ ] **Step 1: Add the dependency**

Add this line to `requirements.txt`, after the `apsw` block at the end:

```
# Datadog APM tracing (opt-in via GROUNDHOG_TRACING_ENABLED - see
# companion/tracing.py and docs/superpowers/specs/2026-08-23-datadog-tracing-design.md).
# Pinned since ddtrace's internal (non-public) writer APIs are used directly
# in tests - see companion/test_verdict_pipeline.py.
ddtrace==4.13.1
```

Run: `uv pip install --python .venv/bin/python -r requirements.txt`
Expected: installs cleanly (it's already been installed once manually while writing this plan, so this should be an instant no-op).

- [ ] **Step 2: Add the config flag**

In `companion/config.py`, right after the existing `DEBUG` line, add:

```python
# Off by default: emits Datadog APM spans for every /verdict and
# /videos/watched request when a local Datadog Agent is running (see
# companion/tracing.py). Same opt-in convention as DEBUG above. See
# docs/superpowers/specs/2026-08-23-datadog-tracing-design.md.
TRACING_ENABLED = os.environ.get("GROUNDHOG_TRACING_ENABLED", "").strip().lower() in ("1", "true", "yes")
```

- [ ] **Step 3: Write the failing tests for `configure_tracing()`**

Create `companion/test_tracing.py`:

```python
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
            mock_get.return_value.raise_for_status.return_value = None
            with self.assertNoLogs("companion.tracing", level="WARNING"):
                tracing.configure_tracing()
        self.assertTrue(tracer.enabled)
        mock_get.assert_called_once()

    def test_enabled_with_unreachable_agent_still_enables_but_warns(self):
        config.TRACING_ENABLED = True
        with patch("companion.tracing.httpx.get", side_effect=httpx.ConnectError("refused")):
            with self.assertLogs("companion.tracing", level="WARNING") as cm:
                tracing.configure_tracing()
        # Enabled either way - the Agent might start after the companion
        # does, so this is a callout, not a reason to give up on tracing.
        self.assertTrue(tracer.enabled)
        self.assertTrue(any("Datadog Agent" in message for message in cm.output))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.venv/bin/python -m unittest companion.test_tracing -v`
Expected: `ModuleNotFoundError: No module named 'companion.tracing'`

- [ ] **Step 5: Write `companion/tracing.py`**

```python
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
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/python -m unittest companion.test_tracing -v`
Expected: 3 tests, all PASS

- [ ] **Step 7: Wire it into `app.py`**

In `companion/app.py:20`, change:

```python
from companion import config, corpus
```

to:

```python
from companion import config, corpus, tracing
```

Then, immediately after the existing `logging.basicConfig(...)` call (the block ending around `companion/app.py:51`) and before the `debug_logger = logging.getLogger("companion.debug")` line, add:

```python
tracing.configure_tracing()
```

- [ ] **Step 8: Run the full companion test suite to confirm nothing broke**

Run: `.venv/bin/python -m unittest discover -s . -p "test_*.py"`
Expected: all tests pass (65 existing + 3 new = 68)

- [ ] **Step 9: Commit**

```bash
git add requirements.txt companion/config.py companion/tracing.py companion/test_tracing.py companion/app.py
git commit -m "Add opt-in Datadog APM tracing on/off switch with Agent-reachability callout"
```

---

### Task 2: Instrument `verdict_pipeline.py` with real spans

**Files:**
- Modify: `companion/verdict_pipeline.py` (replace the `time.monotonic()` block from Task work already partially done - see current file - with `tracer.trace(...)` spans)
- Modify: `companion/test_verdict_pipeline.py` (add two new test methods to the existing `VerdictPipelineTest` class)

**Interfaces:**
- Consumes: `from ddtrace.trace import tracer` (the same global singleton Task 1's `configure_tracing()` turns on/off) and `ddtrace.internal.writer.TraceWriter` (abstract base for the test's collecting writer double).
- Produces: spans named `transcript_fetch`, `embed`, `vector_search`, `gemini_call` (from `run_verdict_pipeline`) and `transcript_fetch`, `corpus_insert` (from `add_watched_video`).

- [ ] **Step 1: Write the failing tests**

Add these two methods to the `VerdictPipelineTest` class in `companion/test_verdict_pipeline.py` (alongside the existing tests - keep the existing `import` lines and add to them):

```python
from ddtrace.internal.writer import TraceWriter
from ddtrace.trace import tracer


class _ListWriter(TraceWriter):
    """Collects finished spans in memory instead of sending them anywhere -
    ddtrace.internal.writer.DummyWriter isn't public in ddtrace==4.13.1, so
    this is a minimal stand-in implementing the same abstract interface."""

    def __init__(self):
        self.spans = []

    def recreate(self, appsec_enabled=None, llmobs_enabled=None):
        return self

    def stop(self, timeout=None):
        pass

    def write(self, spans=None):
        if spans:
            self.spans.extend(spans)

    def flush_queue(self):
        pass
```

Then, as new methods on `VerdictPipelineTest`:

```python
    def setUp(self):
        # ... existing setUp body stays exactly as-is, then add:
        self._saved_tracer_enabled = tracer.enabled
        self._saved_writer = tracer._span_aggregator.writer
        tracer.enabled = True
        self.trace_writer = _ListWriter()
        tracer._span_aggregator.writer = self.trace_writer

    def tearDown(self):
        # ... existing tearDown body stays exactly as-is, then add:
        tracer.enabled = self._saved_tracer_enabled
        tracer._span_aggregator.writer = self._saved_writer

    @patch("companion.verdict_pipeline.verdict.get_verdict")
    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_run_verdict_pipeline_emits_a_span_per_stage(self, mock_fetch, mock_get_verdict):
        mock_fetch.return_value = {
            "transcript": "a transcript",
            "title": "A Title",
            "creator": "A Creator",
            "reason": None,
        }
        mock_get_verdict.return_value = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }

        verdict_pipeline.run_verdict_pipeline(self.conn, "vid123", k=3)

        span_names = [s.name for s in self.trace_writer.spans]
        self.assertEqual(
            span_names, ["transcript_fetch", "embed", "vector_search", "gemini_call"]
        )

    @patch("companion.verdict_pipeline.fetch_transcript")
    def test_add_watched_video_emits_a_span_per_stage(self, mock_fetch):
        mock_fetch.return_value = {
            "transcript": "a transcript about bread baking",
            "title": "Bread Baking",
            "creator": "Bread Channel",
            "reason": None,
        }

        verdict_pipeline.add_watched_video(self.conn, "vid123")

        span_names = [s.name for s in self.trace_writer.spans]
        self.assertEqual(span_names, ["transcript_fetch", "corpus_insert"])
```

Note precisely where to add the `setUp`/`tearDown` lines: the existing methods already exist in the file (creating `self.conn` and saving `_transcript_cache`) - add the tracer-related lines shown above at the end of each existing method's body, don't replace the whole method.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest companion.test_verdict_pipeline -v`
Expected: `test_run_verdict_pipeline_emits_a_span_per_stage` and `test_add_watched_video_emits_a_span_per_stage` FAIL with an empty `span_names` list (`[] != ["transcript_fetch", ...]`) - no spans exist yet, since nothing calls `tracer.trace()` yet.

- [ ] **Step 3: Replace the manual timing block in `verdict_pipeline.py`**

Add this import near the top of `companion/verdict_pipeline.py` (alongside the existing `import apsw` line):

```python
from ddtrace.trace import tracer
```

Then replace the entire body of `run_verdict_pipeline` from `logger.info("verdict requested for video %s", video_id)` through the final `return result` (i.e. everything currently using `stage_start`/`transcript_seconds`/`embed_seconds`/`search_seconds`/`gemini_seconds`) with:

```python
    logger.info("verdict requested for video %s", video_id)

    with tracer.trace("transcript_fetch"):
        fetched = _cached_fetch_transcript(video_id)
    if fetched["transcript"] is None:
        logger.error("no transcript for video %s: %s", video_id, fetched["reason"])
        return {"error": "No transcript available for this video.", "code": "no_transcript"}

    with tracer.trace("embed"):
        embedding = corpus.embed_text(fetched["transcript"])

    with tracer.trace("vector_search"):
        matches = corpus.query_similar(conn, embedding, k)

    new_video = verdict.NewVideo(
        title=fetched["title"] or "",
        creator=fetched["creator"] or "",
        transcript=fetched["transcript"],
        published_at=fetched.get("published_at") or "",
    )

    verdict_kwargs = {"model": model} if model else {}
    with tracer.trace("gemini_call"):
        result = verdict.get_verdict(new_video, matches, **verdict_kwargs)

    return result
```

Also remove the now-unused `import time` line if nothing else in the file uses it (check with `grep -n "time\." companion/verdict_pipeline.py` - `_cached_fetch_transcript` still uses `time.monotonic()` for the TTL cache, so **keep** the `import time` line; only the per-request stage timing is being replaced).

Then, in `add_watched_video`, change:

```python
    logger.info("watched-video add requested for video %s", video_id)
    fetched = _cached_fetch_transcript(video_id)
    if fetched["transcript"] is None:
        return {"added": False, "video_id": video_id, "title": None, "reason": fetched["reason"]}

    corpus.insert_video(
        conn,
        video_id=video_id,
        title=fetched["title"] or video_id,
        creator=fetched["creator"] or "",
        watched_at=corpus.now_watched_at(),
        transcript_text=fetched["transcript"],
        published_at=fetched.get("published_at") or "",
    )

    return {"added": True, "video_id": video_id, "title": fetched["title"], "reason": None}
```

to:

```python
    logger.info("watched-video add requested for video %s", video_id)
    with tracer.trace("transcript_fetch"):
        fetched = _cached_fetch_transcript(video_id)
    if fetched["transcript"] is None:
        return {"added": False, "video_id": video_id, "title": None, "reason": fetched["reason"]}

    with tracer.trace("corpus_insert"):
        corpus.insert_video(
            conn,
            video_id=video_id,
            title=fetched["title"] or video_id,
            creator=fetched["creator"] or "",
            watched_at=corpus.now_watched_at(),
            transcript_text=fetched["transcript"],
            published_at=fetched.get("published_at") or "",
        )

    return {"added": True, "video_id": video_id, "title": fetched["title"], "reason": None}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest companion.test_verdict_pipeline -v`
Expected: all tests in the file PASS, including the two new ones.

- [ ] **Step 5: Run the full companion test suite**

Run: `.venv/bin/python -m unittest discover -s . -p "test_*.py"`
Expected: all tests pass (68 from Task 1 + 2 new = 70)

- [ ] **Step 6: Manual smoke test against a live Agent (optional, requires Datadog Agent set up per this plan's "Manual setup" section)**

```bash
launchctl kickstart -k gui/$(id -u)/com.groundhog.companion
SECRET=$(cat .groundhog-secret)
curl -s -X POST http://127.0.0.1:8787/verdict \
  -H "Content-Type: application/json" \
  -H "X-Groundhog-Secret: $SECRET" \
  -d '{"video_id": "dQw4w9WgXcQ", "k": 5}' > /dev/null
```

Then check the Datadog APM UI (Traces) for a `groundhog-companion` service trace on `/verdict` with four child spans. If `GROUNDHOG_TRACING_ENABLED` wasn't set in the companion's environment, check `.logs/companion.error.log` for the "no Datadog Agent is reachable" warning instead - either outcome confirms the wiring works end to end.

- [ ] **Step 7: Commit**

```bash
git add companion/verdict_pipeline.py companion/test_verdict_pipeline.py
git commit -m "Instrument verdict_pipeline stages with Datadog APM spans"
```

---

## Manual setup (outside this plan - not code, do this yourself)

1. In the Datadog web UI (datadoghq.com, logged into the account created via the GitHub Student Developer Pack): **Organization Settings -> API Keys -> New Key**. Copy it.
2. `brew install --cask datadog-agent`
3. Edit `/opt/datadog-agent/etc/datadog.yaml`:
   ```yaml
   api_key: <the key from step 1>
   apm_config:
     enabled: true
   ```
4. Start the Agent (the cask installer registers it as a launch daemon that starts automatically; if it's not already running, `sudo launchctl kickstart -k system/com.datadoghq.agent` or open the Datadog Agent app from Applications).
5. Confirm it's up: `curl http://localhost:8126/info` should return JSON, not a connection error.
6. Set `GROUNDHOG_TRACING_ENABLED=1` in the companion's environment (add it to the `EnvironmentVariables` dict in `~/Library/LaunchAgents/com.groundhog.companion.plist`, the same way `GEMINI_API_KEY` is already injected there by `install.sh`, then `launchctl kickstart -k gui/$(id -u)/com.groundhog.companion` to restart with it applied).
