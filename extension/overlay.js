/**
 * On-page overlay: the actual DOM/rendering half. Uses the pure state
 * machine in overlay-state.js (createOverlayState / applyVerdictResult /
 * setWatchNote / clearWatchNote / toggleCollapsed / dismissOverlay) for all
 * state transitions, and only concerns itself with building/injecting/
 * re-rendering the panel.
 *
 * Loaded before content.js in manifest.json (after overlay-state.js), so
 * `GroundhogOverlay` is available as a global content.js can call into:
 *
 *   GroundhogOverlay.reset(videoId)               - fresh "checking..." panel
 *   GroundhogOverlay.setResult(videoId, r)        - fill in verdict or error
 *   GroundhogOverlay.setWatchedResult(videoId, r) - corpus-add note (see below)
 *
 * Design notes:
 * - Shadow DOM keeps all CSS below scoped to the overlay - nothing here can
 *   leak into YouTube's own styles, and nothing YouTube does can reach in.
 * - Font stack, corner radius, shadow weight, and light/dark colors are
 *   chosen to match YouTube's own chrome (see the CSS below for the exact
 *   values and why) rather than reading as a bolted-on third-party badge.
 * - Fixed bottom-right corner placement, collapsible to a small pill. Never
 *   auto-expands once collapsed (see overlay-state.js's applyVerdictResult).
 */

/**
 * Machine-readable `code` -> the same one-line reasons the substring
 * matching below produces, for every category background.js/verdict.py
 * currently attach one to. Kept as a flat lookup rather than
 * folded into the substring-matching chain so it's obvious at a glance
 * which codes are recognized, and so a typo'd/unrecognized code cleanly
 * falls through to the substring-matching fallback instead of silently
 * mapping to nothing.
 */
const _CODE_TO_REASON = {
  no_transcript: "No transcript available for this video.",
  timeout: "Groundhog took too long to respond.",
  not_configured: "Groundhog isn't set up yet.",
  misconfigured: "Groundhog isn't configured correctly.",
  companion_unreachable: "Couldn't reach the Groundhog companion.",
  companion_rate_limited: "Groundhog is being rate-limited - try again shortly.",
  companion_error_status: "Groundhog companion returned an error.",
  gemini_busy: "Gemini is busy right now - try again in a bit.",
  verdict_service_unreachable: "Couldn't reach the verdict service.",
  unexpected_verdict_response: "Groundhog got an unexpected response from the verdict service.",
};

/**
 * Turn a raw error string (from companion/app.py's `{error: "..."}`,
 * companion/verdict.py's Gemini failures, or background.js's own
 * companion-unreachable/timeout messages) into a short, calm, one-line
 * reason for the "can't evaluate" badge.
 *
 * Prefers `code` (a machine-readable category attached alongside `raw`'s
 * human-readable prose - see `_CODE_TO_REASON` above) when it's present and
 * recognized. Falls back to pattern-matching recognizable substrings in
 * `raw` otherwise - either because `code` is missing (an older code path)
 * or unrecognized (defensive: a future code this version doesn't know
 * about yet shouldn't produce a blank/broken badge).
 * The known failure sources each produce error text of a wildly different
 * shape and verbosity, and the fallback only needs to degrade gracefully
 * for anything unrecognized - not enumerate every possible internal
 * exception string. Kept outside the IIFE below (and exported via
 * module.exports) so it's plain, DOM-free, testable logic - same pattern as
 * overlay-state.js/video-id.js/watch-tracker.js.
 */
