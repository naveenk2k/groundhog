/**
 * Pure K-value logic (issue #9), kept free of any DOM/chrome.* API so it can
 * be unit-tested directly in Node - same pattern as video-id.js and
 * watch-tracker.js. Shared by options.js (the slider UI) and background.js
 * (reading the persisted value before building the /verdict request body),
 * so both sides agree on the same default and valid range without
 * duplicating the numbers.
 *
 * See DECISIONS.md "Claude call: prompt content and tunables" (predates the
 * Gemini swap; the reasoning still applies): K is the number of similar
 * watched videos (by vector search) sent to the LLM alongside the new
 * video's transcript. Bigger K costs more tokens per call for (hopefully)
 * better-grounded judgments; smaller K is cheaper and faster.
 */

// Matches companion/app.py's VerdictRequest.k default (5) - see app.py. Kept
// in sync manually since the extension and companion are separate deploys
// with no shared schema; changing this without also changing the companion
// default just means the client stops relying on the server default, not a
// behavior change if both happen to still be 5.
const DEFAULT_K = 5;
const MIN_K = 1;
const MAX_K = 10;

/**
 * Clamp an arbitrary value (e.g. straight off a <input type="range"> or out
 * of chrome.storage.local, which could in principle hold anything a previous
 * version wrote) to a valid integer K in [MIN_K, MAX_K]. Falls back to
 * DEFAULT_K for anything that isn't a finite number at all (missing,
 * undefined, NaN, a string that doesn't parse) rather than clamping garbage
 * into a misleading in-range value.
 */
function clampK(value) {
  if (value === null || value === undefined || value === "") {
    return DEFAULT_K;
  }
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) {
    return DEFAULT_K;
  }
  const rounded = Math.round(numeric);
  return Math.max(MIN_K, Math.min(MAX_K, rounded));
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { DEFAULT_K, MIN_K, MAX_K, clampK };
}
