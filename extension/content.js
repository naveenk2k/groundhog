/**
 * Content script: detects YouTube watch-page navigation and tells the
 * background service worker which video is open, and tracks playback
 * progress to report when a video has actually been "watched".
 *
 * YouTube is a single-page app - clicking through to a new video does not
 * trigger a full page load, so a content script that only runs once at
 * injection time would fire for the first video and never again. YouTube's
 * own SPA router dispatches a `yt-navigate-finish` event on `document` after
 * every navigation completes (including the very first one), which is the
 * standard hook for this - not a MutationObserver or URL polling.
 *
 * This script only extracts the video ID and playback progress and forwards
 * them - it doesn't scrape or parse anything else from the page (the
 * companion fetches transcripts itself via yt-dlp, by video ID).
 */

// Track the last video ID we posted "opened" for, so a `yt-navigate-finish`
// that doesn't actually change the video (e.g. only `&t=` changed) doesn't
// fire a duplicate request.
let lastPostedVideoId = null;

// One random ID per content-script injection - purely diagnostic (see the
// breadcrumbs in the onMessage listener below). Safari has a documented bug
// class where a content script from before a page reload can keep running
// as a "zombie" - its chrome.runtime.onMessage listener stays registered
// and can still receive/resolve messages even though its `document` is a
// disconnected leftover from the previous page load, not the one actually
// visible. If a result ever gets marked "delivered" by background.js (see
// background.js's verdict_send_success breadcrumb) but the overlay never
// visibly updates, comparing CONTENT_INSTANCE_ID across breadcrumbs is how
// to tell whether a zombie instance handled it instead of the live one.
const CONTENT_INSTANCE_ID = Math.random().toString(36).slice(2, 10);
logBreadcrumb("content_script_loaded", { instanceId: CONTENT_INSTANCE_ID, href: window.location.href });

// One instance for the content script's lifetime, explicitly reset on every
// navigation - see handleNavigation below and watch-tracker.js's own docs on
// why that reset matters (YouTube reuses the same <video> element across SPA
// navigations, so `timeupdate` state must not carry over from the previous
// video).
const watchTracker = new WatchThresholdTracker();

// True once GroundhogOverlay.showContextInvalidated() has been called, so
// safeSendMessage below doesn't keep re-triggering it (and re-rendering the
// overlay into the same "stale" state) on every later call in the same
// dead tab.
let contextInvalidatedShown = false;

/**
 * chrome.runtime.id reads as `undefined` once this content script's
 * extension context has been invalidated (extension reloaded/updated while
 * this tab was already open) - the same signal chrome.runtime.getManifest()
 * relies on internally. Checked proactively on every navigation (see
 * handleNavigation) rather than only reactively after a sendMessage call
 * actually throws, so the user gets a clear signal as soon as possible.
 */
function isExtensionContextValid() {
  try {
    return Boolean(chrome.runtime && chrome.runtime.id);
  } catch (err) {
    return false;
  }
}

/**
 * chrome.runtime.sendMessage throws synchronously with this exact message
 * once the extension context is invalidated (confirmed via Chrome's own
 * documented behavior and widely-reported extension bug threads - it is
 * not a rejected-promise-only failure mode, so a bare try/catch around the
 * call is required, not just a .catch()).
 */
function isContextInvalidatedError(err) {
  return Boolean(err && typeof err.message === "string" && err.message.includes("Extension context invalidated"));
}

function showContextInvalidatedOnce() {
  if (contextInvalidatedShown) {
    return;
  }
  contextInvalidatedShown = true;
  GroundhogOverlay.showContextInvalidated();
}

/**
 * Wraps every chrome.runtime.sendMessage call in this file so a stale tab
 * (see isExtensionContextValid above) surfaces a clear, calm "needs a
 * refresh" overlay state instead of an uncaught "Extension context
 * invalidated" console error and a silently-stuck request. Safe to call
 * exactly like chrome.runtime.sendMessage itself - callers that don't care
 * about the result (fire-and-forget messages) can ignore the return value.
 */
function safeSendMessage(message) {
  if (!isExtensionContextValid()) {
    showContextInvalidatedOnce();
    return;
  }
  try {
    const result = chrome.runtime.sendMessage(message);
    if (result && typeof result.catch === "function") {
      result.catch((err) => {
        if (isContextInvalidatedError(err)) {
          showContextInvalidatedOnce();
        }
      });
    }
    return result;
  } catch (err) {
    if (isContextInvalidatedError(err)) {
      showContextInvalidatedOnce();
      return;
    }
    throw err;
  }
}

// Lets the overlay's "Open settings" button (setup-shaped errors only) open
// the extension's options page without overlay.js needing chrome.* access
// itself - content scripts may not have every chrome.runtime method
// background.js does, so this routes through a message to the background
// worker instead of calling chrome.runtime.openOptionsPage() directly here.
GroundhogOverlay.onOpenSettingsClick = () => {
  safeSendMessage({ type: "GROUNDHOG_OPEN_OPTIONS" });
};

// Lets the overlay's "Mark as watched" button send the same
// GROUNDHOG_VIDEO_WATCHED message the automatic watch-threshold path
// (handleTimeUpdate below) sends - background.js's postVideoWatched and its
// corpus.insert_video upsert-by-video_id behavior don't distinguish who
// triggered the add, so both paths can safely share one handler.
GroundhogOverlay.onMarkWatchedClick = (videoId) => {
  safeSendMessage({ type: "GROUNDHOG_VIDEO_WATCHED", videoId });
};

