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

const COMPANION_ORIGIN = "http://127.0.0.1:8787";

// #5's real endpoint (companion/app.py's POST /verdict). Fires once per
// video-opened event from content.js. `k` and `model` are left out here so
// the companion applies its own server-side defaults - issue #9 (options
// page K slider) is what will start passing them explicitly.
const VERDICT_PATH = "/verdict";

// Fires once per video, when content.js's WatchThresholdTracker crosses the
// 70%/5-minute watch threshold (issue #7). The companion fetches the
// transcript, embeds it, and adds it to the corpus - see
// companion/app.py's POST /videos/watched.
const VIDEO_WATCHED_PATH = "/videos/watched";

// Header name must match companion/auth.py's SECRET_HEADER exactly
// ("X-Groundhog-Secret") or every request gets a 401.
const SECRET_HEADER = "X-Groundhog-Secret";

// TODO(#9): there is no options page yet (that's a separate issue - "Extension:
// options page for secret paste and K slider"). Until it exists, the secret
// has to be seeded into chrome.storage.local some other way - e.g. from the
// service worker's console in chrome://extensions during manual testing:
//   chrome.storage.local.set({ groundhogSecret: "<value from .groundhog-secret>" })
// Once the options page lands, it should write to this same key so this
// read path doesn't need to change.
async function readSecret() {
  const { groundhogSecret } = await chrome.storage.local.get("groundhogSecret");
  return groundhogSecret || null;
}

/**
 * Call the companion's /verdict endpoint for a video and return either the
 * verdict object (novelty/execution/depth/explanation/recommendation) or an
 * `{ error }` shape - the same "always resolves to something the overlay can
 * render, never throws" contract companion/app.py's endpoint itself follows.
 * This lets content.js's overlay just branch on `result.error` without
 * needing try/catch of its own (issue #8: "not crash or hang on an error
 * response").
 */
async function requestVerdict(videoId) {
  const secret = await readSecret();
  if (!secret) {
    const message =
      "no secret configured in chrome.storage.local (key 'groundhogSecret')";
    console.warn("Groundhog: " + message + "; skipping verdict request for video " + videoId);
    return { error: message };
  }

  try {
    const response = await fetch(COMPANION_ORIGIN + VERDICT_PATH, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [SECRET_HEADER]: secret,
      },
      body: JSON.stringify({ video_id: videoId }),
    });
    if (!response.ok) {
      console.warn(
        "Groundhog: companion responded " + response.status + " for video " + videoId
      );
      return { error: "companion responded with status " + response.status };
    }
    // companion/app.py's /verdict always returns 200 with either a verdict
    // object or { error }, so whatever comes back here is already in the
    // shape the overlay expects - just pass it through.
    return await response.json();
  } catch (err) {
    // The companion may simply not be running, or the request may have
    // timed out - fail into an error result the overlay can show, rather
    // than leaving it stuck on "checking..." forever. A unified "can't
    // evaluate" badge treatment across all failure modes is issue #10; for
    // now the overlay just shows this message plainly.
    console.warn("Groundhog: verdict request failed", err);
    return { error: "companion request failed: " + (err && err.message ? err.message : String(err)) };
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
    // Same rationale as postVideoOpened: the companion may just not be
    // running - fail quietly rather than spamming the console.
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
      // can take several seconds (transcript retrieval alone is 2-4s, see
      // PLAN.md) - content.js's overlay is already showing "checking..." by
      // the time this arrives, driven by GroundhogOverlay.reset() at the
      // point the request was first fired.
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
