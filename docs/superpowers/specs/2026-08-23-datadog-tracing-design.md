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

- `configure_tracing()`: called once at companion startup (`app.py`). Checks for `DD_API_KEY` in the environment/`.env`. If absent, tracing stays disabled and the function is a no-op - no network calls, no ddtrace initialization. If present, initializes the ddtrace tracer, then does a one-time reachability check against the local Agent's own `http://localhost:8126/info` endpoint (short timeout). If that check fails for any reason (Agent not installed, not running, wrong port, network error), logs a `logger.warning(...)` naming the failure - a visible callout in `.logs/companion.error.log`, not silence, and not buried in ddtrace's own internal debug logging.
- `get_tracer()`: returns the configured ddtrace tracer if enabled, or a no-op tracer if not - callers never need to branch on whether tracing is active.

### 2. `verdict_pipeline.py` changes

- The existing `time.monotonic()` timing block and its `logger.info("verdict pipeline timing...")` line are removed.
- `run_verdict_pipeline`'s four stages are each wrapped in a `get_tracer().trace(...)` span, named `transcript_fetch`, `embed`, `vector_search`, and `gemini_call`.
- `add_watched_video`'s two stages get the same treatment, named `transcript_fetch` and `corpus_insert`.

### 3. `app.py` changes

- Calls `tracing.configure_tracing()` once at import/startup time, alongside the existing `logging.basicConfig` setup.
- FastAPI and `httpx` get ddtrace's automatic instrumentation (via `ddtrace-run` wrapping the uvicorn process, or `ddtrace.patch_all()` at startup) - request-level timing for every route comes free, without hand-written spans.

### 4. Local Datadog Agent

- Installed via Homebrew, registered as a `launchd` service, configured with the user's Datadog API key (from the GitHub Student Developer Pack).
- `install.sh` gets an optional prompt for `DD_API_KEY`, matching the existing `GEMINI_API_KEY` prompt pattern - skippable, and skipping it leaves the companion running exactly as it does today.

## Testing

Real Agent connectivity can't be part of an automated test (network/external-service dependent), but the two things that can actually break - the enable/disable/warning logic, and whether spans are really being created in the right places - both have real seams:

- **`companion/test_tracing.py`**: exercises `configure_tracing()` and `get_tracer()` directly.
  - `DD_API_KEY` unset: tracing stays disabled, no network call attempted, no warning logged.
  - `DD_API_KEY` set, Agent reachable (mocked): tracing enabled, no warning.
  - `DD_API_KEY` set, Agent unreachable (mocked failure): warning logged, asserted via `assertLogs`.
- **`companion/test_verdict_pipeline.py`** (new): swaps in ddtrace's own `Tracer()` configured with a `DummyWriter()` (an in-memory span collector ddtrace provides for exactly this purpose) in place of the real tracer, runs `run_verdict_pipeline` and `add_watched_video` against existing test fixtures/mocks, and asserts the expected span names were emitted for each stage. This catches the actual risk of this change - a stage silently not wrapped, or wrapped with a name that doesn't match the intended breakdown - without needing a live Agent or network access.

## Out of scope

- Dashboards/alerts inside Datadog itself (configured by hand in the UI once tracing is live, not part of this change).
- Tracing anything beyond `/verdict` and `/videos/watched` (the only two routes with a multi-stage pipeline worth breaking down).
- Any change to the transcript-fetch caching, embedding, or Gemini-call logic itself - this is instrumentation only.
