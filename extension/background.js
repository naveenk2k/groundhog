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

// Shared model default/resolve logic - also used by options.js so the model
// <select> and the request-building code here agree on the same default and
// valid tiers without duplicating the strings.
importScripts("options-model.js");

// Pure response/error -> `{ error }` decision logic for requestVerdict(),
// pulled out into its own DOM-free/chrome.*-free file so it can be
// unit-tested directly in Node - see background-classify.js and
// background-classify.test.js.
importScripts("background-classify.js");

// Persisted breadcrumb log (chrome.storage.local, readable from
// options.html) for diagnosing whether this service worker is being torn
// down mid-request - see debug-log.js's own docs for why console.log can't
// be relied on for this.
importScripts("debug-log.js");

const COMPANION_ORIGIN = "http://127.0.0.1:8787";

// companion/app.py's POST /verdict. Fires once per video-opened event from
// content.js. `model` is read from the options page's picker (see
// readModel()/options-model.js) and forwarded on every request.
const VERDICT_PATH = "/verdict";

// companion/app.py's POST /videos/watched. Fires once per video, when
// content.js's WatchThresholdTracker crosses the 70%/5-minute watch
// threshold. The companion fetches the transcript, embeds it, and adds it
// to the corpus.
const VIDEO_WATCHED_PATH = "/videos/watched";

// companion/app.py's GET /videos/{video_id}. Fires once per video-opened
// navigation, before ever requesting a verdict - skips the Gemini call
// entirely for a video already in the corpus. video_id is a YouTube video
// ID (fixed-format, no path-unsafe characters), so no extra escaping beyond
// this is needed for the URL.
const VIDEO_LOOKUP_PATH_PREFIX = "/videos/";

// Header name must match companion/auth.py's SECRET_HEADER exactly
// ("X-Groundhog-Secret") or every request gets a 401.
const SECRET_HEADER = "X-Groundhog-Secret";

// VERDICT_TIMEOUT_MS (the client-side fetch timeout budget) lives in
// background-classify.js, imported above, so the timeout value and the
// error string built from it (in classifyVerdictError) can't drift apart.

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

// Falls back to DEFAULT_MODEL (options-model.js) if it's never been set -
// e.g. the very first run before the user has ever opened the options page -
// which matches companion/verdict.py's own hardcoded default, so behavior is
// identical whether or not the options page has been opened yet.
async function readModel() {
  const { groundhogModel } = await chrome.storage.local.get("groundhogModel");
  return resolveModel(groundhogModel);
}

// User-facing copy for the "no secret yet" case - rendered directly in
// overlay.js's error body, so it needs to read as an instruction a real user
// can act on. The "not_configured" code travels alongside it so overlay.js's
// classifyOverlayError can branch on this specific case without
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
    return { error: NOT_CONFIGURED_MESSAGE, code: "not_configured" };
  }
  const k = await readK();
  const model = await readModel();

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), VERDICT_TIMEOUT_MS);

  await logBreadcrumb("verdict_fetch_start", { videoId });
  try {
    const response = await fetch(COMPANION_ORIGIN + VERDICT_PATH, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [SECRET_HEADER]: secret,
      },
      body: JSON.stringify({ video_id: videoId, k, model }),
      signal: controller.signal,
    });
    await logBreadcrumb("verdict_fetch_responded", { videoId, status: response.status });
    const responseError = classifyVerdictResponse(response);
    if (responseError) {
      console.error(
        "Groundhog: companion responded " + response.status + " for video " + videoId
      );
      return responseError;
    }
    // companion/app.py's /verdict always returns 200 with either a verdict
    // object or { error }, so whatever comes back here is already in the
    // shape the overlay expects - just pass it through.
    return await response.json();
  } catch (err) {
    // fetch() rejects with a DOMException named "AbortError" when the
    // AbortController above fires - distinguish that from "companion isn't
    // running at all" so overlay.js's classifyOverlayError can give it its
    // own one-line reason. See background-classify.js's classifyVerdictError
    // for the actual decision.
    if (err && err.name === "AbortError") {
      console.error(
        "Groundhog: verdict request timed out after " + VERDICT_TIMEOUT_MS + "ms for video " + videoId
      );
      await logBreadcrumb("verdict_fetch_timeout", { videoId });
    } else {
      // The full error (e.g. the browser's raw "Failed to fetch"/NetworkError
      // text) is logged here for debugging, not included in the returned
      // message - that field ends up rendered in the overlay, so it stays a
      // short, calm string rather than leaking raw error text to the user.
      console.error("Groundhog: verdict request failed for video " + videoId, err);
      await logBreadcrumb("verdict_fetch_error", { videoId, name: err && err.name, message: err && err.message });
    }
    return classifyVerdictError(err);
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Call the companion's POST /videos/watched for a video and return
 * `{ added, reason }` - companion/app.py's videos_watched always returns
 * this shape with a 200 (a missing transcript is a normal "not added"
 * outcome, not a server error - see that endpoint's docstring), so the only
 * other case to synthesize a result for here is the request itself failing
 * (companion not running, no secret configured, a non-2xx status).
 *
 * Used both by the automatic watch-threshold path (content.js's
 * handleTimeUpdate) and the manual "mark as watched" button (overlay.js's
 * onMarkWatchedClick) - see the GROUNDHOG_VIDEO_WATCHED handler below,
 * which reports this back to the tab as GROUNDHOG_WATCHED_RESULT either
 * way, so both paths can show the same corpus-add feedback.
 */
async function postVideoWatched(videoId) {
  const secret = await readSecret();
  if (!secret) {
    console.warn(
      "Groundhog: no secret configured in chrome.storage.local (key 'groundhogSecret'); " +
        "skipping watched-video request for " + videoId
    );
    return { added: false, reason: "not_configured" };
  }

  await logBreadcrumb("watched_fetch_start", { videoId });
  try {
    const response = await fetch(COMPANION_ORIGIN + VIDEO_WATCHED_PATH, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [SECRET_HEADER]: secret,
      },
      body: JSON.stringify({ video_id: videoId }),
    });
    await logBreadcrumb("watched_fetch_responded", { videoId, status: response.status });
    if (!response.ok) {
      console.warn(
        "Groundhog: companion responded " + response.status + " to watched-video request for " + videoId
      );
      return { added: false, reason: "companion_error_status" };
    }
    // { added: true, video_id, title } or { added: false, video_id, reason }
    // - already the shape the overlay needs, just pass it through.
    return await response.json();
  } catch (err) {
    // The companion may just not be running - fail quietly rather than
    // spamming the console.
    console.warn("Groundhog: watched-video request failed", err);
    await logBreadcrumb("watched_fetch_error", { videoId, name: err && err.name, message: err && err.message });
    return { added: false, reason: "companion_unreachable" };
  }
}

