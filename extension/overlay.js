/**
 * On-page overlay: the actual DOM/rendering half. Uses the pure state
 * machine in overlay-state.js (createOverlayState / applyVerdictResult /
 * toggleCollapsed / dismissOverlay) for all state transitions, and only
 * concerns itself with building/injecting/re-rendering the panel.
 *
 * Loaded before content.js in manifest.json (after overlay-state.js), so
 * `GroundhogOverlay` is available as a global content.js can call into:
 *
 *   GroundhogOverlay.reset(videoId)        - fresh "checking..." panel
 *   GroundhogOverlay.setResult(videoId, r) - fill in verdict or error
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
 * Turn a raw error string (from companion/app.py's `{error: "..."}`,
 * companion/verdict.py's Gemini failures, or background.js's own
 * companion-unreachable/timeout messages) into a short, calm, one-line
 * reason for the "can't evaluate" badge.
 *
 * Deliberately pattern-matching on recognizable substrings rather than an
 * exhaustive enum: the known failure sources (no transcript, companion
 * unreachable/timed out, Gemini API failure) each produce error text of a
 * wildly different shape and verbosity, and this only needs to degrade
 * gracefully for anything unrecognized - not enumerate every possible
 * internal exception string. Kept outside the IIFE below (and exported via
 * module.exports) so it's plain, DOM-free, testable logic - same pattern as
 * overlay-state.js/video-id.js/watch-tracker.js.
 */
function classifyOverlayError(raw) {
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
    return "Groundhog companion returned an error.";
  }

  // companion/verdict.py's already-clean Gemini-failure message (client/
  // server/generic API errors, and the "did not return a parseable verdict"
  // case all return this same text directly - see verdict.py) - matched
  // here as a direct pass-through, plus the legacy substring in case an
  // older/unexpected message still mentions Gemini by name.
  if (msg.includes("couldn't reach the verdict service") || msg.includes("gemini")) {
    return "Couldn't reach the verdict service.";
  }

  // Generic fallback for anything unrecognized - still calm and short,
  // never the raw exception text.
  return "Groundhog couldn't evaluate this video.";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { classifyOverlayError };
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
      width: 52px;
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

    const badge = document.createElement("div");
    badge.className = "ghog-badge";
    badge.title = "Show Groundhog check";
    badge.addEventListener("click", () => {
      state = toggleCollapsed(state);
      render();
    });
    root.appendChild(badge);

    els = { host, root, panel, body, badge };
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
      reason.textContent = classifyOverlayError(state.data);
      text.appendChild(reason);

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
      void els.panel.offsetWidth;
      els.panel.classList.add("ghog-visible");
    }
  }

  function badgeLabel() {
    if (state.phase === "checking") {
      return "Groundhog: checking…";
    }
    if (state.phase === "error") {
      return "Groundhog: can't evaluate video";
    }
    const verdict = state.data || {};
    if (typeof verdict.novelty === "number") {
      return "Groundhog: " + verdict.novelty + "/10";
    }
    return "Groundhog";
  }

  const GroundhogOverlay = {
    /** Called on every fresh video-opened request - see content.js. Always starts un-collapsed, un-dismissed, showing "checking...". */
    reset(videoId) {
      currentVideoId = videoId;
      state = createOverlayState();
      render();
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
      if (els && els.host && els.host.parentNode) {
        els.host.parentNode.removeChild(els.host);
      }
      shadowRoot = null;
      els = null;
    },
  };

  window.GroundhogOverlay = GroundhogOverlay;
})();
