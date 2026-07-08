/**
 * Background service worker: does the actual fetch() to the companion.
 *
 * Kept out of the content script deliberately - the content script runs in
 * the YouTube page's isolated world, and cross-origin fetches from that
 * context can get tangled up in the host page's own CSP. The service worker
 * has no such page context, only the extension's own `host_permissions`
 * (scoped to http://127.0.0.1:8787/* - see manifest.json), so it's the
 * right place for outbound requests to the companion to live.
 */

// Shared K default/clamp logic - also used by options.js so the slider and
// the request-building code here agree on the same default and valid range
// without duplicating the numbers.
importScripts("options-k.js");

const COMPANION_ORIGIN = "http://127.0.0.1:8787";

// companion/app.py's POST /verdict. Fires once per video-opened event from
// content.js. `model` is left out so the companion applies its own
// server-side default; a model picker is a later addition (PLAN.md).
const VERDICT_PATH = "/verdict";

// companion/app.py's POST /videos/watched. Fires once per video, when
// content.js's WatchThresholdTracker crosses the 70%/5-minute watch
// threshold. The companion fetches the transcript, embeds it, and adds it
// to the corpus.
const VIDEO_WATCHED_PATH = "/videos/watched";

// Header name must match companion/auth.py's SECRET_HEADER exactly
// ("X-Groundhog-Secret") or every request gets a 401.
const SECRET_HEADER = "X-Groundhog-Secret";

// Client-side safety net: the companion's own Gemini call is bounded at
// ~45s (companion/verdict.py's DEFAULT_TIMEOUT_SECONDS), but that only
// helps if the companion process itself is alive to respond at all. If it
// hangs (stuck request, wedged event loop) or never gets to answer, nothing
// else here would ever stop "checking..." from spinning forever. 60s
// comfortably clears the companion's own 45s budget plus network/queueing
// overhead.
const VERDICT_TIMEOUT_MS = 60000;

async function readSecret() {
  const { groundhogSecret } = await chrome.storage.local.get("groundhogSecret");
  return groundhogSecret || null;
}

// Falls back to DEFAULT_K (options-k.js) if it's never been set - e.g. the
// very first run before the user has ever opened the options page - so
// behavior is identical whether or not the options page has been opened
// yet, matching companion/app.py's own server-side default of 5.
async function readK() {
  const { groundhogK } = await chrome.storage.local.get("groundhogK");
  return clampK(groundhogK);
}

// User-facing copy for the "no secret yet" case - rendered directly in
// overlay.js's error body (see overlay-state.js's applyVerdictResult, which
// takes `result.error` as the display string), so it needs to read as an
// instruction a real user can act on. `notConfigured: true` travels
// alongside it so overlay.js can branch on this specific case without
// string-matching the message.
const NOT_CONFIGURED_MESSAGE =
  "Groundhog isn't set up yet - open the extension's options page and paste your secret from .groundhog-secret.";

/**
 * Call the companion's /verdict endpoint for a video and return either the
 * verdict object (novelty/execution/depth/explanation/recommendation) or an
 * `{ error }` shape - the same "always resolves to something the overlay can
 * render, never throws" contract companion/app.py's endpoint itself follows.
 */
async function requestVerdict(videoId) {
  const secret = await readSecret();
  if (!secret) {
    console.warn(
      "Groundhog: no secret configured in chrome.storage.local (key 'groundhogSecret'); " +
        "skipping verdict request for video " + videoId
    );
    return { error: NOT_CONFIGURED_MESSAGE, notConfigured: true };
  }
  const k = await readK();

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), VERDICT_TIMEOUT_MS);

  try {
    const response = await fetch(COMPANION_ORIGIN + VERDICT_PATH, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [SECRET_HEADER]: secret,
      },
      body: JSON.stringify({ video_id: videoId, k }),
      signal: controller.signal,
    });
    if (!response.ok) {
      console.error(
        "Groundhog: companion responded " + response.status + " for video " + videoId
      );
      return { error: "companion responded with status " + response.status };
    }
    // companion/app.py's /verdict always returns 200 with either a verdict
    // object or { error }, so whatever comes back here is already in the
    // shape the overlay expects - just pass it through.
    return await response.json();
  } catch (err) {
    // fetch() rejects with a DOMException named "AbortError" when the
    // AbortController above fires - distinguish that from "companion isn't
    // running at all" so overlay.js's classifyOverlayError can give it its
    // own one-line reason.
    if (err && err.name === "AbortError") {
      console.error(
        "Groundhog: verdict request timed out after " + VERDICT_TIMEOUT_MS + "ms for video " + videoId
      );
      return { error: "companion request timed out after " + (VERDICT_TIMEOUT_MS / 1000) + "s" };
    }
    // The full error (e.g. the browser's raw "Failed to fetch"/NetworkError
    // text) is logged here for debugging, not included in the returned
    // message - that field ends up rendered in the overlay, so it stays a
    // short, calm string rather than leaking raw error text to the user.
    console.error("Groundhog: verdict request failed for video " + videoId, err);
    return { error: "companion request failed" };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function postVideoWatched(videoId) {
  const secret = await readSecret();
  if (!secret) {
    console.warn(
      "Groundhog: no secret configured in chrome.storage.local (key 'groundhogSecret'); " +
        "skipping watched-video request for " + videoId
    );
    return;
  }

  try {
    const response = await fetch(COMPANION_ORIGIN + VIDEO_WATCHED_PATH, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [SECRET_HEADER]: secret,
      },
      body: JSON.stringify({ video_id: videoId }),
    });
    if (!response.ok) {
      console.warn(
        "Groundhog: companion responded " + response.status + " to watched-video request for " + videoId
      );
    }
  } catch (err) {
    // The companion may just not be running - fail quietly rather than
    // spamming the console.
    console.warn("Groundhog: watched-video request failed", err);
  }
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (!message) {
    return;
  }
  if (message.type === "GROUNDHOG_VIDEO_OPENED" && message.videoId) {
    const tabId = sender && sender.tab ? sender.tab.id : null;
    requestVerdict(message.videoId).then((result) => {
      if (tabId == null) {
        // No tab to route the result back to (shouldn't normally happen -
        // this message only ever comes from the content script, which
        // always runs in a tab) - nothing more to do.
        return;
      }
      // Routed back as a separate message (rather than a sendResponse to
      // the original GROUNDHOG_VIDEO_OPENED message) because the fetch above
      // can take several seconds (transcript retrieval alone is 2-4s) -
      // content.js's overlay is already showing "checking..." by the time
      // this arrives, driven by GroundhogOverlay.reset() at the point the
      // request was first fired.
      chrome.tabs
        .sendMessage(tabId, {
          type: "GROUNDHOG_VERDICT_RESULT",
          videoId: message.videoId,
          result,
        })
        .catch((err) => {
          // The tab may have navigated away or closed before the verdict
          // came back - fail quietly, there's nothing left to update.
          console.warn("Groundhog: could not deliver verdict result to tab", err);
        });
    });
  }
  if (message.type === "GROUNDHOG_VIDEO_WATCHED" && message.videoId) {
    postVideoWatched(message.videoId);
  }
});
