/**
 * Pure decision logic for background.js's requestVerdict(), kept free of any
 * chrome.* / fetch / AbortController calls so it can be unit-tested directly
 * in Node - same pattern as options-k.js/overlay-state.js/watch-tracker.js.
 *
 * requestVerdict() itself stays a thin wrapper: it does the actual fetch(),
 * builds the AbortController/timeout, and logs to the console, then hands
 * the response-or-error off to the functions below to decide what
 * `{ error }` shape (if any) the overlay should end up rendering.
 */

// Client-side safety net: the companion's own Gemini call is bounded at
// ~45s (companion/verdict.py's DEFAULT_TIMEOUT_SECONDS), but that only
// helps if the companion process itself is alive to respond at all. If it
// hangs (stuck request, wedged event loop) or never gets to answer, nothing
// else here would ever stop "checking..." from spinning forever. 60s
// comfortably clears the companion's own 45s budget plus network/queueing
// overhead.
//
// Lives here (rather than background.js) so classifyVerdictTimeout below
// can build its error string without reaching into background.js's module
// scope; background.js imports this same constant so the two stay in sync.
const VERDICT_TIMEOUT_MS = 60000;

/**
 * Given the `Response` from a `fetch()` call to the companion's /verdict
 * endpoint, decide whether it's an error the overlay should show.
 *
 * Returns `{ error: "..." }` if `response.ok` is false, or `null` if the
 * response is fine to use as-is (the caller should then `await
 * response.json()` itself - that's I/O, not decidable logic, so it doesn't
 * belong in this pure function).
 */
function classifyVerdictResponse(response) {
  if (!response.ok) {
    return { error: "companion responded with status " + response.status };
  }
  return null;
}

/**
 * Given the error thrown out of a `fetch()` call (or from awaiting
 * `response.json()`), decide what `{ error }` shape the overlay should show.
 * Distinguishes the AbortController-driven client-side timeout from every
 * other failure (companion not running, network error, etc.) so
 * overlay.js's classifyOverlayError can give each its own one-line reason.
 */
function classifyVerdictError(err) {
  if (err && err.name === "AbortError") {
    return { error: "companion request timed out after " + (VERDICT_TIMEOUT_MS / 1000) + "s" };
  }
  return { error: "companion request failed" };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { VERDICT_TIMEOUT_MS, classifyVerdictResponse, classifyVerdictError };
}
