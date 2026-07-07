/**
 * Content script: detects YouTube watch-page navigation and tells the
 * background service worker which video is open, and tracks playback
 * progress to report when a video has actually been "watched" (issue #7).
 *
 * YouTube is a single-page app - clicking through to a new video does not
 * trigger a full page load, so a content script that only runs once at
 * injection time would fire for the first video and never again. YouTube's
 * own SPA router dispatches a `yt-navigate-finish` event on `document` after
 * every navigation completes (including the very first one), which is the
 * standard hook for this - see PLAN.md / issue #4. Deliberately not using a
 * MutationObserver or URL polling per that issue's design.
 *
 * This script only extracts the video ID and playback progress and forwards
 * them - it doesn't scrape or parse anything else from the page (see
 * PLAN.md: the companion fetches transcripts itself via yt-dlp, by video
 * ID).
 */

// Track the last video ID we posted "opened" for, so a `yt-navigate-finish`
// that doesn't actually change the video (e.g. only `&t=` changed) doesn't
// fire a duplicate request.
let lastPostedVideoId = null;

// Tracks watch-threshold progress (issue #7). One instance for the content
// script's lifetime, explicitly reset on every navigation - see
// handleNavigation below and watch-tracker.js's own docs on why that reset
// matters (YouTube reuses the same <video> element across SPA navigations,
// so `timeupdate` state must not carry over from the previous video).
const watchTracker = new WatchThresholdTracker();

function handleNavigation() {
  const videoId = extractVideoId(window.location.href);

  // Reset watch-threshold tracking for every navigation, even if the video
  // ID is unchanged (e.g. only `&t=` changed) or missing - either way,
  // whatever was being tracked before no longer applies.
  watchTracker.reset(videoId);

  if (!videoId) {
    return;
  }
  if (videoId === lastPostedVideoId) {
    return;
  }
  lastPostedVideoId = videoId;

  chrome.runtime.sendMessage({ type: "GROUNDHOG_VIDEO_OPENED", videoId });
}

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
    chrome.runtime.sendMessage({ type: "GROUNDHOG_VIDEO_WATCHED", videoId });
  }
}

document.addEventListener("yt-navigate-finish", handleNavigation);
document.addEventListener("timeupdate", handleTimeUpdate, true);
