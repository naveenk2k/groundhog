/**
 * Shared video-ID extraction logic, used by both content.js (content-script
 * context) and background.js (service-worker context, via importScripts).
 * Kept in one file so the two contexts can't drift on parsing rules.
 */

/**
 * Extract the `v` query-param (the video ID) from a YouTube watch URL.
 *
 * Handles the extra query params YouTube commonly appends, e.g.
 * `&t=42s` (timestamp), `&list=...` (playlist), `&index=...`, `&pp=...`.
 * Returns null for URLs with no `v` param (e.g. bare /watch with no ID,
 * which shouldn't normally occur but is handled defensively).
 */
function extractVideoId(url) {
  try {
    const parsed = new URL(url);
    return parsed.searchParams.get("v");
  } catch (err) {
    return null;
  }
}

// Support both the content-script (plain <script>, no modules) and the
// service-worker (importScripts) contexts.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { extractVideoId };
}
