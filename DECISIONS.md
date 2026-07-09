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

## Verdict tone: second person, and naming specific matched videos

**Decision:** the system prompt addresses the viewer directly ("you", "your
watch history") rather than describing them in the third person ("the
viewer", "the user"), and explicitly instructs naming a specific matched
video by title when the comparison really centers on one, rather than only
referencing "your watch history" as an abstract whole. Each matched video's
watch date is now included in the prompt (it previously wasn't — `corpus.py`
tracked `watched_at` on every row, but `verdict.py` never actually passed it
through) so the model can reference *when* something was watched, but only
when that adds something useful — not as a mechanical timestamp on every
sentence.

**Why:** the original third-person framing read like a report about someone
else ("this topic is completely new to the viewer's watch history") rather
than a tool talking to you, and only ever gestured at "the matches" in
aggregate even though the corpus data names exactly which video is being
compared against. Verified live against the real corpus: a near-duplicate
video correctly produced "this new video is identical in content to 'AI
agents explained...' which you watched very recently on July 7, 2026",
while a genuinely unrelated video's verdict used second person throughout
but correctly didn't force a date reference where one wouldn't help.

**Gotcha caught while fixing this:** Python's `datetime.fromisoformat` only
accepts a `Z` suffix (vs. an explicit `+00:00` offset) starting in 3.11 —
but Takeout's own watch-history timestamps are `Z`-suffixed, and this
project's venv runs 3.9. Without normalizing `Z` to `+00:00` before parsing,
every backfilled video would have silently never gotten a date shown at all
(harmless — the code already degrades gracefully when a date can't be
parsed — but silently wrong for the common case, not just for malformed
edge cases).

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

## Manual "Mark as watched" as a fallback, not a universal action

**Decision:** the overlay always offers a manual "Mark as watched" button
(`b4c9197`) alongside the automatic 70%-watched threshold, so a video can
still be added to the corpus after a failed verdict check. But the button is
hidden or disabled — not just left clickable and silently failing — in the
two states where a click can never actually succeed: a setup error
(`not_configured`/`misconfigured`, issue #44 — `postVideoWatched` needs the
same missing/wrong secret that already broke the verdict check) and
`no_transcript` (issue #43 — `add_watched_video` needs the same transcript
fetch that already failed, since every corpus row requires an embedding).

**Why:** the button originally showed unconditionally in every non-terminal
phase. In both excluded states, clicking it produced a generic failure note
that faded a few seconds later, reverting to a plain "Mark as watched" as if
nothing had happened — indistinguishable from the button being broken. The
fix is state-driven (mirrors the existing `isRetryableError`/`isSetupError`
code-based pattern) rather than hiding the button for every error, since
most error codes (timeout, companion/Gemini unreachable, rate-limited) are
about the *verdict* call, not the corpus-add path, and a manual add still
has a real chance of succeeding there.

## Corpus pre-check before requesting a verdict

**Decision:** `content.js` fires a lightweight `GET /videos/{video_id}`
lookup (`corpus.find_video`, issue #41) on every fresh navigation, before
ever posting `GROUNDHOG_VIDEO_OPENED`. If the video's already in the corpus,
the overlay jumps straight to an "Already in your watch history" state — no
"Checking...", no Gemini call. The lookup fails open (`{found: false}`) on
any problem (no secret yet, companion unreachable, non-2xx), falling
straight through to the normal verdict flow rather than surfacing its own
error state.

**Why:** re-watching (or re-opening) an already-judged video was spending a
full embedding+similarity-search+Gemini call for a verdict that would just
be discarded — a real per-video cost multiplied by however often a video
gets revisited. The lookup itself costs nothing an ordinary corpus row
doesn't already provide (no embedding/similarity work, no Gemini). Fail-open
was chosen over fail-closed because this is purely an optimization: a lookup
outage should never block or error the actual verdict check that already
worked before this existed.

## Transcript fetch caching

**Decision:** `companion/verdict_pipeline.py` caches the last-fetched
transcript per video ID for 10 minutes (`_cached_fetch_transcript`, issue
#33), capped at 50 entries with oldest-first eviction. Both the verdict path
and the manual "Mark as watched" `add_watched_video` path share this cache.

**Why:** a verdict check and a subsequent manual "Mark as watched" click for
the same video (or a retry after a transient failure) were each paying the
full 2-4s transcript fetch independently, even though nothing about the
video changed between them. A short TTL avoids serving a stale transcript
indefinitely while still collapsing the common back-to-back case. The
failure result is cached too (not just successes) — a `no_transcript` video
won't change on a second attempt within the window, so caching that outcome
also saves the repeat fetch a manual retry or "Mark as watched" click would
otherwise trigger.

## Removing a video from watch history: hard delete, not soft

**Decision (not yet implemented, tracked as issue #42):** when a "Remove
from watch history" action is added to the already-watched overlay state,
it will issue a real `DELETE FROM videos WHERE video_id = ?` — the row
(including its embedding) is gone, not flagged unwatched-but-retained.

**Why:** `corpus.insert_video` already upserts by `video_id`, so a deleted
video re-added later (auto-threshold or manual) just gets a fresh row with
no special-casing needed for "was this soft-deleted before." A soft-delete
flag would add a permanent extra state every future corpus query has to
filter for, for a tool whose whole job is judging novelty against exactly
the rows that are actually in the corpus — a soft-deleted-but-present row
would be an easy source of subtle bugs (e.g. a similarity search
accidentally including it) for no real benefit over just removing it.
