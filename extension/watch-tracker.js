/**
 * Watch-threshold tracking logic, loaded before content.js in manifest.json
 * so WatchThresholdTracker is available as a global in the content-script
 * context - same pattern as video-id.js.
 *
 * Deliberately kept free of any DOM/chrome.* API so it can be unit-tested
 * directly in Node: it only ever sees plain numbers (currentTime/duration)
 * and a video ID string, and returns a plain boolean. content.js is
 * responsible for the actual `timeupdate` wiring and for calling `reset()`
 * on SPA navigation.
 */

// A video only counts as "seen" once you've watched 70% of it or 5 minutes,
// whichever comes first - see PLAN.md "Corpus policy". Logging on open
// (rather than on crossing this threshold) would count videos bailed on
// seconds in, defeating the whole point of the comparison corpus.
const WATCH_FRACTION_THRESHOLD = 0.7;
const WATCH_SECONDS_CAP = 5 * 60;

/**
 * Compute the number of seconds into a video that counts as "watched",
 * given the video's total duration.
 *
 * `duration` can legitimately be unavailable or not-yet-loaded (e.g.
 * `video.duration` is `NaN` before metadata loads, or `0` for some live/
 * unusual streams) - in that case there's no 70%-of-duration figure to
 * compute, so this falls back to the flat 5-minute cap. That's a
 * conservative choice: watching 5 real minutes still crosses it even if
 * duration metadata never resolves, but nothing fires before then.
 */
function computeWatchThresholdSeconds(duration) {
  if (!Number.isFinite(duration) || duration <= 0) {
    return WATCH_SECONDS_CAP;
  }
  return Math.min(duration * WATCH_FRACTION_THRESHOLD, WATCH_SECONDS_CAP);
}

/**
 * Stateful (but DOM-free) tracker for the "has this video crossed the watch
 * threshold yet" question. One instance is meant to live for the lifetime of
 * the content script and be explicitly `reset()` on every SPA navigation to
 * a new video - see content.js.
 */
class WatchThresholdTracker {
  constructor() {
    this.videoId = null;
    this.fired = false;
  }

  /**
   * Start tracking a (possibly new) video. Must be called on every
   * navigation so `timeupdate` events from the video element YouTube reuses
   * across SPA navigations don't carry over state (currentTime, "already
   * fired") from whatever was playing before.
   */
  reset(videoId) {
    this.videoId = videoId;
    this.fired = false;
  }

  /**
   * Call on every `timeupdate`. Returns `true` exactly once per video - the
   * moment `currentTime` first crosses the watch threshold for `videoId` -
   * and `false` on every other call (before the threshold, after it's
   * already fired, or if `videoId` doesn't match the video currently being
   * tracked).
   *
   * The `videoId` check is a defensive backstop, not the primary reset
   * mechanism: content.js calls `reset()` on navigation, but this guards
   * against a stray `timeupdate` from a previous video's element slipping
   * through before that reset lands.
   */
  checkProgress(videoId, currentTime, duration) {
    if (videoId !== this.videoId) {
      return false;
    }
    if (this.fired) {
      return false;
    }
    if (!Number.isFinite(currentTime)) {
      return false;
    }
    if (currentTime >= computeWatchThresholdSeconds(duration)) {
      this.fired = true;
      return true;
    }
    return false;
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { computeWatchThresholdSeconds, WatchThresholdTracker };
}
