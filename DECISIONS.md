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

**Decision:** store `video_id`, `title`, `watched_at`, raw `transcript_text`,
and `embedding` per row — not just the embedding.

**Why:** raw text is small (tens of KB/video) and lets the corpus be
re-embedded later if the embedding model changes, without re-fetching every
transcript.

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

**Deferred:** a hard per-day/per-month Claude spend cap (tracked cumulative
token usage vs. a configurable ceiling, falling back to "can't evaluate" once
crossed) is wanted but explicitly deferred past v1 — not blocking initial
implementation.

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
