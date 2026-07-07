/**
 * Video-ID extraction logic, loaded before content.js in manifest.json so
 * extractVideoId is available as a global in the content-script context.
 * background.js does not need this - it receives the already-extracted
 * video ID from content.js via chrome.runtime.sendMessage.
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