/**
 * Call the companion's GET /videos/{video_id} to check whether a video is
 * already in the corpus - none of /verdict's embedding/similarity-search/
 * Gemini cost. Fails open (`{ found: false }`) on any request problem
 * (no secret configured, companion unreachable, non-2xx status) rather than
 * surfacing its own error state: this is purely an optimization to skip
 * unnecessary verdict checks, so a lookup failure should just fall through
 * to the normal verdict flow instead of blocking on it.
 */
async function lookupVideo(videoId) {
  const secret = await readSecret();
  if (!secret) {
    return { found: false };
  }

  try {
    const response = await fetch(COMPANION_ORIGIN + VIDEO_LOOKUP_PATH_PREFIX + encodeURIComponent(videoId), {
      method: "GET",
      headers: {
        [SECRET_HEADER]: secret,
      },
    });
    if (!response.ok) {
      return { found: false };
    }
    // { found: true, title, watched_at } or { found: false } - already the
    // shape the overlay needs, just pass it through.
    return await response.json();
  } catch (err) {
    // The companion may just not be running - fail open, same as above.
    return { found: false };
  }
}

/**
 * Call the companion's DELETE /videos/{video_id} to remove a video from
 * the corpus entirely (issue #42) - a real delete, not a soft-delete flag,
 * see DECISIONS.md. Same URL prefix as lookupVideo above, just a different
 * HTTP method.
 */
