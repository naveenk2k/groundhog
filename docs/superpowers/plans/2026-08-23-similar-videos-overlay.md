# Similar-Video Cards in the Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a verdict's corpus matches include one or more videos genuinely similar to the new one (not just loosely related), surface them in the overlay as clickable cards (thumbnail, title, creator/watched-date) linking straight to that video on YouTube.

**Architecture:** `companion/verdict.py`'s `get_verdict` already receives the full list of corpus matches (each with a vector `distance`) before it ever calls Gemini. Filtering to "genuinely similar" and capping the count happens there, attached to the `Verdict` result as a new `similar_videos` field. The extension renders that field as-is - no new backend endpoint, no new extension-side computation, no new stored data (thumbnails are derived client-side from `video_id` via YouTube's public thumbnail URL convention).

**Tech Stack:** Python (`companion/verdict.py`, stdlib `TypedDict`), vanilla JS (`extension/overlay.js`, matches the file's existing DOM-construction style).

## Global Constraints

- Similarity threshold: `distance <= 0.78` (sqlite-vec L2 distance over normalized embeddings), picked from real corpus data - see `docs/superpowers/specs/2026-08-23-similar-videos-overlay-design.md` for the measurements behind this number. Defined as a named constant, never inlined.
- Cap: at most 3 similar videos shown, closest first. Enforced server-side (`companion/verdict.py`) - the extension renders whatever array it receives with no truncation logic of its own.
- This is independent of Gemini's novelty/execution/depth scores - computed directly from `CorpusMatch.distance`, never from anything the model returns.
- Scope: `/verdict` responses only. No change to the "already watched" panel.
- Thumbnail URLs: `https://i.ytimg.com/vi/{video_id}/hqdefault.jpg` (public convention, no new dependency). Link: `https://www.youtube.com/watch?v={video_id}`, opened in a new tab.
- No DOM/rendering test for the extension change - consistent with the rest of `overlay.js`, which isn't unit-tested for DOM output (see `extension/overlay.test.js`'s own docstring: this file only tests the DOM-free `classifyOverlayError`-family functions, guarded behind a `typeof window === "undefined"` early return).

---

### Task 1: Backend - compute and return `similar_videos`

**Files:**
- Modify: `companion/verdict.py`
- Test: `companion/test_verdict.py`

**Interfaces:**
- Consumes: `companion.corpus.CorpusMatch` (existing dataclass; fields used: `video_id: str`, `title: str`, `creator: str`, `watched_at: str`, `distance: float`).
- Produces: `companion.verdict.SimilarVideo` (new `TypedDict`: `video_id: str`, `title: str`, `creator: str`, `watched_at: str`); `Verdict`'s dict now also has a `similar_videos: list[SimilarVideo]` key, always present (empty list when nothing qualifies). Task 2 consumes this exact field name and shape from the JSON the `/verdict` endpoint returns (FastAPI serializes the `Verdict` dict as-is - see `companion/app.py`'s `verdict_endpoint`).

- [ ] **Step 1: Write the failing tests**

Add to `companion/test_verdict.py`. First, add this import near the top (alongside the existing `from companion import verdict` line):

```python
from companion.corpus import CorpusMatch
```

Then add these test methods to the `GetVerdictTest` class:

```python
    def test_similar_videos_only_includes_matches_within_threshold(self):
        response = MagicMock()
        response.parsed = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }
        client = _fake_client(generate_content_result=response)
        matches = [
            CorpusMatch(
                video_id="close1",
                title="Close Match",
                creator="Creator A",
                published_at="",
                watched_at="2026-01-01T00:00:00Z",
                transcript_text="t",
                distance=0.71,
            ),
            CorpusMatch(
                video_id="far1",
                title="Loosely Related",
                creator="Creator B",
                published_at="",
                watched_at="2026-01-02T00:00:00Z",
                transcript_text="t",
                distance=0.91,
            ),
        ]

        result = verdict.get_verdict(NEW_VIDEO, matches, client=client)

        self.assertEqual(
            result["similar_videos"],
            [{"video_id": "close1", "title": "Close Match", "creator": "Creator A", "watched_at": "2026-01-01T00:00:00Z"}],
        )

    def test_similar_videos_caps_at_three_closest_first(self):
        response = MagicMock()
        response.parsed = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }
        client = _fake_client(generate_content_result=response)
        # Five qualifying matches, already closest-first (matching query_similar's
        # own ORDER BY vv.distance) - only the first three should come back.
        matches = [
            CorpusMatch(
                video_id=f"v{i}",
                title=f"Match {i}",
                creator="Creator",
                published_at="",
                watched_at="2026-01-01T00:00:00Z",
                transcript_text="t",
                distance=0.70 + i * 0.01,
            )
            for i in range(5)
        ]

        result = verdict.get_verdict(NEW_VIDEO, matches, client=client)

        self.assertEqual([m["video_id"] for m in result["similar_videos"]], ["v0", "v1", "v2"])

    def test_similar_videos_empty_when_none_qualify(self):
        response = MagicMock()
        response.parsed = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }
        client = _fake_client(generate_content_result=response)
        matches = [
            CorpusMatch(
                video_id="far1",
                title="Loosely Related",
                creator="Creator",
                published_at="",
                watched_at="2026-01-01T00:00:00Z",
                transcript_text="t",
                distance=0.95,
            ),
        ]

        result = verdict.get_verdict(NEW_VIDEO, matches, client=client)

        self.assertEqual(result["similar_videos"], [])

    def test_similar_videos_empty_when_no_matches_at_all(self):
        response = MagicMock()
        response.parsed = {
            "novelty": 7,
            "execution": 8,
            "depth": 6,
            "explanation": "explanation",
            "recommendation": "watch it",
        }
        client = _fake_client(generate_content_result=response)

        result = verdict.get_verdict(NEW_VIDEO, [], client=client)

        self.assertEqual(result["similar_videos"], [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest companion.test_verdict -v`
