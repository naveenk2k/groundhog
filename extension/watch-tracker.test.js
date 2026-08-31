const test = require("node:test");
const assert = require("node:assert/strict");

const { computeWatchProgress } = require("./watch-tracker.js");

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
