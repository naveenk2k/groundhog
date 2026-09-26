const test = require("node:test");
const assert = require("node:assert/strict");

const { computeWatchProgress, WatchThresholdTracker } = require("./watch-tracker.js");

test("watch progress fills toward the five-minute cap for long videos", () => {
  assert.deepEqual(computeWatchProgress(150, 900), {
    fraction: 0.5,
    currentSeconds: 150,
    thresholdSeconds: 300,
  });
});

test("watch progress fills toward 70 percent for shorter videos", () => {
  assert.deepEqual(computeWatchProgress(42, 120), {
    fraction: 0.5,
    currentSeconds: 42,
    thresholdSeconds: 84,
  });
});

test("watch progress remains usable before duration metadata is available", () => {
  assert.deepEqual(computeWatchProgress(60, NaN), {
    fraction: 0.2,
    currentSeconds: 60,
    thresholdSeconds: 300,
  });
});

test("rearming the current video after a failed add lets its already-past-threshold position fire again", () => {
  const tracker = new WatchThresholdTracker();
  tracker.reset("video-id");

  assert.equal(tracker.checkProgress("video-id", 301, 900), true);
  assert.equal(tracker.checkProgress("video-id", 302, 900), false);
  assert.equal(tracker.rearm("other-video"), false);
  assert.equal(tracker.checkProgress("video-id", 302, 900), false);

  assert.equal(tracker.rearm("video-id"), true);
  assert.equal(tracker.checkProgress("video-id", 302, 900), true);
});
