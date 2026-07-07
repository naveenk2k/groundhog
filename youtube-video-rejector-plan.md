# Groundhog — design plan

## What it does

A Chrome extension that checks a YouTube video against everything you've already
watched, and tells you whether it's actually saying something new before you
sink time into it. You get a novelty/execution/depth read plus a plain-language
take. It doesn't auto-skip anything; you decide.

## Architecture

```
┌─────────────────────┐        localhost:8787        ┌──────────────────────────┐
│  Chrome Extension    │  ── fetch() + secret + ────▶ │  Python companion (bg)    │
│  - content script     │    video ID, watch events    │  - FastAPI/uvicorn server │
│  - on-page overlay UI │ ◀──── JSON verdict ────────  │  - yt-dlp (transcripts)   │
│  - options page        │                              │  - sentence-transformers  │
└─────────────────────┘                              │    (embedding model)      │
                                                        │  - sqlite-vec (corpus DB) │
                                                        │  - Claude API client      │
                                                        └──────────────────────────┘
```

Two pieces, because a browser extension can't write files, run Python, or keep a
background process alive on its own. The companion holds the corpus, runs the
embedding model, fetches transcripts, and holds the Claude API key so it never
has to live in the extension bundle.

The content script's job shrinks to just detecting the video ID and tracking
watch progress — it doesn't scrape or parse anything from the page. Transcript
retrieval turned out to need neither a documented API nor DOM scraping (see
below), so the companion fetches transcripts itself, by video ID, once the
extension tells it which video is open.

## How transcripts get fetched

Plain calls to YouTube's InnerTube API (the `player` endpoint scraper libraries
normally use) are dead as of 2024-2025 for the common client spoofs (`web`,
`ios`, `android` all failed live testing with a PO-token wall or an outright
400). DOM-scraping the transcript panel was the fallback until testing turned
up a better option: yt-dlp's `android_vr` client, which is currently exempt
from YouTube's PO-token requirement and pulled a full transcript cleanly in
live testing with no browser, no cookies, and no token exchange.

The companion depends on `yt-dlp` (Python package, not a subprocess call) and
requests captions with `player_client=android_vr`. This is inherently fragile
in one specific way: YouTube's list of PO-token-exempt clients moves over
time — what's exempt today may not be next month. yt-dlp is actively
maintained against exactly this kind of breakage, so pinning to it (rather
than hand-rolling the InnerTube call) means picking up their fixes instead of
re-discovering the workaround ourselves. If `android_vr` ever stops working,
the fallback is `bgutil-ytdlp-pot-provider`, a local sidecar that solves the
real PO-token challenge — more moving parts, kept in reserve rather than
built into v1.

Transcript fetch latency is real and not fully hideable: three sequential
HTTPS round trips (webpage, player API, then the actual caption content from a
third host) run about 2-4 seconds in live testing, regardless of whether
yt-dlp is invoked via its CLI or its Python API — the cost is network round
trips, not process overhead. Skipping the webpage round trip to save time
triggers YouTube's bot-check wall, so it isn't a safe optimization.

## Why vector search alone doesn't cut it

Vector search is fast and free. It narrows your whole watch history down to the
5-10 videos closest in topic to whatever you just opened. But it only measures
meaning, not quality — two videos on the same subject look basically identical
to an embedding whether one of them is lazy filler and the other is genuinely
rigorous.

That's the part Claude handles: read the new transcript next to the closest
matches from your history and actually judge whether it adds anything. Vector
search filters, Claude judges. It's a fairly standard retrieval-then-reason
setup, nothing exotic about it.

## The decisions, and why

Extension talks to the companion over plain HTTP (`127.0.0.1:8787`) rather than
native messaging, mostly because you can `curl` it while debugging, and that
matters more day to day than the theoretical cleanliness of native messaging.

There's a shared-secret header from the start. Any tab in your browser can
technically poke a localhost port, so the extension sends a token generated at
install time and the companion drops anything that doesn't include it. Cheap to
add, closes off a real attack path.

The companion is Python, not Node. `sentence-transformers` and `sqlite-vec` are
just much better documented than their JS equivalents, and since you're learning
this stack as you build it, that matters more than keeping one language
end-to-end.

Embeddings run locally through `sentence-transformers` — something like
`all-MiniLM-L6-v2` — which does its work in milliseconds on CPU, keeps
everything on your machine, and costs nothing per call.

Storage is `sqlite-vec`: one file you can back up, query with plain SQL, no
hosted service to think about.

Auto-start is a `launchd` LaunchAgent with `RunAtLoad` and `KeepAlive`, so it
comes up at login and survives crashes without you ever touching it again after
the initial setup.

For now it ships as a private GitHub repo plus `install.sh`. A Homebrew tap
would be nicer eventually, but not worth building while the install steps are
still likely to change.

Haiku is the default scoring model, since this fires on every single video you
open and speed was the thing you cared about most. A model picker (Sonnet, etc.)
is a later addition, not something v1 needs.

It's scoped to `youtube.com/watch` pages only. Shorts and embedded players are a
different problem shape — short-form, different DOM, much higher volume — so
they're just not in scope, not merely postponed.

## Scoring

