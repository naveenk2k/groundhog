# Groundhog — architecture decisions

Running log of decisions made while designing this project, kept separate
from `PLAN.md` (the narrative design doc) so it's easy to scan and append to.

## Extension ↔ companion authentication

**Decision:** shared-secret token, generated once by `install.sh`, pasted
manually into the extension's options page.

**Why:** the companion listens on `127.0.0.1:8787`, and any tab in the
browser — not just the extension, including a malicious page — can attempt
to `fetch()` a localhost port. The token stops the companion from acting on
requests that aren't from the extension. A `/setup` endpoint that hands the
token out automatically was considered and rejected: it either needs its own
protection (recreating the same problem) or a "only before first claim"
window that adds complexity for a single-user local tool. Manual paste is a
one-time 10-second step and has zero moving parts.

## Transcript retrieval

**Decision:** companion fetches transcripts itself via `yt-dlp`
(`player_client=android_vr`), given just a video ID. No DOM scraping in the
content script.

**Why:** live-tested plain InnerTube calls (`web`, `ios`, `android` clients)
against the current YouTube API — all blocked by a PO-token wall or outright
400s. DOM-scraping the transcript panel was the fallback until testing found
`android_vr` currently exempt from PO-token requirements and working cleanly.
yt-dlp is pinned as a dependency (not hand-rolled HTTP) so PO-token/client
exemption churn gets picked up via yt-dlp upgrades instead of re-discovered.
Fallback if `android_vr` stops working: `bgutil-ytdlp-pot-provider` (local
sidecar that solves the real PO-token challenge).

**Known cost:** transcript fetch is 2-4s (three sequential HTTPS round trips
to different hosts) regardless of CLI vs Python API. This pushed the
"1-3 second" pipeline latency target in `PLAN.md` to "well under 10 seconds."

## Corpus schema

**Decision:** store `video_id`, `title`, `creator`, `watched_at`, raw
`transcript_text`, and `embedding` per row — not just the embedding.

**Why:** raw text is small (tens of KB/video) and lets the corpus be
re-embedded later if the embedding model changes, without re-fetching every
transcript. `creator` was added after the schema first shipped: it lets
Claude distinguish "the same channel revisiting its own topic" from "several
different creators independently covering the same ground" — different
signals for judging novelty that title alone can't carry, since titles
collide across channels anyway. Cheap to add (already available from both
Takeout's `subtitles[].name` and yt-dlp's `uploader`/`channel` fields);
existing corpus rows are migrated in place via `ALTER TABLE` rather than
requiring a rebuild.

## Claude call: prompt content and tunables

**Decision:** send Claude the new video's full transcript plus **full
transcripts** (not random or fixed-position excerpts) of the top-K matched
videos from vector search. K is exposed as an options-page slider rather than
a hardcoded 5-10, so it can be tuned empirically per-user.

**Why:** a random N-token slice of a transcript starts/ends mid-argument and
strips the context needed to judge substance — the one thing this tool
exists to judge. A structured excerpt (intro+conclusion) was considered as a
cheaper middle ground but rejected for v1 in favor of just testing full
transcripts first, since Haiku's context window and pricing absorb 5-10
video transcripts without issue and it removes a variable from early tuning.
K as a slider costs nothing extra to implement (it's just a vector-search
`LIMIT`) and lets cost/quality be tuned live instead of re-argued later.

**Deferred:** a hard per-day/per-month spend cap (tracked cumulative token
usage vs. a configurable ceiling, falling back to "can't evaluate" once
crossed) is wanted but explicitly deferred past v1 — not blocking initial
implementation.

## Provider swap: Gemini instead of Claude

**Decision:** `companion/verdict.py` calls Gemini (`google-genai` SDK,
`gemini-2.5-flash` default), not Claude/Anthropic, swapped wholesale rather
than kept as a multi-provider abstraction.

**Why:** Claude has no free tier — issue #5 was built and merged against
Claude first, but the very first real API call hit a "credit balance too
low" error, and the project owner didn't want to pay for a plan just to
keep developing. Gemini's free tier covers Flash models with generous rate
limits, which is enough for a call that fires once per opened video, not in
a loop. Everything else about the design carries over unchanged: full
transcripts (not excerpts) for the new video and top-K matches, creator
labels so the model can distinguish "same channel repeating itself" from
"different creators converging on a topic," and forced structured output
(Gemini's `response_schema` + `response_mime_type="application/json"`,
the equivalent of Claude's forced tool-use) so the response shape is
guaranteed rather than parsed from free text. Verified live: a real Gemini
call against a real TEDx-talk transcript and a real UK-road-trip transcript
already in the corpus correctly scored them as unrelated (novelty 10) with
grounded reasoning.

Kept as a single swapped implementation, not a provider-selectable
abstraction, because there's no second provider actually in use right now —
building a switchable interface for a hypothetical future provider would be
speculative. If a second real need for Claude (or another provider) shows
up later, that's the point to introduce an abstraction, not before.

## Backfill

**Decision:** sequential (not parallelized), skip-and-log videos with no
transcript, checkpointed for resumability, smoke-tested with a `--limit` flag
before the full overnight run.

**Why:** live watch-history export measured at 5,561 unique videos ≈ 3-6
hours sequential at 2-4s/video — an acceptable overnight job. Parallelizing
would cut wall-clock time but risks tripping YouTube's abuse detection on a
personal IP, for a workflow that only ever needs to run once. Some fraction
of older videos will have no transcript at all (deleted, privated, no
captions ever generated) — expected loss, not a bug, so the run must
tolerate and log it rather than fail outright.
