# Groundhog — domain glossary

A living reference for the vocabulary this codebase actually uses. Kept
separate from `DECISIONS.md` (the ADR-style log of *why* specific choices
were made). This file is the "what things are called and how they relate"
reference; skills exploring this repo should use these terms rather than
drifting to synonyms.

## The two halves

- **extension** — the Manifest V3 browser extension under `extension/`,
  loaded unpacked as-is in both Chrome and Safari (no separate Safari build
  or Xcode conversion needed). Runs in the browser: detects the video ID on
  a YouTube watch page, tracks watch progress, renders the overlay, and
  holds the options page. Cannot run Python, hold API keys long-term, or
  keep a background process alive on its own — that's what the companion is
  for.
- **companion** — the local FastAPI/uvicorn server under `companion/`,
  listening on `127.0.0.1:8787`. Fetches transcripts, embeds them, queries
  the corpus, calls Gemini for a verdict, and owns the corpus database and
  the Gemini API key so neither has to live in the extension bundle.

## Core objects

- **video ID** — YouTube's video identifier, extracted client-side from the
  watch page URL (`video-id.js`). The unit everything else keys off.
- **transcript** — full text of a video's captions, fetched by the
  companion via `yt-dlp` (`player_client=android_vr`), not scraped from the
  DOM.
- **corpus** — the `sqlite-vec`-backed table (`corpus.db`) of previously
  watched videos: `video_id`, `title`, `creator`, `published_at`,
  `watched_at`, `transcript_text`, and an `embedding` vector (384-dim,
  `all-MiniLM-L6-v2`). The raw transcript is kept alongside the embedding so
  the corpus can be re-embedded if the embedding model ever changes.
- **watch threshold** — a video counts as "watched" (added to the corpus)
  once 70% or 5 minutes has elapsed, whichever comes first, tracked via a
  `timeupdate` listener (`watch-tracker.js`). Below this, opening a video
  doesn't pollute the corpus with things you bailed on.
- **K** — the number of nearest-neighbor corpus matches (by embedding
  similarity) pulled for a given video before Gemini judges it. An
  options-page slider (1-10), not hardcoded.
- **verdict** — the `/verdict` endpoint's response: `novelty`, `execution`,
  `depth` (1-10 each), a short `explanation`, and a holistic
  `recommendation` string. Produced by Gemini via structured/schema-
  constrained output (`companion/verdict.py`), given the new video's full
  transcript plus the full transcripts of its top-K corpus matches — not a
  formula, no weighted threshold.
- **overlay** — the on-page panel the content script injects into the
  YouTube watch page (`overlay.js` + `overlay-state.js`), rendered in a
  shadow DOM. Shows "checking…", then a verdict or a neutral "can't
  evaluate" badge. Collapsible to a corner pill; never auto-expands once
  collapsed. Distinct from a toolbar popup (MV3 discards popup DOM state on
  close, which would break the "starts checking as soon as the page loads"
  behavior).
- **shared secret** — the token in `.groundhog-secret` (generated once by
  `install.sh`), sent as the `X-Groundhog-Secret` header on every companion
  request except `/health`. Origin-gating, not user auth — stops arbitrary
  local tabs from hitting the companion's localhost port.
- **backfill** — `backfill.py`, a one-time (rerunnable) script that seeds
  the corpus from a Google Takeout `watch-history.json` export, fetching a
  transcript per unique video ID the same way the live pipeline does.

## Failure vocabulary

The overlay's error badge (`classifyOverlayError` in `overlay.js`) currently
buckets failures into: not set up (no secret configured), companion
misconfigured (no Gemini key resolvable), timed out, companion unreachable,
companion returned an error status, no transcript available, and a generic
"couldn't reach the verdict service" catch-all for Gemini-side failures.
See open issues #22/#26 for known gaps in this classification (a couple of
distinct failure modes still collapse into the same message).

## Not part of this tool

YouTube Shorts, embedded players on other sites, and any auto-pause/
auto-skip behavior. Groundhog is advisory only — it never takes control
away from the user, even when it's confident.