Claude sees the new video's full transcript plus the **full transcripts** of
the top-K closest matches pulled from your corpus (vector search narrows
thousands of videos down to K by topic; Claude then judges substance from the
actual text, not a summary of it) and returns novelty, execution, and depth
scores (1-10 each), a short explanation, and a holistic recommendation via
structured tool-use output, not free-text parsing. No scoring formula —
you'd need something like 50 real examples before a weighted threshold meant
anything, and "worth watching" was never really arithmetic to begin with. You
see the scores and the reasoning and make the call yourself.

K is an options-page slider, not a hardcoded number — cheap to expose since
it's just a vector-search `LIMIT`, and lets you tune the cost/quality
tradeoff empirically instead of guessing at a fixed 5-10 up front. Random or
fixed-position transcript excerpts (to cut cost) were considered and
rejected: a random slice starts and ends mid-argument, stripping exactly the
context Claude needs to judge substance.

## Corpus policy

A video only counts as seen once you've watched 70% of it or 5 minutes,
whichever comes first, tracked with a `timeupdate` listener on the player.
Logging on open would count videos you bailed on ten seconds in, which
undermines the whole point of the corpus.

No verdict caching in v1. Every open re-runs the pipeline from scratch. Since
you're actively trying not to rewatch things, the rewatch rate should stay low,
and Haiku calls are cheap enough that caching isn't worth building yet.

## When it fires

The check starts as soon as the video page loads, not when you press play,
since you tend to click straight in from a thumbnail rather than sitting on the
page first. Starting immediately also overlaps the transcript fetch's own
2-4 second network latency with whatever time you spend on the page before
watching, instead of stacking it after. The overlay shows "checking…" and
fills in as results come back — the full pipeline (transcript, embed, vector
search, Claude call) typically lands in well under 10 seconds, not the 1-3
seconds originally hoped for, since transcript retrieval alone can take 2-4
seconds.

## Overlay, not a toolbar popup

This is an on-page overlay the content script injects directly into the
YouTube watch page — a small, dismissible panel, collapsible to a corner
badge — not the browser-action toolbar popup. A toolbar popup only exists in
the DOM while open and Manifest V3 discards its state the instant it closes,
so there'd be nowhere for "checking…" to render until you click the
extension icon, contradicting the "starts as soon as the page loads" behavior
above: the check would run silently in the background with no visible
progress until you happened to click the icon. The tradeoff is intrusiveness
— it's UI drawn over YouTube's own page, so it needs to hold up against
YouTube's dark mode and mobile-width layouts rather than assuming one theme.

## When it can't tell you anything

No transcript, companion not running, or the Claude call timing out all show
the same neutral "can't evaluate" badge with a one-line reason, instead of
failing silently or blocking the page. The no-transcript case should be rare —
under 5% of typical produced content — and usually means the video was uploaded
minutes ago and captions haven't finished processing, or the audio isn't in
English.

## Corpus schema

Each corpus row stores `video_id`, `title`, `watched_at`, the raw
`transcript_text`, and the `embedding` vector — keeping the raw text (not just
the embedding) so the corpus can be re-embedded later if the embedding model
ever changes, at the cost of some extra disk space (transcripts run tens of KB
each, so this is cheap even at thousands of videos).

## Backfill

`backfill.py` reads a Takeout `watch-history.json` and fetches a transcript
per unique video ID via yt-dlp, same as the live pipeline. For a typical
history (live-tested at 5,561 unique videos across ~9.5 months) that's
roughly 3-6 hours run sequentially at 2-4s per video — an overnight job, not
something to parallelize and risk tripping YouTube's abuse detection on a
personal IP.

It has to tolerate partial failure by design: some fraction of older videos
will have no transcript at all (deleted, privated, captions never generated —
Takeout history itself already includes some entries with no working URL at
all, for videos removed since). Backfill skips and logs these rather than
failing the whole run, and checkpoints progress so a crash or restart partway
through an hours-long run doesn't mean starting over.

Before kicking off the full overnight run, do a small test run against a
handful of videos (say, 10-20) to confirm the transcript fetch, embedding, and
corpus write path all work end to end — cheap insurance against discovering a
bug only after several hours of unattended runtime.

## Setup

1. Clone the repo and run `./install.sh`. It sets up the Python venv, installs
   dependencies, asks once for your Claude API key, and registers and starts
   the launchd service.
2. Load the unpacked extension into Chrome.
3. Run `python backfill.py <takeout-watch-history.json> --limit 20` first as a
   smoke test, then run it again without `--limit` to seed the full corpus
   from watch history. The full run takes a few hours and runs in the
   background.

## Day to day

You open a video. The content script detects the video ID and posts it to the
companion, which fetches the transcript, embeds it, searches the corpus, and
calls Claude. The overlay fills in with scores and a recommendation within a
few seconds. Once you cross the watch threshold, the video joins the corpus
on its own — no manual upkeep after the initial backfill.

## Deferred, not forgotten

A manual "mark as seen" button, verdict caching, a Homebrew tap, a feedback loop
for personalizing future scores, per-failure-type retry logic, and a hard
Claude spend cap (cumulative token tracking against a configurable daily or
monthly ceiling) are all reasonable additions that just aren't needed for a
working v1.

## Not part of this tool

Shorts, embedded players on other sites, and any kind of auto-pause or
auto-reject. It stays advisory because a wrong verdict that yanks control away
from you is worse than a wrong badge you can just ignore.