async function removeWatchedVideo(videoId) {
  const secret = await readSecret();
  if (!secret) {
    return { removed: false, reason: "not_configured" };
  }

  try {
    const response = await fetch(COMPANION_ORIGIN + VIDEO_LOOKUP_PATH_PREFIX + encodeURIComponent(videoId), {
      method: "DELETE",
      headers: {
        [SECRET_HEADER]: secret,
      },
    });
    if (!response.ok) {
      return { removed: false, reason: "companion_error_status" };
    }
    // { removed: true } or { removed: false } - already the shape the
    // overlay needs, just pass it through.
    return await response.json();
  } catch (err) {
    // The companion may just not be running - fail quietly, same as
    // postVideoWatched above.
    return { removed: false, reason: "companion_unreachable" };
  }
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (!message) {
    return;
  }
  if (message.type === "GROUNDHOG_VIDEO_LOOKUP" && message.videoId) {
    const tabId = sender && sender.tab ? sender.tab.id : null;
    lookupVideo(message.videoId).then((result) => {
      if (tabId == null) {
        return;
      }
      chrome.tabs
        .sendMessage(tabId, {
          type: "GROUNDHOG_LOOKUP_RESULT",
          videoId: message.videoId,
          result,
        })
        .catch((err) => {
          console.warn("Groundhog: could not deliver lookup result to tab", err);
        });
    });
  }
  if (message.type === "GROUNDHOG_VIDEO_OPENED" && message.videoId) {
    const tabId = sender && sender.tab ? sender.tab.id : null;
    logBreadcrumb("verdict_message_received", { videoId: message.videoId, tabId });
    requestVerdict(message.videoId).then(async (result) => {
      await logBreadcrumb("verdict_settled", {
        videoId: message.videoId,
        tabId,
        hasError: Boolean(result && result.error),
        code: result && result.code,
      });
      if (tabId == null) {
        // No tab to route the result back to. This message only ever comes
        // from the content script (which always runs in a tab), so sender.tab
        // should always be present per the WebExtensions spec - but if a
        // browser's implementation ever fails to populate it, the result
        // (including a perfectly good verdict/error the companion already
        // computed) would otherwise vanish here with zero trace, leaving the
        // overlay stuck on "checking..." forever with no visible cause. Log
        // it loudly rather than silently dropping it, so this is at least
        // diagnosable if it ever happens.
        console.error(
          "Groundhog: no sender.tab on GROUNDHOG_VIDEO_OPENED for video " +
            message.videoId + " - cannot deliver result", sender
        );
        await logBreadcrumb("verdict_no_tab", { videoId: message.videoId });
        return;
      }
      // Routed back as a separate message (rather than a sendResponse to
      // the original GROUNDHOG_VIDEO_OPENED message) because the fetch above
      // can take several seconds (transcript retrieval alone is 2-4s) -
      // content.js's overlay is already showing "checking..." by the time
      // this arrives, driven by GroundhogOverlay.reset() at the point the
      // request was first fired.
      await logBreadcrumb("verdict_send_attempt", { videoId: message.videoId, tabId });
      chrome.tabs
        .sendMessage(tabId, {
          type: "GROUNDHOG_VERDICT_RESULT",
          videoId: message.videoId,
          result,
        })
        .then(() => logBreadcrumb("verdict_send_success", { videoId: message.videoId, tabId }))
        .catch((err) => {
          // The tab may have navigated away or closed before the verdict
          // came back - fail quietly, there's nothing left to update.
          console.warn("Groundhog: could not deliver verdict result to tab", err);
          return logBreadcrumb("verdict_send_fail", { videoId: message.videoId, tabId, message: err && err.message });
        });
    });
  }
  if (message.type === "GROUNDHOG_VIDEO_WATCHED" && message.videoId) {
    const tabId = sender && sender.tab ? sender.tab.id : null;
    logBreadcrumb("watched_message_received", { videoId: message.videoId, tabId });
    postVideoWatched(message.videoId).then(async (result) => {
      await logBreadcrumb("watched_settled", { videoId: message.videoId, tabId, added: result && result.added });
      if (tabId == null) {
        return;
      }
      await logBreadcrumb("watched_send_attempt", { videoId: message.videoId, tabId });
      chrome.tabs
        .sendMessage(tabId, {
          type: "GROUNDHOG_WATCHED_RESULT",
          videoId: message.videoId,
          result,
        })
        .then(() => logBreadcrumb("watched_send_success", { videoId: message.videoId, tabId }))
        .catch((err) => {
          // Same as GROUNDHOG_VERDICT_RESULT above - the tab may have
          // navigated away or closed before this came back.
          console.warn("Groundhog: could not deliver watched result to tab", err);
          return logBreadcrumb("watched_send_fail", { videoId: message.videoId, tabId, message: err && err.message });
        });
    });
  }
  if (message.type === "GROUNDHOG_VIDEO_REMOVE" && message.videoId) {
    const tabId = sender && sender.tab ? sender.tab.id : null;
    removeWatchedVideo(message.videoId).then((result) => {
      if (tabId == null) {
        return;
      }
      chrome.tabs
        .sendMessage(tabId, {
          type: "GROUNDHOG_REMOVE_RESULT",
          videoId: message.videoId,
          result,
        })
        .catch((err) => {
          // Same as the other result-delivery sends above - the tab may
          // have navigated away or closed before this came back.
          console.warn("Groundhog: could not deliver remove result to tab", err);
        });
    });
  }
  if (message.type === "GROUNDHOG_OPEN_OPTIONS") {
    // The background worker always has full chrome.runtime access, unlike
    // the content script the overlay's "Open settings" button lives in -
    // see content.js's GroundhogOverlay.onOpenSettingsClick.
    chrome.runtime.openOptionsPage();
  }
});