// Lets the overlay's "Retry" button (retry-worthy errors only - see
// overlay.js's isRetryableError) re-fire a fresh verdict check for the same
// video. Deliberately bypasses lastPostedVideoId below - that dedupe exists
// to skip no-op navigations (e.g. only `&t=` changed), not to block an
// explicit user-initiated retry for the video already on screen.
GroundhogOverlay.onRetryClick = (videoId) => {
  GroundhogOverlay.reset(videoId);
  safeSendMessage({ type: "GROUNDHOG_VIDEO_OPENED", videoId });
};

function handleNavigation() {
  // Checked proactively here (every SPA navigation), not only reactively
  // after a sendMessage call throws below - a reload/update can happen at
  // any time while this tab sits idle on a watch page, and this is the
  // most frequent hook available to notice it without waiting for the next
  // watch-threshold crossing, which would otherwise fail to add the video
  // with no visible cause at all.
  if (!isExtensionContextValid()) {
    showContextInvalidatedOnce();
    return;
  }

  const videoId = extractVideoId(window.location.href);

  // Reset watch-threshold tracking for every navigation, even if the video
  // ID is unchanged (e.g. only `&t=` changed) or missing - either way,
  // whatever was being tracked before no longer applies.
  watchTracker.reset(videoId);

  if (!videoId) {
    // Navigated away from a watch page entirely (home, search, channel,
    // etc.). Tear down the overlay so nothing stale (a verdict,
    // "checking...", a "can't evaluate" badge) lingers on a page that isn't
    // a watch page anymore, and forget the last-posted video ID: without
    // this, navigating away and back to the *same* video later would be
    // silently skipped as a no-op and leave the overlay torn down forever.
    lastPostedVideoId = null;
    GroundhogOverlay.teardown();
    return;
  }
  if (videoId === lastPostedVideoId) {
    return;
  }
  lastPostedVideoId = videoId;

  // Show "checking..." immediately, in lockstep with the lookup firing -
  // the overlay must not wait for a response to appear at all.
  GroundhogOverlay.reset(videoId);

  // Check the corpus for this video before ever requesting a verdict - if
  // it's already been watched (auto-added or via "Mark as watched", see
  // GROUNDHOG_LOOKUP_RESULT below), there's nothing to judge and no point
  // spending a Gemini call on it.
  safeSendMessage({ type: "GROUNDHOG_VIDEO_LOOKUP", videoId });
}

/**
 * background.js's /verdict response (or a synthesized error, e.g. companion
 * unreachable) comes back here as a runtime message rather than a direct
 * response to the sendMessage above, because the fetch to the companion
 * (2-4s+ for transcript retrieval alone, see PLAN.md) happens entirely in
 * the background worker - see background.js's requestVerdict.
 */
chrome.runtime.onMessage.addListener((message) => {
  if (!message) {
    return;
  }
  if (message.type === "GROUNDHOG_VERDICT_RESULT" || message.type === "GROUNDHOG_WATCHED_RESULT") {
    // See CONTENT_INSTANCE_ID's docs above - document.hidden/visibilityState
    // and whether the overlay host is actually attached distinguish a live,
    // on-screen context from a zombie leftover receiving this instead.
    logBreadcrumb("content_message_received", {
      instanceId: CONTENT_INSTANCE_ID,
      type: message.type,
      videoId: message.videoId,
      lastPostedVideoId,
      hidden: document.hidden,
      visibilityState: document.visibilityState,
      overlayHostAttached: Boolean(document.getElementById("groundhog-overlay-host")),
      href: window.location.href,
    });
  }
  if (message.type === "GROUNDHOG_VERDICT_RESULT") {
    GroundhogOverlay.setResult(message.videoId, message.result);
  }
  if (message.type === "GROUNDHOG_WATCHED_RESULT") {
    GroundhogOverlay.setWatchedResult(message.videoId, message.result);
  }
  if (message.type === "GROUNDHOG_LOOKUP_RESULT") {
    if (message.result && message.result.found) {
      GroundhogOverlay.showAlreadyWatched(message.videoId, message.result);
    } else {
      // Not in the corpus - proceed with the real verdict check now,
      // exactly the request handleNavigation used to fire directly.
      safeSendMessage({ type: "GROUNDHOG_VIDEO_OPENED", videoId: message.videoId });
    }
  }
});

/**
 * Handle `timeupdate` events from the page's video element, delegated at
 * `document` in the capture phase so it works no matter which specific
 * <video> element is currently playing (YouTube reuses the same element
 * across SPA navigations, but capture-phase delegation is one less thing to
 * get wrong if that ever changes). `timeupdate` doesn't bubble, but the
 * capture phase still reaches it from an ancestor listener.
 */
function handleTimeUpdate(event) {
  const video = event.target;
  if (!video || typeof video.currentTime !== "number") {
    return;
  }

  const videoId = extractVideoId(window.location.href);
  if (!videoId) {
    return;
  }

  const crossedThreshold = watchTracker.checkProgress(
    videoId,
    video.currentTime,
    video.duration
  );
  if (crossedThreshold) {
    safeSendMessage({ type: "GROUNDHOG_VIDEO_WATCHED", videoId });
  }
}

document.addEventListener("yt-navigate-finish", handleNavigation);
document.addEventListener("timeupdate", handleTimeUpdate, true);