function classifyOverlayError(raw, code) {
  if (typeof code === "string" && Object.prototype.hasOwnProperty.call(_CODE_TO_REASON, code)) {
    return _CODE_TO_REASON[code];
  }

  if (typeof raw !== "string" || !raw.trim()) {
    return "Groundhog couldn't evaluate this video.";
  }
  const msg = raw.toLowerCase();

  // companion/app.py: `no transcript available: <reason from transcript.py>`
  // - reason itself can be a raw yt-dlp exception string, but the prefix is
  // always this, so it's a safe, generic match regardless of what follows.
  if (msg.includes("no transcript available")) {
    return "No transcript available for this video.";
  }

  // background.js's own client-side AbortController timeout ("...timed out
  // after Xs") or companion/verdict.py's own clean timeout message ("took
  // too long to respond") - distinct from "unreachable" since a timeout got
  // this far, so the companion *is* reachable, it just didn't finish in
  // time.
  if (msg.includes("timed out") || msg.includes("timeout") || msg.includes("took too long")) {
    return "Groundhog took too long to respond.";
  }

  // background.js's NOT_CONFIGURED_MESSAGE: no secret has been pasted into
  // the options page yet - distinct one-liner pointing at setup rather than
  // a failure.
  if (msg.includes("isn't set up") || msg.includes("no secret configured")) {
    return "Groundhog isn't set up yet.";
  }

  // companion/verdict.py: the Gemini client itself couldn't be constructed
  // (e.g. no GEMINI_API_KEY resolvable at all) - a setup problem, not a
  // transient failure, but still surfaced through the same neutral badge.
  if (msg.includes("isn't configured correctly")) {
    return "Groundhog isn't configured correctly.";
  }

  // background.js's requestVerdict() catch block: the fetch to the
  // companion failed before any HTTP response came back - browsers surface
  // this as a generic "Failed to fetch"/NetworkError with no detail on why
  // (not running, wrong port, or blocked for some other reason, e.g. a CORS
  // preflight rejection), so this can't claim a specific diagnosis like
  // "isn't running" - that's more than the extension actually knows.
  if (
    msg.includes("companion request failed") ||
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    msg.includes("connection refused") ||
    msg.includes("econnrefused")
  ) {
    return "Couldn't reach the Groundhog companion.";
  }

  if (msg.includes("companion responded with status")) {
    // background.js's requestVerdict(): "companion responded with status N".
    // 429 (rate-limited) is a transient, retry-worthy state - a meaningfully
    // different story from a generic 5xx, which suggests something's
    // actually broken.
    const statusMatch = msg.match(/status (\d+)/);
    const status = statusMatch ? parseInt(statusMatch[1], 10) : null;
    if (status === 429) {
      return "Groundhog is being rate-limited - try again shortly.";
    }
    return "Groundhog companion returned an error.";
  }

  // companion/verdict.py: Gemini's own transient overload/rate-limit signal
  // (429/503 - confirmed live: "This model is currently experiencing high
  // demand") - distinct from genuine unreachability, since Gemini responded
  // just fine and told us it's busy. Checked before the generic "gemini"
  // substring match below so it doesn't get swallowed by that bucket.
  if (msg.includes("is busy right now")) {
    return "Gemini is busy right now - try again in a bit.";
  }

  // companion/verdict.py: Gemini responded successfully but the response
  // didn't parse against the schema - a companion/prompt/schema bug, not a
  // connectivity problem, so it needs its own distinct message rather than
  // being absorbed into the generic "couldn't reach" bucket below.
  if (msg.includes("unexpected response from the verdict service")) {
    return "Groundhog got an unexpected response from the verdict service.";
  }

  // companion/verdict.py's already-clean Gemini-failure message (client/
  // server/generic API errors all return this same text directly - see
  // verdict.py) - matched here as a direct pass-through, plus the legacy
  // substring in case an older/unexpected message still mentions Gemini by
  // name.
  if (msg.includes("couldn't reach the verdict service") || msg.includes("gemini")) {
    return "Couldn't reach the verdict service.";
  }

  // Generic fallback for anything unrecognized - still calm and short,
  // never the raw exception text.
  return "Groundhog couldn't evaluate this video.";
}

/**
 * Error codes where retrying the same video is pointless, so the "can't
 * evaluate" badge shouldn't offer a "Retry" button for them:
 *
 * - no_transcript: the video itself has no transcript - that won't change
 *   on a second attempt.
 * - not_configured / misconfigured: setup problems - "Open settings" (see
 *   isSetupError below) is the useful action, not a retry.
 * - unexpected_verdict_response: a schema mismatch between the companion
 *   and Gemini's response - a companion/prompt bug, not the kind of
 *   transient failure a retry fixes.
 *
 * Every other known code (timeouts, companion/Gemini unreachability or
 * rate-limiting) is transient enough that a second attempt is worth
 * offering.
 */
const _NON_RETRYABLE_CODES = new Set([
  "no_transcript",
  "not_configured",
  "misconfigured",
  "unexpected_verdict_response",
]);

/**
 * True if the "can't evaluate" badge should offer a "Retry" button for this
 * error. Prefers `code`, same precedence as classifyOverlayError/
 * isSetupError. Falls back to "retryable unless it's a setup error" for a
 * missing/unrecognized code, rather than hiding retry by default - an older
 * or unrecognized error shape shouldn't silently lose a legitimately useful
 * action.
 */
function isRetryableError(raw, code) {
  if (typeof code === "string" && Object.prototype.hasOwnProperty.call(_CODE_TO_REASON, code)) {
    return !_NON_RETRYABLE_CODES.has(code);
  }
  return !isSetupError(raw, code);
}

/**
 * True if this error is a setup problem - missing secret or misconfigured
 * Gemini key - the only two categories "open the options page" actually
 * fixes. Every other error (companion unreachable, timeout, no transcript,
 * Gemini failure) has no business showing a settings link, since opening
 * options wouldn't help there.
 *
 * Prefers `code` (see classifyOverlayError's _CODE_TO_REASON above) and
 * only falls back to substring-matching `raw` when `code` is missing or
 * unrecognized, same precedence classifyOverlayError itself uses.
 */
