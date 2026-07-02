# Groundhog — design plan

## What it does

A Chrome extension that checks a YouTube video against everything you've already
watched, and tells you whether it's actually saying something new before you
sink time into it. You get a novelty/execution/depth read plus a plain-language
take. It doesn't auto-skip anything; you decide.

## Architecture

```
┌─────────────────────┐        localhost:8787        ┌──────────────────────────┐
│  Chrome Extension    │  ───── fetch() + secret ───▶ │  Python companion (bg)    │
│  - content script     │ ◀──── JSON verdict ────────  │  - FastAPI/uvicorn server │
│  - overlay/popup UI   │                              │  - sentence-transformers  │
│  - options page        │                              │    (embedding model)      │
└─────────────────────┘                              │  - sqlite-vec (corpus DB) │
                                                        │  - Claude API client      │
                                                        └──────────────────────────┘
```

Two pieces, because a browser extension can't write files, run Python, or keep a
background process alive on its own. The companion holds the corpus, runs the
embedding model, and holds the Claude API key so it never has to live in the
extension bundle.

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

Claude sees the new transcript plus the closest matches pulled from your corpus
and returns novelty, execution, and depth scores (1-10 each), a short
explanation, and a holistic recommendation. No formula — you'd need something
like 50 real examples before a weighted threshold meant anything, and "worth
watching" was never really arithmetic to begin with. You see the scores and the
reasoning and make the call yourself.

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
page first. The popup shows "checking…" and fills in as results come back — the
full pipeline (transcript, embed, vector search, Claude call) should land in
1-3 seconds.

## When it can't tell you anything

No transcript, companion not running, or the Claude call timing out all show
the same neutral "can't evaluate" badge with a one-line reason, instead of
failing silently or blocking the page. The no-transcript case should be rare —
under 5% of typical produced content — and usually means the video was uploaded
minutes ago and captions haven't finished processing, or the audio isn't in
English.

## Setup

1. Clone the repo and run `./install.sh`. It sets up the Python venv, installs
   dependencies, asks once for your Claude API key, and registers and starts
   the launchd service.
2. Load the unpacked extension into Chrome.
3. Run `python backfill.py <takeout-watch-history.json>` once to seed the corpus
   from your existing history. This takes a while on a large history and runs
   in the background.

## Day to day

You open a video. The content script grabs the transcript, the extension posts
it to the companion, the companion embeds it, searches the corpus, and calls
Claude. The popup fills in with scores and a recommendation within a couple
seconds. Once you cross the watch threshold, the video joins the corpus on its
own — no manual upkeep after the initial backfill.

## Deferred, not forgotten

A manual "mark as seen" button, verdict caching, a Homebrew tap, a feedback loop
for personalizing future scores, and per-failure-type retry logic are all
reasonable additions that just aren't needed for a working v1.

## Not part of this tool

Shorts, embedded players on other sites, and any kind of auto-pause or
auto-reject. It stays advisory because a wrong verdict that yanks control away
from you is worse than a wrong badge you can just ignore.
