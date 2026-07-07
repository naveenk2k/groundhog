# Groundhog

Groundhog is a Chrome extension paired with a local Python companion that
checks a YouTube video against everything you've already watched, and tells
you whether it's actually saying something new before you sink time into it.
Open a video and, within a few seconds, an overlay tells you how novel it is
compared to your watch history, how well-executed it is, and how deep it
goes — plus a plain-language recommendation. It doesn't skip or hide
anything; you still decide what to watch. It only covers regular
`youtube.com/watch` pages (not Shorts or embedded players).

![Groundhog overlay showing a verdict on a real YouTube video](docs/screenshots/overlay-in-context.jpeg)

When it can't form an opinion — no transcript, the companion isn't running,
or the model call failed — it shows the same neutral "can't evaluate" badge
instead of guessing or failing silently:

![Groundhog overlay showing the "can't evaluate" state](docs/screenshots/overlay-cant-evaluate.png)

## Architecture at a glance

```
┌───────────────────────┐        localhost:8787         ┌───────────────────────────┐
│  Chrome extension      │  ── fetch() + secret header ─▶│  Python companion (bg)    │
│  - content script      │    video ID, watch events     │  - FastAPI/uvicorn server │
│  - on-page overlay UI  │ ◀──── JSON verdict ─────────  │  - yt-dlp (transcripts)   │
│  - options page        │                                │  - sentence-transformers │
└───────────────────────┘                                │    + sqlite-vec (corpus) │
                                                            │  - Gemini (verdict call)  │
                                                            └───────────────────────────┘
```

A browser extension can't write files, run Python, or keep a background
process alive, so the actual work happens in a local companion process:

- **Chrome extension** — a content script detects the video ID and tracks
  watch progress on the page; a background service worker talks to the
  companion over HTTP; an options page holds the two user-facing settings.
- **Python companion** — a FastAPI server that fetches the transcript via
  `yt-dlp`, embeds it locally with `sentence-transformers`, searches a
  `sqlite-vec` corpus of your watch history for the closest topical matches,
  and sends the new video's full transcript plus the matches' full
  transcripts to Gemini for a structured verdict (novelty, execution, depth,
  explanation, recommendation).

The two talk over authenticated `http://127.0.0.1:8787`, gated by a shared
secret so a random tab in your browser can't poke the companion.

For the full design rationale (why HTTP instead of native messaging, why
Gemini instead of Claude, why full transcripts instead of excerpts, why a
70%/5-minute watch threshold, etc.), see [`PLAN.md`](PLAN.md) and
[`DECISIONS.md`](DECISIONS.md).

## Prerequisites

- **macOS** — the companion auto-starts via a `launchd` LaunchAgent, which is
  macOS-specific.
- **Python 3**
- **Chrome**
- A free **Gemini API key** from [aistudio.google.com](https://aistudio.google.com)

## Setup

1. **Clone the repo and run the installer:**

   ```
   git clone <this-repo>
   cd groundhog
   ./install.sh
   ```

   This creates a `.venv`, installs dependencies from `requirements.txt`,
   generates a one-time shared secret at `.groundhog-secret`, and registers +
   starts a `launchd` service that runs the companion at
   `http://127.0.0.1:8787`. It's safe to re-run — it won't overwrite an
   existing secret. Check it came up with:

   ```
   curl http://127.0.0.1:8787/health
   ```

2. **Load the extension into Chrome:**
   - Go to `chrome://extensions`
   - Turn on "Developer mode" (top right)
   - Click "Load unpacked" and select the repo's `extension/` folder

3. **Paste the shared secret into the options page:**
   - Right-click the Groundhog extension icon → "Options" (or find it under
     "Manage extension" → "Extension options")
   - Copy the contents of `.groundhog-secret` from the repo root into the
     "Shared secret" field and click Save

4. **Add your Gemini API key.** The companion reads `GEMINI_API_KEY` from its
   process environment (via the `google-genai` SDK). Create a `.env` file at
   the repo root:

   ```
   GEMINI_API_KEY=your-key-here
   ```

   `.env` is gitignored. Note that the `launchd` service `install.sh`
   registers doesn't read `.env` files itself, so the key still needs to
   reach that process's actual environment — the simplest way is:

   ```
   launchctl setenv GEMINI_API_KEY your-key-here
   launchctl kickstart -k gui/$(id -u)/com.groundhog.companion
   ```

   (Run the `setenv` line once; it persists across logins. Re-run the
   `kickstart` line any time you change the key.)

5. **(Optional) Seed the corpus from your existing watch history.** Export
   `watch-history.json` from [Google Takeout](https://takeout.google.com)
   (YouTube and YouTube Music → history), then run a small smoke test first:

   ```
   python backfill.py path/to/watch-history.json --limit 20
   ```

   Once that looks right, run it again without `--limit` to process your
   full history:

   ```
   python backfill.py path/to/watch-history.json
   ```

   This is sequential and rate-limited on purpose (see
   [`DECISIONS.md`](DECISIONS.md) — "Backfill") — a history of a few thousand
   videos can take several hours. It's resumable: re-running after an
   interruption picks up where it left off instead of starting over. You can
   also add one video at a time with `python add_video.py <url-or-video-id>`.

## Day-to-day usage

Open any `youtube.com/watch` page. The overlay appears in the bottom-right
corner showing "Checking your watch history…" immediately, then fills in
with scores and a recommendation within a few seconds (transcript retrieval
alone typically takes 2-4 seconds, so the whole pipeline usually lands in
well under 10 seconds). You can collapse it to a small pill or dismiss it
entirely from its header buttons.

Once you watch a video past 70% or 5 minutes, whichever comes first, it's
automatically fetched, embedded, and added to the corpus — no manual step
required after the initial backfill.

## Configuration

The extension's options page (`chrome://extensions` → Groundhog → Options)
has two controls:

- **Shared secret** — pasted from `.groundhog-secret`, required for the
  extension to authenticate to the companion.
- **K (videos compared per check)** — a 1–10 slider for how many of your
  closest-matching watched videos (by vector search) get sent to Gemini
  alongside the new video for comparison. Higher K is a more thorough (and
  more expensive/slower) check; lower is cheaper and faster. Defaults to 5.

## Project status

This is a personal, experimental project — not production software. It works
end to end (transcript fetch → embed → vector search → Gemini verdict →
overlay), but a few things are worth knowing:

- **Transcript fetching is inherently a bit fragile.** It relies on `yt-dlp`'s
  `android_vr` client being exempt from YouTube's PO-token requirement, which
  could change at any time — see [`DECISIONS.md`](DECISIONS.md) for the
  fallback plan if that happens.
- **No verdict caching.** Every video open re-runs the full pipeline, even on
  a rewatch.
- **No spend cap.** There's no tracked ceiling on Gemini API usage yet.
- A few in-code comments still reference internal GitHub issue numbers that
  could use a pass for readability
  ([#12](https://github.com/naveenk2k/groundhog/issues/12)).

See the "Deferred, not forgotten" and "Not part of this tool" sections of
[`PLAN.md`](PLAN.md) for the fuller list of what's intentionally out of scope
for now (a manual "mark as seen" button, a model picker, Shorts support, and
so on).

## License

MIT — see [`LICENSE`](LICENSE).
