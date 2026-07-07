/**
 * Content script: detects YouTube watch-page navigation and tells the
 * background service worker which video is open.
 *
 * YouTube is a single-page app - clicking through to a new video does not
 * trigger a full page load, so a content script that only runs once at
 * injection time would fire for the first video and never again. YouTube's
 * own SPA router dispatches a `yt-navigate-finish` event on `document` after
 * every navigation completes (including the very first one), which is the
 * standard hook for this - see PLAN.md / issue #4. Deliberately not using a
 * MutationObserver or URL polling per that issue's design.
 *
 * This script only extracts the video ID and forwards it - it doesn't scrape
 * or parse anything else from the page (see PLAN.md: the companion fetches
 * transcripts itself via yt-dlp, by video ID).
 */

// Track the last video ID we posted for, so a `yt-navigate-finish` that
// doesn't actually change the video (e.g. only `&t=` changed) doesn't fire a
// duplicate request.
let lastPostedVideoId = null;

function handleNavigation() {
  const videoId = extractVideoId(window.location.href);
  if (!videoId) {
    return;
  }
  if (videoId === lastPostedVideoId) {
    return;
  }
  lastPostedVideoId = videoId;

  chrome.runtime.sendMessage({ type: "GROUNDHOG_VIDEO_OPENED", videoId });
}

document.addEventListener("yt-navigate-finish", handleNavigation);
