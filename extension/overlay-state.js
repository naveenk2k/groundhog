/**
 * Overlay state machine (issue #8), kept free of any DOM/chrome.* API so it
 * can be unit-tested directly in Node - same pattern as watch-tracker.js and
 * video-id.js. overlay.js (the DOM-rendering half) is the only thing that
 * touches `document`/shadow DOM; this file just computes the next plain-object
 * state given the previous state and an action.
 *
 * Shape of the state object:
 *   {
 *     phase: "checking" | "verdict" | "error",
 *     data: null | <verdict object from /verdict> | <error string>,
 *     collapsed: boolean,   // true = shown as a small corner badge only
 *     dismissed: boolean,   // true = fully hidden until the next navigation
 *   }
 *
 * Lifecycle: content.js calls createOverlayState() fresh on every navigation
 * to a video it actually posts "opened" for (see content.js's
 * handleNavigation) - the overlay always starts a video afresh: not
 * collapsed, not dismissed, showing "checking...". That's a deliberate
 * simplification over trying to remember collapse/dismiss preference across
 * different videos: it keeps "appears on every watch page load" (issue #8's
 * acceptance criteria) unambiguous. Collapse/dismiss only affect the overlay
 * for the *current* video's lifetime - see toggleCollapsed/dismissOverlay.
 */

/**
 * The state used the instant a video-opened request is fired, before the
 * companion has responded.
 */
function createOverlayState() {
  return { phase: "checking", data: null, collapsed: false, dismissed: false };
}

/**
 * Apply a `/verdict` response (either a verdict object with
 * novelty/execution/depth/explanation/recommendation, or a `{ error }`
 * shape - see companion/app.py's verdict_endpoint) to the current state.
 *
 * Deliberately does not touch `collapsed` - if the user already collapsed
 * the panel to a badge while the check was running, the verdict arriving
 * must not force it back open (PLAN.md/issue #8: "no auto-popping-open
 * after being collapsed once"). It also does not touch `dismissed` for the
 * same reason: a dismiss is a per-video decision the user already made.
 */
function applyVerdictResult(state, result) {
  if (result && typeof result === "object" && typeof result.error === "string") {
    return { ...state, phase: "error", data: result.error };
  }
  return { ...state, phase: "verdict", data: result };
}

/** Flip collapsed <-> expanded. Does not affect dismissed or phase/data. */
function toggleCollapsed(state) {
  return { ...state, collapsed: !state.collapsed };
}

/** Fully hide the overlay for the current video. Reversed only by the next navigation's fresh createOverlayState(). */
function dismissOverlay(state) {
  return { ...state, dismissed: true };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    createOverlayState,
    applyVerdictResult,
    toggleCollapsed,
    dismissOverlay,
  };
}
