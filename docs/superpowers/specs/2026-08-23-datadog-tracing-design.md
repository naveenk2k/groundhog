# Datadog APM tracing for the companion

## Problem

Investigating why a verdict check felt slow required temporary, one-off instrumentation (`time.monotonic()` calls plumbed through `run_verdict_pipeline`, logged as a single INFO line). That answered the question once, live, by hand. There's no lasting way to see the same breakdown - per-stage latency across transcript fetch, embedding, vector search, and the Gemini call - without repeating that manual work, and no historical view across requests.

## Goal

Replace the one-off log-line instrumentation with real, persistent tracing: every `/verdict` and `/videos/watched` request produces a trace showing time spent in each pipeline stage, viewable as a waterfall in a hosted dashboard, with history retained over time.

## Why Datadog

Chosen over a self-hosted Prometheus+Grafana stack (extra local processes, extra maintenance for a single-user tool) and over Google Cloud Monitoring (metrics-only; no native per-request waterfall view). Datadog APM is purpose-built for exactly this question - "which stage of this one request took the time" - via per-request trace waterfalls, and is free for 2 years through the GitHub Student Developer Pack (Pro tier, 10 hosts), independent of ongoing student-status re-verification.

## Architecture

```
companion (FastAPI + ddtrace) --spans--> Datadog Agent (local, launchd) --> Datadog Cloud (dashboard)
```

The companion never talks to Datadog's cloud directly. It sends spans to a Datadog Agent running locally (installed via Homebrew, registered as a `launchd` service - the same pattern already used for the companion itself), which batches and forwards them. This keeps per-request overhead to a local network hop instead of a live call to the internet on every video check.

## Components

### 1. `companion/tracing.py` (new)

- **Correction from the original draft of this spec**: the Datadog API key belongs to the *Agent's* own config (`/opt/datadog-agent/etc/datadog.yaml`'s `api_key` field), not to the companion process. `ddtrace` in the app only needs to know where the local Agent is (`DD_AGENT_HOST` / `DD_TRACE_AGENT_PORT`, both already defaulting to `localhost:8126`) - no API key ever touches this repo or its `.env`.
- `configure_tracing()`: called once at companion startup (`app.py`). Reads `GROUNDHOG_TRACING_ENABLED` (same on/off convention as `GROUNDHOG_DEBUG` in `companion/config.py`). If not set/truthy, sets `tracer.enabled = False` on the global `ddtrace.trace.tracer` and returns - no network calls. If enabled, sets `tracer.enabled = True`, then does a one-time reachability check against the local Agent's own `http://localhost:8126/info` endpoint (short timeout). If that check fails for any reason (Agent not installed, not running, wrong port, network error), logs a `logger.warning(...)` naming the failure - a visible callout in `.logs/companion.error.log`, not silence, and not buried in ddtrace's own internal debug logging. The tracer stays enabled either way (the Agent might come up after the companion does); the warning is purely informational.
- No `get_tracer()` wrapper needed: call sites import the global `from ddtrace.trace import tracer` directly and call `tracer.trace(...)` unconditionally - `tracer.enabled = False` makes every span a real no-op object (confirmed against the installed `ddtrace==4.13.1`), so nothing needs to branch on whether tracing is active.

### 2. `verdict_pipeline.py` changes

- The existing `time.monotonic()` timing block and its `logger.info("verdict pipeline timing...")` line are removed.
- `run_verdict_pipeline`'s four stages are each wrapped in a `tracer.trace(...)` span, named `transcript_fetch`, `embed`, `vector_search`, and `gemini_call`.
- `add_watched_video`'s two stages get the same treatment, named `transcript_fetch` and `corpus_insert`.

### 3. `app.py` changes

- Calls `tracing.configure_tracing()` once at import/startup time, alongside the existing `logging.basicConfig` setup.
- FastAPI and `httpx` get ddtrace's automatic instrumentation via `ddtrace.patch_all()` at startup - request-level timing for every route comes free, without hand-written spans.

### 4. Local Datadog Agent

- Installed via Homebrew (`brew install --cask datadog-agent`), configured with the user's Datadog API key directly in the Agent's own `datadog.yaml` (`api_key: ...`, `apm_config: enabled: true`) - a one-time manual setup step outside this repo, not something `install.sh` manages.
- `install.sh` is unchanged. `GROUNDHOG_TRACING_ENABLED` is a plain env var the user sets themselves when they want tracing on, same as `GROUNDHOG_DEBUG` today - no install-time prompt.

## Testing

Real Agent connectivity can't be part of an automated test (network/external-service dependent), but the two things that can actually break - the enable/disable/warning logic, and whether spans are really being created in the right places - both have real seams:

- **`companion/test_tracing.py`**: exercises `configure_tracing()` directly.
  - `GROUNDHOG_TRACING_ENABLED` unset: `tracer.enabled` ends up `False`, no network call attempted, no warning logged.
  - `GROUNDHOG_TRACING_ENABLED=1`, Agent reachable (mocked): `tracer.enabled` is `True`, no warning.
  - `GROUNDHOG_TRACING_ENABLED=1`, Agent unreachable (mocked failure): `tracer.enabled` is still `True`, warning logged, asserted via `assertLogs`.
- **`companion/test_verdict_pipeline.py`** (new): swaps the global tracer's writer for a small hand-rolled in-memory `TraceWriter` (confirmed working against `ddtrace==4.13.1`: `ddtrace.internal.writer.TraceWriter` is the abstract base; `DummyWriter` isn't public in this version, so the test defines its own collecting subclass and assigns it to `tracer._span_aggregator.writer`), runs `run_verdict_pipeline` and `add_watched_video` against existing test fixtures/mocks, and asserts the expected span names were emitted for each stage. This catches the actual risk of this change - a stage silently not wrapped, or wrapped with a name that doesn't match the intended breakdown - without needing a live Agent or network access.

## Out of scope

- Dashboards/alerts inside Datadog itself (configured by hand in the UI once tracing is live, not part of this change).
- Tracing anything beyond `/verdict` and `/videos/watched` (the only two routes with a multi-stage pipeline worth breaking down).
- Any change to the transcript-fetch caching, embedding, or Gemini-call logic itself - this is instrumentation only.