Expected: the four new tests FAIL with `KeyError: 'similar_videos'` (the field doesn't exist yet).

- [ ] **Step 3: Add the threshold constant, `SimilarVideo` type, and filter function**

In `companion/verdict.py`, add these two constants right after the existing `_TRANSIENT_GEMINI_CODES = {429, 503}` line:

```python
# See docs/superpowers/specs/2026-08-23-similar-videos-overlay-design.md for
# how this was picked: checked against real corpus data rather than assumed
# - genuinely recurring/same-story video pairs (same match series on
# different days, same news story covered a day apart) clustered at
# 0.69-0.75 distance, while merely-related videos started around 0.85+.
# Set a little above that cluster to avoid missing genuine matches right at
# the edge.
SIMILAR_VIDEO_DISTANCE_THRESHOLD = 0.78

# Keeps the overlay's "Very similar to" section compact even when more than
# 3 corpus matches clear the threshold.
_MAX_SIMILAR_VIDEOS = 3
```

Then, in the `Verdict` TypedDict definition, add `similar_videos: list[SimilarVideo]` right after the existing `creator: str` line (the one with the "Echoed back from the NewVideo..." comment above it) - so the class ends with both `title`/`creator` and the new field.

Add a new `SimilarVideo` TypedDict definition directly above the `Verdict` class it's used by:

```python
class SimilarVideo(TypedDict):
    """One corpus match close enough to show as a clickable card in the
    overlay - see SIMILAR_VIDEO_DISTANCE_THRESHOLD above."""

    video_id: str
    title: str
    creator: str
    watched_at: str
```

Then add this function directly above `get_verdict`:

```python
def _similar_videos(matches: list[CorpusMatch]) -> list[SimilarVideo]:
    """Corpus matches close enough to show as clickable cards in the
    overlay, closest first, capped at _MAX_SIMILAR_VIDEOS.

    Independent of Gemini's novelty/execution/depth judgment - this is
    computed directly from the vector distance, before the Gemini call
    happens, not from anything the model returns.
    """
    qualifying = [m for m in matches if m.distance <= SIMILAR_VIDEO_DISTANCE_THRESHOLD]
    return [
        {
            "video_id": m.video_id,
            "title": m.title,
            "creator": m.creator,
            "watched_at": m.watched_at,
        }
        for m in qualifying[:_MAX_SIMILAR_VIDEOS]
    ]
```

- [ ] **Step 4: Wire it into `get_verdict`'s return value**

In `get_verdict`, change the final `return` statement from:

```python
    data = response.parsed
    return {
        "novelty": data["novelty"],
        "execution": data["execution"],
        "depth": data["depth"],
        "explanation": data["explanation"],
        "recommendation": data["recommendation"],
        "title": new_video.title,
        "creator": new_video.creator,
    }
```

to:

```python
    data = response.parsed
    return {
        "novelty": data["novelty"],
        "execution": data["execution"],
        "depth": data["depth"],
        "explanation": data["explanation"],
        "recommendation": data["recommendation"],
        "title": new_video.title,
        "creator": new_video.creator,
        "similar_videos": _similar_videos(matches),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest companion.test_verdict -v`
Expected: all tests in the file PASS, including the four new ones.

- [ ] **Step 6: Run the full companion test suite**

Run: `.venv/bin/python -m unittest discover -s . -p "test_*.py"`
Expected: all tests pass (70 existing + 4 new = 74).

- [ ] **Step 7: Commit**

```bash
git add companion/verdict.py companion/test_verdict.py
git commit -m "Return similar_videos in the verdict result for genuinely close corpus matches"
```

---

### Task 2: Extension - render similar-video cards in the overlay

**Files:**
- Modify: `extension/overlay.js`

**Interfaces:**
- Consumes: `verdict.similar_videos` - an array of `{video_id, title, creator, watched_at}` objects (from Task 1), already filtered and capped by the companion. May be absent/empty on older cached responses or non-matching verdicts - render nothing in that case.

- [ ] **Step 1: Add the CSS**

In `extension/overlay.js`, find the existing `.ghog-explanation` CSS rule:

```css
    .ghog-explanation {
      font-size: 11.5px;
      color: var(--ghog-fg-secondary);
      line-height: 1.4;
    }
```

Add this new CSS directly after it (same indentation, inside the same template-literal style block):

```css
    /* "Very similar to" cards - one per qualifying corpus match (see
     * companion/verdict.py's SIMILAR_VIDEO_DISTANCE_THRESHOLD). Same wide-
     * thumbnail row style validated in the design mockup. */
    .ghog-similar-label {
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--ghog-fg-secondary);
      margin: 10px 0 6px;
    }

    .ghog-similar-row {
      display: flex;
      gap: 8px;
      border: 1px solid var(--ghog-border);
      border-radius: 8px;
      padding: 6px;
      margin-bottom: 6px;
      text-decoration: none;
      color: inherit;
    }
    .ghog-similar-row:last-child {
      margin-bottom: 0;
    }
    .ghog-similar-row img {
      width: 64px;
      height: 36px;
      object-fit: cover;
      border-radius: 4px;
      flex-shrink: 0;
    }
    .ghog-similar-meta {
      min-width: 0;
    }
    .ghog-similar-title {
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      color: var(--ghog-fg);
    }
    .ghog-similar-sub {
      font-size: 11px;
      color: var(--ghog-fg-secondary);
    }
```

- [ ] **Step 2: Add the rendering logic**

Find this existing block (the last thing rendered in the verdict phase, right before that function's closing brace):

```javascript
    if (verdict.explanation) {
      const explanation = document.createElement("div");
      explanation.className = "ghog-explanation";
      explanation.textContent = verdict.explanation;
      body.appendChild(explanation);
    }
  }
```

Change it to (adding the new block between the explanation and the closing brace):

```javascript
    if (verdict.explanation) {
      const explanation = document.createElement("div");
      explanation.className = "ghog-explanation";
      explanation.textContent = verdict.explanation;
      body.appendChild(explanation);
    }

    // similar_videos: corpus matches close enough to link to directly (see
    // companion/verdict.py's SIMILAR_VIDEO_DISTANCE_THRESHOLD) - absent or
    // empty on older cached responses or verdicts with no close match, in
    // which case nothing renders here.
    if (Array.isArray(verdict.similar_videos) && verdict.similar_videos.length > 0) {
      const label = document.createElement("div");
      label.className = "ghog-similar-label";
      label.textContent = "Very similar to";
      body.appendChild(label);

      verdict.similar_videos.forEach((similar) => {
        const row = document.createElement("a");
        row.className = "ghog-similar-row";
        row.href = "https://www.youtube.com/watch?v=" + encodeURIComponent(similar.video_id);
        row.target = "_blank";
        row.rel = "noopener noreferrer";

        const thumb = document.createElement("img");
        thumb.src = "https://i.ytimg.com/vi/" + encodeURIComponent(similar.video_id) + "/hqdefault.jpg";
        thumb.alt = "";
        row.appendChild(thumb);

        const meta = document.createElement("div");
        meta.className = "ghog-similar-meta";

        const titleEl = document.createElement("div");
        titleEl.className = "ghog-similar-title";
        titleEl.textContent = similar.title || similar.video_id;
        meta.appendChild(titleEl);

        const sub = document.createElement("div");
        sub.className = "ghog-similar-sub";
        const parsedDate = similar.watched_at ? new Date(similar.watched_at) : null;
        const dateText =
          parsedDate && !isNaN(parsedDate)
            ? parsedDate.toLocaleDateString(undefined, { month: "short", day: "numeric" })
            : null;
        sub.textContent =
          similar.creator && dateText
            ? similar.creator + " · " + dateText
            : similar.creator || dateText || "";
        meta.appendChild(sub);

        row.appendChild(meta);
        body.appendChild(row);
      });
    }
  }
```

- [ ] **Step 3: Run the extension test suite**

Run: `cd extension && npm test`
Expected: all existing tests still pass (this change adds no new test file, per the Global Constraints note on why - confirm nothing broke).

- [ ] **Step 4: Manual smoke test**

Load the extension unpacked (or reload if already loaded - see the Safari/Chrome reload guidance in this repo's own history if using Safari, i.e. reload then open a fresh tab). Open a video you know is a near-duplicate of something in your watch history (e.g. two videos from the same news story or match series a day apart, per the same kind of pair used to calibrate the threshold in the design spec). Confirm:
- The "Very similar to" section appears with a thumbnail, title, and creator/date for the matching video.
- Clicking a card opens that video in a new tab.
- Opening a video with no close match shows no such section (no empty label, no broken layout).

- [ ] **Step 5: Commit**

```bash
git add extension/overlay.js
git commit -m "Show similar-video cards in the overlay for genuinely close corpus matches"
```
