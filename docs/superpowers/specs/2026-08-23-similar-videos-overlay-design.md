# Similar-video cards in the overlay

## Problem

When a verdict judges a new video against the corpus, the explanation text sometimes names the specific videos it's comparing against (see DECISIONS.md "Verdict tone: second person, and naming specific matched videos") - but that's prose buried in a paragraph, not something you can click through to. There's no way to jump straight to the video Groundhog says you've already effectively seen.

## Goal

When a corpus match is genuinely close (not just "broadly the same topic"), show it in the overlay as a clickable card - thumbnail, title, creator - so the comparison is something you can act on, not just read about.

## Threshold: grounded in real data, not a guess

`corpus.query_similar` already returns each match's `distance` - sqlite-vec's L2 distance over the corpus's normalized embeddings (0 = identical, larger = less similar). Before picking a cutoff, this was checked against the real corpus (4,868 videos) rather than assumed:

- A random anchor with no true near-duplicate in the corpus: nearest neighbor sits at distance 0.86, with a sharp jump straight from 0.0 (self-match).
- Two videos from the same cricket Test match series, different days ("Ashes Test | Day One" vs "Day Two"): distance 0.72.
- Two videos covering the same recurring news story, same sensationalist format, different day ("BREAKING: Trump issues CRAZY ANNOUNCEMENT about Epstein" vs. "BREAKING: EXPLOSIVE Epstein update SURGES into news"): distance 0.71.
- A second same-story pair in that cluster: distance 0.71.

Consistent finding across two unrelated topic domains: genuinely recurring/same-story content clusters at **0.69-0.75**, while merely-related content (same general subject, different specifics) starts around 0.85+. There's no sharp near-zero cluster of true duplicates - this embedding model doesn't produce one for real watch history, since no two videos have identical transcripts. The gradient is continuous, not bimodal.

**Threshold: distance <= 0.78** - a little above the observed recurring-topic cluster (0.69-0.75) to avoid missing genuine matches right at the edge, comfortably below the loosely-related tier (0.85+). Defined as a single named constant (`SIMILAR_VIDEO_DISTANCE_THRESHOLD` in `companion/verdict.py` or `verdict_pipeline.py`), not inlined, since this is a judgment call within a continuous range that may need adjusting after living with it - not a value with a mathematically sharp justification.

## Design

### Backend (`companion/verdict_pipeline.py`, `companion/verdict.py`)

- After `corpus.query_similar` returns its matches (already sorted closest-first), filter to `distance <= SIMILAR_VIDEO_DISTANCE_THRESHOLD`, take the closest 3, and attach them to the `Verdict` result as a new field:
  ```python
  similar_videos: list[SimilarVideo]  # closest first, max 3, empty list if none qualify

  class SimilarVideo(TypedDict):
      video_id: str
      title: str
      creator: str
      watched_at: str
  ```
- No new storage: `video_id`, `title`, `creator`, `watched_at` all already live in the `videos` table via `CorpusMatch`.
- This is independent of the novelty/execution/depth scores Gemini returns - it's computed directly from the vector distance, before the Gemini call, not from anything the model judges. A video can have a close corpus match by distance while still scoring reasonably on novelty (e.g. genuinely deeper coverage of the same topic) - the card shows "here's what's close," not "this is why novelty is low."
- Scope: `/verdict` only. The "already watched" panel doesn't get this - there's no new-vs-history comparison happening there to show a match for.

### Extension (`extension/overlay.js`)

- New section, rendered only when `similar_videos` is non-empty, placed after the existing explanation/scores in the verdict view: a small uppercase label ("Very similar to") followed by one row per match.
- Each row: a wide thumbnail (matches the mockup's Option A card layout - approved over a no-thumbnail text link and a compact-list variant), title, creator + watched date. The whole row is a link.
- Thumbnail source: `https://i.ytimg.com/vi/{video_id}/hqdefault.jpg` - YouTube's public, stable thumbnail convention. No new API call, no backend involvement; derived client-side from `video_id` alone.
- Link target: `https://www.youtube.com/watch?v={video_id}`, opened in a new tab (`target="_blank"`) - the overlay itself shouldn't navigate away from the video already open.
- Cap of 3 enforced server-side (see above) - the extension just renders whatever array it receives, no client-side truncation logic to duplicate.

## Testing

- `companion/test_verdict.py` (or `test_verdict_pipeline.py`): given a set of mocked corpus matches with known distances, `get_verdict`/`run_verdict_pipeline` returns `similar_videos` containing only the ones at or below the threshold, capped at 3, closest-first. Covers: zero qualifying matches (empty list), fewer than 3 qualifying, more than 3 qualifying (cap enforced), and matches present but none within threshold.
- No test seam for the extension's DOM rendering of the new cards - consistent with the rest of `overlay.js`, which isn't unit-tested for DOM output (see `overlay.test.js`'s docstring: DOM-heavy code here is verified manually, not in `node:test`).

## Out of scope

- Any change to how the verdict's explanation prose names matched videos - this is a separate, additive UI surface, not a replacement for the existing text.
- Applying this to the "already watched" panel.
- Fetching real thumbnail data from an API - the public URL convention is sufficient and adds no new dependency or failure mode.