function isSetupError(raw, code) {
  if (code === "not_configured" || code === "misconfigured") {
    return true;
  }
  if (typeof raw !== "string") {
    return false;
  }
  const msg = raw.toLowerCase();
  return (
    msg.includes("isn't set up") ||
    msg.includes("no secret configured") ||
    msg.includes("isn't configured correctly")
  );
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { classifyOverlayError, isSetupError, isRetryableError };
}

(function () {
  // No `window`/DOM at all - this file is being `require()`d from Node
  // (e.g. to unit-test the DOM-free classifyOverlayError above), not
  // running in a content-script/browser context. Nothing below this point
  // is meaningful without a document, so just skip installing the overlay.
  if (typeof window === "undefined") {
    return;
  }

  // Guard against double-injection: YouTube's SPA navigation re-runs
  // handleNavigation in content.js repeatedly, but this IIFE (and thus the
  // shadow host) should only ever be created once per content-script
  // lifetime. GroundhogOverlay itself persists across navigations; only its
  // internal per-video `state` gets reset.
  if (window.__groundhogOverlayInstalled) {
    return;
  }
  window.__groundhogOverlayInstalled = true;

  const HOST_ID = "groundhog-overlay-host";

  let state = createOverlayState();
  let currentVideoId = null;
  let shadowRoot = null;
  let els = null; // cached references into the shadow DOM, set up in ensureDom()
  let watchNoteTimer = null; // pending auto-fade for state.watchNote, see setWatchedResult

  // How long the corpus-add note (state.watchNote) stays visible before
  // auto-fading - long enough to read a short sentence, short enough not to
  // linger like a permanent status line.
  const WATCH_NOTE_TIMEOUT_MS = 4000;

  /**
   * Turn a raw POST /videos/watched result (companion/app.py's
   * `{ added: true, title }` / `{ added: false, reason }`, or one of
   * background.js's own synthesized reasons for a request that never
   * reached the companion at all - "not_configured", "companion_error_status",
   * "companion_unreachable") into the short, calm `{ kind, message }` shape
   * overlay-state.js's watchNote holds. Deliberately doesn't surface the
   * companion's raw transcript-failure reason (can be verbose yt-dlp text) -
   * same "short and calm, never raw exception text" rule classifyOverlayError
   * follows above.
   */
  function describeWatchedResult(result) {
    if (result && result.added) {
      return { kind: "success", message: "Added to your watch history." };
    }
    if (result && result.reason === "not_configured") {
      return { kind: "failure", message: "Groundhog isn't set up yet - open the options page." };
    }
    return { kind: "failure", message: "Couldn't add this video to your watch history." };
  }

  /**
   * Read YouTube's own dark-mode flag. YouTube sets the boolean attribute
   * `dark` on <html> when dark theme is active (no attribute at all in
   * light mode) - this is the same signal YouTube's own UI uses, so the
   * overlay tracks it directly rather than guessing from prefers-color-scheme
   * (which reflects the OS, not necessarily YouTube's own per-site setting).
   */
  function isDarkMode() {
    return document.documentElement.hasAttribute("dark");
  }

  const CSS = `
    :host {
      all: initial;
    }

    /* ---- Design tokens -------------------------------------------------
     * Font stack matches YouTube's own (Roboto first, "YouTube Sans" as
     * YouTube's branded fallback, then system fallbacks) so overlay text
     * doesn't visually clash with the page around it.
     *
     * Light mode: background #f9f9f9 (YouTube's own light chrome
     * background, not stock white), primary text #0f0f0f, secondary text
     * #606060, border #e5e5e5 - all lifted from YouTube's light-theme
     * chrome colors.
     *
     * Dark mode: background #212121 (YouTube's dark-theme card/menu
     * background - the page background itself is #0f0f0f, #212121 is what
     * YouTube uses for chrome *on top of* that, which is the right layer
     * for an overlay panel), primary text #f1f1f1, secondary text #aaaaaa,
     * border #3f3f3f.
     *
     * No red anywhere - red is YouTube's own brand/accent color, and using
     * it here would read as "part of YouTube" in a misleading way rather
     * than "fitting in". No bright/neon accent either: the one accent role
     * (score-bar fill) just uses the primary text color at reduced opacity,
     * so it reads as restrained/monochrome, not a color statement.
     */
    .ghog-root {
      --ghog-font: Roboto, "YouTube Sans", Arial, sans-serif;
      --ghog-radius: 12px;
      --ghog-transition: 200ms ease;
      position: fixed;
      right: 16px;
      bottom: 16px;
      z-index: 2147483000; /* below YouTube's fullscreen/miniplayer chrome (2^31-1 range) but above ordinary page content */
      font-family: var(--ghog-font);
      pointer-events: none; /* re-enabled on the actual panel/badge below so the rest of the fixed layer never eats page clicks */
    }
    .ghog-root.ghog-light {
      --ghog-bg: #f9f9f9;
      --ghog-fg: #0f0f0f;
      --ghog-fg-secondary: #606060;
      --ghog-border: #e5e5e5;
      --ghog-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
      --ghog-track: #e5e5e5;
    }
    .ghog-root.ghog-dark {
      --ghog-bg: #212121;
      --ghog-fg: #f1f1f1;
      --ghog-fg-secondary: #aaaaaa;
      --ghog-border: #3f3f3f;
      --ghog-shadow: 0 2px 10px rgba(0, 0, 0, 0.5);
      --ghog-track: #3f3f3f;
    }

    .ghog-panel {
      pointer-events: auto;
      width: 280px;
      max-width: calc(100vw - 32px); /* keeps it usable on mobile-width layouts */
      background: var(--ghog-bg);
      color: var(--ghog-fg);
      border: 1px solid var(--ghog-border);
      border-radius: var(--ghog-radius);
      box-shadow: var(--ghog-shadow);
      overflow: hidden;
      opacity: 0;
      transform: translateY(6px);
      transition: opacity var(--ghog-transition), transform var(--ghog-transition);
    }
    .ghog-panel.ghog-visible {
      opacity: 1;
      transform: translateY(0);
    }

    .ghog-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 10px;
      border-bottom: 1px solid var(--ghog-border);
    }
    .ghog-title {
      font-size: 12px;
      font-weight: 500;
      color: var(--ghog-fg-secondary);
      letter-spacing: 0.2px;
    }
    .ghog-header-buttons {
      display: flex;
      gap: 4px;
    }
    .ghog-icon-btn {
      all: unset;
      cursor: pointer;
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      color: var(--ghog-fg-secondary);
      font-size: 13px;
      line-height: 1;
    }
    .ghog-icon-btn:hover {
      background: var(--ghog-track);
    }

    .ghog-body {
      padding: 10px 12px 12px;
    }

    .ghog-checking {
      font-size: 12px;
      color: var(--ghog-fg-secondary);
      display: flex;
      align-items: center;
      /* No gap: the dots span sits flush against the label text so it
       * reads as "...history..." with no visible space before the dots. */
    }
    .ghog-dots::after {
      content: "";
      animation: ghog-dots 1.4s steps(4, end) infinite;
    }
    @keyframes ghog-dots {
      0% { content: ""; }
      25% { content: "."; }
      50% { content: ".."; }
      75% { content: "..."; }
      100% { content: ""; }
    }

    /* "Can't evaluate" badge - deliberately distinct from both "checking..."
     * (no dots/spinner) and a real verdict (no score bars, no bold
     * recommendation line) so it reads as "we don't know" rather than "this
     * scored badly" or "still working." Muted/monochrome only, same no-red
     * rule as the rest of the overlay (see the design-tokens comment above)
     * - a neutral glyph in a soft circle, not a warning triangle or an
     * alarm color. */
    .ghog-cant-evaluate {
      display: flex;
      align-items: flex-start;
      gap: 8px;
    }
    .ghog-cant-evaluate-icon {
      flex-shrink: 0;
      width: 18px;
      height: 18px;
      margin-top: 1px;
      border-radius: 999px;
      background: var(--ghog-track);
      color: var(--ghog-fg-secondary);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 11px;
      line-height: 1;
    }
    .ghog-cant-evaluate-text {
      min-width: 0;
    }
    .ghog-cant-evaluate-label {
      font-size: 12px;
      font-weight: 600;
      color: var(--ghog-fg-secondary);
      margin-bottom: 2px;
    }
    .ghog-cant-evaluate-reason {
      font-size: 11.5px;
      color: var(--ghog-fg-secondary);
      line-height: 1.4;
    }

    /* Persistent footer (mark-as-watched button + the corpus-add note) -
     * shown below whatever renderBody() produces for the current phase,
     * since both are about the *corpus* rather than the verdict check (see
     * overlay-state.js's watchNote docs). */
    .ghog-footer {
      padding: 0 12px 10px;
    }
    .ghog-mark-watched-btn {
      all: unset;
      cursor: pointer;
      display: inline-block;
      padding: 4px 10px;
      font-size: 11px;
      font-weight: 500;
      color: var(--ghog-fg);
      border: 1px solid var(--ghog-border);
      border-radius: 999px;
    }
    .ghog-mark-watched-btn:hover {
      background: var(--ghog-track);
    }
    .ghog-mark-watched-btn:disabled {
      cursor: default;
      opacity: 0.6;
    }
    .ghog-watch-note {
      margin-top: 6px;
      font-size: 11px;
      color: var(--ghog-fg-secondary);
      line-height: 1.4;
      opacity: 0;
      max-height: 0;
      overflow: hidden;
      transition: opacity var(--ghog-transition);
    }
    .ghog-watch-note.ghog-visible {
      opacity: 1;
      max-height: none;
    }

    /* Only shown for setup-shaped errors - "Open settings" is a
     * useless action for e.g. "companion unreachable", so this stays out of
     * the DOM entirely for every other error rather than being hidden via
     * CSS. Styled as a subtle outlined pill, same muted/monochrome language
     * as the rest of the overlay - not a bright call-to-action button. */
    .ghog-cant-evaluate-action {
      all: unset;
      cursor: pointer;
      display: inline-block;
      margin-top: 6px;
      padding: 4px 10px;
      font-size: 11px;
      font-weight: 500;
      color: var(--ghog-fg);
      border: 1px solid var(--ghog-border);
      border-radius: 999px;
    }
    .ghog-cant-evaluate-action:hover {
      background: var(--ghog-track);
    }

    /* Recommendation is the single most prominent line in the panel - the
     * actual "should I watch this" takeaway. Everything else (scores,
     * explanation) is visually secondary. */
    .ghog-recommendation {
      font-size: 13px;
      font-weight: 600;
      color: var(--ghog-fg);
      line-height: 1.35;
      margin-bottom: 10px;
    }

    .ghog-scores {
      display: flex;
      flex-direction: column;
      gap: 5px;
      margin-bottom: 10px;
    }
    .ghog-score-row {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .ghog-score-label {
      /* 64px, not 52px: "EXECUTION" is the longest of the three labels and
       * was overflowing the old width, butting up against the score bar. */
      width: 64px;
      flex-shrink: 0;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.4px;
      color: var(--ghog-fg-secondary);
    }
    .ghog-score-track {
      flex: 1;
      height: 4px;
      border-radius: 999px;
      background: var(--ghog-track);
      overflow: hidden;
    }
    .ghog-score-fill {
      height: 100%;
      border-radius: 999px;
      /* Monochrome fill (primary text color at reduced opacity) - a
       * deliberate restrained choice, not a coded traffic-light color
       * scheme. */
      background: var(--ghog-fg);
      opacity: 0.55;
    }
    .ghog-score-value {
      width: 24px;
      flex-shrink: 0;
      text-align: right;
      font-size: 10px;
      font-variant-numeric: tabular-nums;
      color: var(--ghog-fg-secondary);
    }

    .ghog-explanation {
      font-size: 11.5px;
      color: var(--ghog-fg-secondary);
      line-height: 1.4;
    }

    .ghog-badge {
      pointer-events: auto;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      background: var(--ghog-bg);
      color: var(--ghog-fg);
      border: 1px solid var(--ghog-border);
      border-radius: 999px;
      box-shadow: var(--ghog-shadow);
      font-size: 11px;
      font-weight: 500;
      cursor: pointer;
      opacity: 0;
      transform: translateY(6px);
      transition: opacity var(--ghog-transition), transform var(--ghog-transition);
    }
    .ghog-badge.ghog-visible {
      opacity: 1;
      transform: translateY(0);
    }
    .ghog-badge:hover {
      filter: brightness(1.05);
    }
  `;

  function ensureDom() {
    if (shadowRoot) {
      return;
    }
    const host = document.createElement("div");
    host.id = HOST_ID;
    document.documentElement.appendChild(host);
    shadowRoot = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = CSS;
    shadowRoot.appendChild(style);

    const root = document.createElement("div");
    root.className = "ghog-root";
    shadowRoot.appendChild(root);

    const panel = document.createElement("div");
    panel.className = "ghog-panel";
    root.appendChild(panel);

    const header = document.createElement("div");
    header.className = "ghog-header";
    panel.appendChild(header);

    const title = document.createElement("div");
    title.className = "ghog-title";
    title.textContent = "Groundhog";
    header.appendChild(title);

    const headerButtons = document.createElement("div");
    headerButtons.className = "ghog-header-buttons";
    header.appendChild(headerButtons);

    const collapseBtn = document.createElement("button");
    collapseBtn.className = "ghog-icon-btn";
    collapseBtn.title = "Collapse";
    collapseBtn.textContent = "−"; // minus sign
    collapseBtn.addEventListener("click", () => {
      state = toggleCollapsed(state);
      render();
    });
    headerButtons.appendChild(collapseBtn);

    const dismissBtn = document.createElement("button");
    dismissBtn.className = "ghog-icon-btn";
    dismissBtn.title = "Dismiss";
    dismissBtn.textContent = "×"; // times sign
    dismissBtn.addEventListener("click", () => {
      state = dismissOverlay(state);
      render();
    });
    headerButtons.appendChild(dismissBtn);

    const body = document.createElement("div");
    body.className = "ghog-body";
    panel.appendChild(body);

    const footer = document.createElement("div");
    footer.className = "ghog-footer";
    panel.appendChild(footer);

    const markWatchedBtn = document.createElement("button");
    markWatchedBtn.className = "ghog-mark-watched-btn";
    markWatchedBtn.textContent = "Mark as watched";
    markWatchedBtn.addEventListener("click", () => {
      if (typeof GroundhogOverlay.onMarkWatchedClick === "function" && currentVideoId) {
        GroundhogOverlay.onMarkWatchedClick(currentVideoId);
      }
      markWatchedBtn.disabled = true;
      markWatchedBtn.textContent = "Marking as watched…";
    });
    footer.appendChild(markWatchedBtn);

    const watchNote = document.createElement("div");
    watchNote.className = "ghog-watch-note";
    footer.appendChild(watchNote);

    const badge = document.createElement("div");
    badge.className = "ghog-badge";
    badge.title = "Show Groundhog check";
    badge.addEventListener("click", () => {
      state = toggleCollapsed(state);
      render();
    });
    root.appendChild(badge);

    els = { host, root, panel, body, footer, markWatchedBtn, watchNote, badge };
  }

  /** Build the body's inner content for the current state. Pure DOM construction, no side effects on `state`. */
  function renderBody() {
    const body = els.body;
    body.textContent = "";

    if (state.phase === "checking") {
      const p = document.createElement("div");
      p.className = "ghog-checking";
      const label = document.createElement("span");
      label.textContent = "Checking your watch history";
      const dots = document.createElement("span");
      dots.className = "ghog-dots";
      p.appendChild(label);
      p.appendChild(dots);
      body.appendChild(p);
      return;
    }

    if (state.phase === "stale") {
      // Terminal state: the extension's context was invalidated (reload/
      // update) while this tab was already open - nothing in this tab can
      // reach the background worker anymore, so the only useful action is
      // an actual page reload (window.location.reload(), not a chrome.*
      // call - see content.js's isExtensionContextValid). Reuses the same
      // muted "can't evaluate" visual language rather than inventing a new
      // one, since it's the same "we can't tell you anything right now"
      // shape of message.
      const wrap = document.createElement("div");
      wrap.className = "ghog-cant-evaluate";

      const icon = document.createElement("div");
      icon.className = "ghog-cant-evaluate-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "!";
      wrap.appendChild(icon);

      const text = document.createElement("div");
      text.className = "ghog-cant-evaluate-text";

      const label = document.createElement("div");
      label.className = "ghog-cant-evaluate-label";
      label.textContent = "Groundhog needs a refresh";
      text.appendChild(label);

      const reason = document.createElement("div");
      reason.className = "ghog-cant-evaluate-reason";
      reason.textContent = "The extension was updated - reload this page to keep checking videos.";
      text.appendChild(reason);

      const action = document.createElement("button");
      action.className = "ghog-cant-evaluate-action";
      action.textContent = "Reload page";
      action.addEventListener("click", () => window.location.reload());
      text.appendChild(action);

      wrap.appendChild(text);
      body.appendChild(wrap);
      return;
    }

    if (state.phase === "error") {
      // Neutral "can't evaluate" badge: same treatment no matter which
      // failure source produced it (no transcript, companion
      // unreachable/timed out, Gemini call failure) - only the one-line
      // reason (classifyOverlayError) differs.
      const wrap = document.createElement("div");
      wrap.className = "ghog-cant-evaluate";

      const icon = document.createElement("div");
      icon.className = "ghog-cant-evaluate-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "–"; // en dash: reads as "no reading available", not an alarm glyph
      wrap.appendChild(icon);

      const text = document.createElement("div");
      text.className = "ghog-cant-evaluate-text";

      const label = document.createElement("div");
      label.className = "ghog-cant-evaluate-label";
      label.textContent = "Can't evaluate video";
      text.appendChild(label);

      const reason = document.createElement("div");
      reason.className = "ghog-cant-evaluate-reason";
      reason.textContent = classifyOverlayError(state.data.message, state.data.code);
      text.appendChild(reason);

      if (isSetupError(state.data.message, state.data.code)) {
        const action = document.createElement("button");
        action.className = "ghog-cant-evaluate-action";
        action.textContent = "Open settings";
        action.addEventListener("click", () => {
          if (typeof GroundhogOverlay.onOpenSettingsClick === "function") {
            GroundhogOverlay.onOpenSettingsClick();
          }
        });
        text.appendChild(action);
      } else if (isRetryableError(state.data.message, state.data.code)) {
        const action = document.createElement("button");
        action.className = "ghog-cant-evaluate-action";
        action.textContent = "Retry";
        action.addEventListener("click", () => {
          if (typeof GroundhogOverlay.onRetryClick === "function" && currentVideoId) {
            GroundhogOverlay.onRetryClick(currentVideoId);
          }
        });
        text.appendChild(action);
      }

      wrap.appendChild(text);
      body.appendChild(wrap);
      return;
    }

    // phase === "verdict"
    const verdict = state.data || {};

    const recommendation = document.createElement("div");
    recommendation.className = "ghog-recommendation";
    recommendation.textContent = verdict.recommendation || "";
    body.appendChild(recommendation);

    const scores = document.createElement("div");
    scores.className = "ghog-scores";
    body.appendChild(scores);
    [
      ["Novelty", verdict.novelty],
      ["Execution", verdict.execution],
      ["Depth", verdict.depth],
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "ghog-score-row";

      const labelEl = document.createElement("span");
      labelEl.className = "ghog-score-label";
      labelEl.textContent = label;
      row.appendChild(labelEl);

      const track = document.createElement("div");
      track.className = "ghog-score-track";
      const fill = document.createElement("div");
      fill.className = "ghog-score-fill";
      const numeric = typeof value === "number" ? value : 0;
      const pct = Math.max(0, Math.min(100, (numeric / 10) * 100));
      fill.style.width = pct + "%";
      track.appendChild(fill);
      row.appendChild(track);

      const valueEl = document.createElement("span");
      valueEl.className = "ghog-score-value";
      valueEl.textContent = typeof value === "number" ? String(value) : "?";
      row.appendChild(valueEl);

      scores.appendChild(row);
    });

    if (verdict.explanation) {
      const explanation = document.createElement("div");
      explanation.className = "ghog-explanation";
      explanation.textContent = verdict.explanation;
      body.appendChild(explanation);
    }
  }

  /**
   * Re-render the whole overlay from `state`. Applies a short fade/slide
   * transition (the .ghog-visible class, transitioned in CSS over 200ms -
   * see the design-tokens comment above) whenever content changes, rather
   * than an abrupt pop - covers both "first appears" and "checking ->
   * verdict updates in place".
   */
  function render() {
    ensureDom();

    const root = els.root;
    root.classList.toggle("ghog-light", !isDarkMode());
    root.classList.toggle("ghog-dark", isDarkMode());

    if (state.dismissed) {
      els.panel.classList.remove("ghog-visible");
      els.badge.classList.remove("ghog-visible");
      els.host.style.display = "none";
      return;
    }
    els.host.style.display = "";

    if (state.collapsed) {
      els.panel.classList.remove("ghog-visible");
      els.panel.style.display = "none";
      els.badge.style.display = "inline-flex";
      els.badge.textContent = badgeLabel();
      // Force a reflow so the opacity/transform transition re-triggers even
      // if the badge was already in the DOM (e.g. re-collapsing after a
      // content update) - matches how content updates should still read as
      // a soft transition, not an abrupt cut.
      void els.badge.offsetWidth;
      els.badge.classList.add("ghog-visible");
    } else {
      els.badge.classList.remove("ghog-visible");
      els.badge.style.display = "none";
      els.panel.style.display = "block";
      renderBody();
      renderFooter();
      void els.panel.offsetWidth;
      els.panel.classList.add("ghog-visible");
    }
  }

  /**
   * Render the persistent footer (mark-as-watched button + corpus-add
   * note) - kept separate from renderBody() since both are about the
   * *corpus* rather than the current verdict phase, and must survive a
   * phase change (e.g. "checking" -> "verdict") without being wiped out or
   * reset by it.
   */
  function renderFooter() {
    // Nothing in the footer (mark-as-watched, corpus-add note) can do
    // anything useful once the extension context is invalidated - every
    // chrome.runtime.sendMessage it would trigger is already known to fail.
    els.footer.style.display = state.phase === "stale" ? "none" : "";
    if (state.phase === "stale") {
      return;
    }
    els.watchNote.classList.toggle("ghog-visible", Boolean(state.watchNote));
    if (state.watchNote) {
      els.watchNote.textContent = state.watchNote.message;
    }
  }

  function badgeLabel() {
    if (state.phase === "checking") {
      return "Groundhog: checking…";
    }
    if (state.phase === "error") {
      return "Groundhog: can't evaluate video";
    }
    if (state.phase === "stale") {
      return "Groundhog: needs a refresh";
    }
    const verdict = state.data || {};
    if (typeof verdict.novelty === "number") {
      return "Groundhog: " + verdict.novelty + "/10";
    }
    return "Groundhog";
  }

  const GroundhogOverlay = {
    /**
     * Set by content.js to a function that opens the extension's options
     * page. Called when the user clicks "Open settings" on a setup-shaped
     * error. overlay.js stays DOM-only and never touches chrome.* itself
     * (content scripts may not have access to every chrome.runtime method
     * background.js does, e.g. openOptionsPage) - it just invokes whatever
     * content.js registered here, defaulting to a no-op if nothing has
     * (shouldn't normally happen - content.js sets this at load time).
     */
    onOpenSettingsClick: null,
    /**
     * Set by content.js to a function that posts GROUNDHOG_VIDEO_WATCHED
     * for the given video ID - the same message the automatic
     * watch-threshold path already sends, so both share one result path
     * (background.js's postVideoWatched, reported back as
     * setWatchedResult below). overlay.js stays chrome.*-free for the same
     * reason onOpenSettingsClick does.
     */
    onMarkWatchedClick: null,
    /**
     * Set by content.js to a function that re-fires a fresh
     * GROUNDHOG_VIDEO_OPENED request for the given video ID, bypassing
     * content.js's lastPostedVideoId dedupe (which exists to skip no-op
     * navigations, not to block an explicit retry) - see content.js's
     * onRetryClick. Called when the user clicks "Retry" on a
     * retry-worthy error (see isRetryableError above).
     */
    onRetryClick: null,
    /**
     * Called by content.js (see isExtensionContextValid/safeSendMessage)
     * the first time a chrome.runtime call fails because the extension's
     * context was invalidated - e.g. the extension got reloaded/updated
     * while this tab was already open. Moves to the terminal "stale" phase;
     * nothing recovers from this short of an actual page reload.
     */
    showContextInvalidated() {
      state = markContextInvalidated(state);
      render();
    },
    /** Called on every fresh video-opened request - see content.js. Always starts un-collapsed, un-dismissed, showing "checking...". */
    reset(videoId) {
      currentVideoId = videoId;
      state = createOverlayState();
      if (watchNoteTimer) {
        clearTimeout(watchNoteTimer);
        watchNoteTimer = null;
      }
      render();
      if (els) {
        els.markWatchedBtn.disabled = false;
        els.markWatchedBtn.textContent = "Mark as watched";
      }
    },
    /** Called when the background worker's /verdict response (or an error) comes back for `videoId`. Ignored if the user has since navigated to a different video (stale response). */
    setResult(videoId, result) {
      if (videoId !== currentVideoId) {
        return;
      }
      state = applyVerdictResult(state, result);
      render();
    },
    /**
     * Called when background.js's postVideoWatched result (either the
     * automatic watch-threshold path or a manual "Mark as watched" click)
     * comes back for `videoId`. Ignored if the user has since navigated to
     * a different video. Shows the note for WATCH_NOTE_TIMEOUT_MS, then
     * auto-clears it - a stray earlier timer is cleared first so two
     * results arriving close together don't have the first one's timer
     * wipe out the second one's note early.
     */
    setWatchedResult(videoId, result) {
      if (videoId !== currentVideoId) {
        return;
      }
      if (watchNoteTimer) {
        clearTimeout(watchNoteTimer);
        watchNoteTimer = null;
      }
      state = setWatchNote(state, describeWatchedResult(result));
      render();
      if (els) {
        els.markWatchedBtn.disabled = false;
        els.markWatchedBtn.textContent = "Mark as watched";
      }
      watchNoteTimer = setTimeout(() => {
        state = clearWatchNote(state);
        watchNoteTimer = null;
        render();
      }, WATCH_NOTE_TIMEOUT_MS);
    },
    /**
     * Called from content.js's handleNavigation when a `yt-navigate-finish`
     * lands on a URL that isn't a watch page at all (extractVideoId
     * returned null). Removes the shadow-DOM host entirely (rather than
     * hiding it via `display: none`) and drops the `els`/`shadowRoot`
     * references so the removed subtree is eligible for GC.
     *
     * Deliberately does not reset `window.__groundhogOverlayInstalled` or
     * `GroundhogOverlay` itself - those track "has this content-script
     * instance's IIFE run," not "is a host currently in the DOM," and must
     * survive teardown so a later reset() can run ensureDom() again.
     *
     * Resets `currentVideoId` too, so a stale setResult() for the
     * torn-down video arriving after this runs is ignored instead of
     * recreating the host we just removed.
     */
    teardown() {
      currentVideoId = null;
      state = createOverlayState();
      if (watchNoteTimer) {
        clearTimeout(watchNoteTimer);
        watchNoteTimer = null;
      }
      if (els && els.host && els.host.parentNode) {
        els.host.parentNode.removeChild(els.host);
      }
      shadowRoot = null;
      els = null;
    },
  };

  window.GroundhogOverlay = GroundhogOverlay;
})();
