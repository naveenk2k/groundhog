/**
 * Overlay state machine, kept free of any DOM/chrome.* API so it can be
 * unit-tested directly in Node - same pattern as watch-tracker.js and
 * video-id.js. overlay.js (the DOM-rendering half) is the only thing that
 * touches `document`/shadow DOM; this file just computes the next plain-object
 * state given the previous state and an action.
 *
 * Shape of the state object:
 *   {
 *     phase: "checking" | "verdict" | "error" | "stale" | "watched",
 *     data: null | <verdict object from /verdict> | <{ message, code } for phase "error"> | <{ title, watched_at } for phase "watched">,
 *     collapsed: boolean,   // true = shown as a small corner badge only
 *     dismissed: boolean,   // true = fully hidden until the next navigation
 *     watchNote: null | { kind: "success" | "failure", message: string },
 *     alreadyWatched: boolean,  // true once this video is known to be in the corpus - see markAlreadyWatched/setAlreadyWatchedFlag
 *   }
 *
 * `code` (phase "error" only) is the machine-readable category alongside
 * `message`'s human-readable prose - see overlay.js's classifyOverlayError,
 * which prefers `code` when present rather than pattern-matching `message`.
 * `code` may be `undefined` for a result that omits it; classifyOverlayError
 * falls back to substring matching on `message` in that case.
 *
 * `watchNote` is a separate, transient signal from the corpus-add path
 * (POST /videos/watched succeeding or failing - either the automatic
 * watch-threshold add or a manual "mark as watched" click) - deliberately
 * orthogonal to `phase`/`data`, which are only ever about the *verdict*
 * check. overlay.js renders it as its own small banner alongside whatever
 * phase is showing, and clears it on a timer - see overlay.js's
 * setWatchNote. Kept as an opaque `{ kind, message }` pair here (no
 * DOM/text-shortening logic) - overlay.js is responsible for turning a raw
 * /videos/watched result into this shape, same as it already does for
 * verdict errors via classifyOverlayError.
 *
 * Lifecycle: content.js calls createOverlayState() fresh on every navigation
 * to a video it actually posts "opened" for (see content.js's
 * handleNavigation) - the overlay always starts a video afresh: not
 * collapsed, not dismissed, showing "checking...". That's a deliberate
 * simplification over trying to remember collapse/dismiss preference across
 * different videos: it keeps "appears on every watch page load" unambiguous.
 * Collapse/dismiss only affect the overlay for the *current* video's
 * lifetime - see toggleCollapsed/dismissOverlay.
 */

/**
 * The state used the instant a video-opened request is fired, before the
 * companion has responded.
 */
function createOverlayState() {
  return { phase: "checking", data: null, collapsed: false, dismissed: false, watchNote: null, alreadyWatched: false };
}

/**
 * Apply a `/verdict` response (either a verdict object with
 * novelty/execution/depth/explanation/recommendation, or a `{ error }`
 * shape - see companion/app.py's verdict_endpoint) to the current state.
 *
 * Deliberately does not touch `collapsed` - if the user already collapsed
 * the panel to a badge while the check was running, the verdict arriving
 * must not force it back open. It also does not touch `dismissed` for the
 * same reason: a dismiss is a per-video decision the user already made.
 */
function applyVerdictResult(state, result) {
  if (result && typeof result === "object" && typeof result.error === "string") {
    return { ...state, phase: "error", data: { message: result.error, code: result.code } };
  }
  return { ...state, phase: "verdict", data: result };
}

/**
 * Move to a terminal "stale" phase - the extension's context was
 * invalidated (e.g. reloaded or updated) while this tab was already open,
 * so chrome.runtime.sendMessage can no longer reach the background worker
 * at all. Nothing else in this state machine can recover from this without
 * an actual page reload - see content.js's isExtensionContextValid/
 * safeSendMessage, which is what detects this and calls in here.
 */
function markContextInvalidated(state) {
  return { ...state, phase: "stale", data: null };
}

/**
 * Move to a terminal "watched" phase - a pre-check (content.js's
 * GROUNDHOG_VIDEO_LOOKUP, fired before ever requesting a verdict) found
 * this video already in the corpus, so there's no verdict to show and no
 * point spending a Gemini call on it. `info` is the companion's lookup
 * result shape ({ title, watched_at }) or null/undefined if the caller
 * doesn't have it. Also flips `alreadyWatched`, same as
 * setAlreadyWatchedFlag - see that function's docs for why the two are
 * tracked separately from `phase`.
 */
function markAlreadyWatched(state, info) {
  return { ...state, phase: "watched", data: info || null, alreadyWatched: true };
}

/**
 * Flip `alreadyWatched` without touching `phase`/`data` - used when a
 * manual "Mark as watched" click succeeds *during* an unrelated phase
 * (checking/verdict/error), so the footer's button state can move to
 * "already watched" without discarding whatever the main body is currently
 * showing for the verdict check. Kept as its own top-level field (not
 * derived from `phase === "watched"`) precisely so it can be true
 * simultaneously with any other phase.
 */
function setAlreadyWatchedFlag(state) {
  return { ...state, alreadyWatched: true };
}

/**
 * Undo `alreadyWatched` once a "Remove from watch history" click (issue
 * #42) actually removed the video from the corpus. If `phase` was the
 * terminal "watched" state (the corpus pre-check found this video before
 * any verdict was ever requested - see markAlreadyWatched), there's no
 * verdict data left to show once it's removed, so this also moves `phase`
 * back to "checking" and clears `data` - overlay.js's caller is
 * responsible for actually firing a fresh verdict request to fill that back
 * in, since this file has no chrome.* access to do that itself. Any other
 * phase (checking/verdict/error) already reflects a real verdict that's
 * still valid regardless of corpus membership, so only the flag changes.
 */
function clearAlreadyWatched(state) {
  if (state.phase === "watched") {
    return { ...state, phase: "checking", data: null, alreadyWatched: false };
  }
  return { ...state, alreadyWatched: false };
}

/**
 * Set (or replace) the transient corpus-add banner - see the state-shape
 * comment above. Does not touch phase/data/collapsed/dismissed: this is
 * strictly a secondary signal layered on top of whatever the verdict check
 * is currently showing.
 */
function setWatchNote(state, note) {
  return { ...state, watchNote: note };
}

/** Clear the corpus-add banner, e.g. once overlay.js's auto-fade timer fires. */
function clearWatchNote(state) {
  return { ...state, watchNote: null };
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
    markContextInvalidated,
    markAlreadyWatched,
    setAlreadyWatchedFlag,
    clearAlreadyWatched,
    setWatchNote,
    clearWatchNote,
    toggleCollapsed,
    dismissOverlay,
  };
}
