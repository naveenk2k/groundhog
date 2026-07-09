/**
 * Tests for overlay.js's classifyOverlayError - the pure, DOM-free mapping
 * from a raw `/verdict` error string (no transcript, companion
 * unreachable/timed out, or a Gemini API failure - see companion/app.py,
 * background.js, and companion/verdict.py) to a short, calm one-line
 * reason for the "can't evaluate" badge.
 *
 * Run directly: node extension/overlay.test.js
 * (also discoverable via `node --test extension/`, same as any node:test file)
 *
 * overlay.js itself is otherwise DOM-heavy (Shadow DOM, chrome.* APIs), so
 * requiring it from Node only works because of the `typeof window ===
 * "undefined"` early-return guard at the top of its IIFE - see that file.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { classifyOverlayError, isSetupError, isRetryableError, cannotMarkWatched } = require("./overlay.js");

test("a recognized code wins over substring matching, even with mismatched/garbage message text", () => {
  // Deliberately mismatched raw message per code, to prove code (not the
  // substring fallback) is what's actually driving the result.
  assert.equal(
    classifyOverlayError("this text matches nothing recognizable", "no_transcript"),
    "No transcript available for this video."
  );
  assert.equal(classifyOverlayError("", "timeout"), "Groundhog took too long to respond.");
  assert.equal(classifyOverlayError("gemini mentioned here", "gemini_busy"), "Gemini is busy right now - try again in a bit.");
  assert.equal(
    classifyOverlayError("irrelevant", "unexpected_verdict_response"),
    "Groundhog got an unexpected response from the verdict service."
  );
  assert.equal(
    classifyOverlayError("irrelevant", "companion_rate_limited"),
    "Groundhog is being rate-limited - try again shortly."
  );
  assert.equal(classifyOverlayError("irrelevant", "not_configured"), "Groundhog isn't set up yet.");
  assert.equal(
    classifyOverlayError("irrelevant", "misconfigured"),
    "Groundhog isn't configured correctly."
  );
});

test("an unrecognized code falls back to substring matching on the message", () => {
  assert.equal(
    classifyOverlayError("companion request timed out after 60s", "some_future_code_this_version_does_not_know"),
    "Groundhog took too long to respond."
  );
});

test("a missing code falls back to substring matching, same as before code existed", () => {
  assert.equal(
    classifyOverlayError("companion request timed out after 60s"),
    "Groundhog took too long to respond."
  );
  assert.equal(classifyOverlayError("companion request timed out after 60s", undefined), "Groundhog took too long to respond.");
});

test("no-transcript errors map to a transcript-specific reason", () => {
  assert.equal(
    classifyOverlayError("no transcript available: no English captions available"),
    "No transcript available for this video."
  );
  // The `reason` half of this string is a raw yt-dlp exception in practice
  // (verbose, technical) - the classifier only needs to recognize the
  // stable "no transcript available" prefix companion/app.py always uses.
  assert.equal(
    classifyOverlayError(
      "no transcript available: unexpected error fetching video info: " +
        "ERROR: [youtube] abc123: Some very long yt-dlp internal traceback-ish string"
    ),
    "No transcript available for this video."
  );
});

test("companion-unreachable errors map to a companion-specific reason", () => {
  assert.equal(
    classifyOverlayError("companion request failed: Failed to fetch"),
    "Couldn't reach the Groundhog companion."
  );
  assert.equal(
    classifyOverlayError("companion request failed: NetworkError when attempting to fetch resource."),
    "Couldn't reach the Groundhog companion."
  );
  assert.equal(
    classifyOverlayError("companion responded with status 500"),
    "Groundhog companion returned an error."
  );
});

test("companion responded with status 429 maps to a distinct rate-limited reason", () => {
  assert.equal(
    classifyOverlayError("companion responded with status 429"),
    "Groundhog is being rate-limited - try again shortly."
  );
  // A generic 5xx should still get the plain "returned an error" reason, not
  // the rate-limited one.
  assert.equal(
    classifyOverlayError("companion responded with status 503"),
    "Groundhog companion returned an error."
  );
});

test("Gemini's own transient overload/rate-limit signal maps to a distinct busy reason", () => {
  assert.equal(
    classifyOverlayError("Gemini is busy right now - try again in a bit."),
    "Gemini is busy right now - try again in a bit."
  );
});

test("unparseable Gemini response maps to a distinct reason from generic unreachability", () => {
  const unparseableReason = classifyOverlayError(
    "Groundhog got an unexpected response from the verdict service."
  );
  assert.equal(unparseableReason, "Groundhog got an unexpected response from the verdict service.");
  assert.notEqual(unparseableReason, classifyOverlayError("Couldn't reach the verdict service."));
});

test("client-side timeout maps to a distinct reason from unreachable", () => {
  const timeoutReason = classifyOverlayError("companion request timed out after 60s");
  assert.equal(timeoutReason, "Groundhog took too long to respond.");
  assert.notEqual(timeoutReason, classifyOverlayError("companion request failed: Failed to fetch"));
});

test("Gemini/API errors map to a verdict-service reason", () => {
  assert.equal(
    classifyOverlayError("Gemini API server error (503): overloaded"),
    "Couldn't reach the verdict service."
  );
  assert.equal(
    classifyOverlayError("Gemini API client error (429): rate limited"),
    "Couldn't reach the verdict service."
  );
  assert.equal(
    classifyOverlayError("could not create Gemini client: no API key"),
    "Couldn't reach the verdict service."
  );
  assert.equal(
    classifyOverlayError("Gemini did not return a parseable verdict"),
    "Couldn't reach the verdict service."
  );
});

test("no-secret-configured maps to a setup-specific reason", () => {
  assert.equal(
    classifyOverlayError("no secret configured in chrome.storage.local (key 'groundhogSecret')"),
    "Groundhog isn't set up yet."
  );
});

// Guards against a real regression: companion/verdict.py and background.js
// now return already-clean, short messages over the wire instead of raw
// exception text, and the classifier's substring checks (e.g. looking for
// "gemini") silently stopped matching once the upstream wording no longer
// contained those keywords.
test("already-clean upstream messages pass through with their intended reason, not the generic fallback", () => {
  assert.equal(
    classifyOverlayError("No transcript available for this video."),
    "No transcript available for this video."
  );
  assert.equal(
    classifyOverlayError("Groundhog took too long to respond."),
    "Groundhog took too long to respond."
  );
  assert.equal(
    classifyOverlayError(
      "Groundhog isn't set up yet - open the extension's options page and paste your secret from .groundhog-secret."
    ),
    "Groundhog isn't set up yet."
  );
  assert.equal(
    classifyOverlayError("Groundhog isn't configured correctly."),
    "Groundhog isn't configured correctly."
  );
  assert.equal(classifyOverlayError("companion request failed"), "Couldn't reach the Groundhog companion.");
  assert.equal(
    classifyOverlayError("Couldn't reach the verdict service."),
    "Couldn't reach the verdict service."
  );
});

test("unrecognized errors fall back to a generic calm message instead of the raw text", () => {
  const raw = "totally unrecognized garbage exception xyz123";
  const result = classifyOverlayError(raw);
  assert.equal(result, "Groundhog couldn't evaluate this video.");
  assert.notEqual(result, raw);
});

test("degrades gracefully on non-string/empty input rather than throwing", () => {
  assert.doesNotThrow(() => classifyOverlayError(null));
  assert.doesNotThrow(() => classifyOverlayError(undefined));
  assert.doesNotThrow(() => classifyOverlayError(""));
  assert.equal(classifyOverlayError(null), "Groundhog couldn't evaluate this video.");
  assert.equal(classifyOverlayError(undefined), "Groundhog couldn't evaluate this video.");
  assert.equal(classifyOverlayError(""), "Groundhog couldn't evaluate this video.");
});

test("every mapped reason is short (fits a one-line badge)", () => {
  const samples = [
    "no transcript available: no English captions available",
    "companion request failed: Failed to fetch",
    "companion request timed out after 60s",
    "companion responded with status 500",
    "Gemini API server error (503): overloaded",
    "no secret configured in chrome.storage.local (key 'groundhogSecret')",
    "something completely unrecognized",
  ];
  for (const raw of samples) {
    const out = classifyOverlayError(raw);
    assert.ok(out.length > 0 && out.length < 60, `unexpectedly long reason for "${raw}": "${out}"`);
  }
});

test("isSetupError: true for not_configured/misconfigured codes, via code not message text", () => {
  assert.equal(isSetupError("irrelevant text", "not_configured"), true);
  assert.equal(isSetupError("irrelevant text", "misconfigured"), true);
});

test("isSetupError: false for every other known code", () => {
  const nonSetupCodes = [
    "no_transcript",
    "timeout",
    "companion_unreachable",
    "companion_rate_limited",
    "companion_error_status",
    "gemini_busy",
    "verdict_service_unreachable",
    "unexpected_verdict_response",
  ];
  for (const code of nonSetupCodes) {
    assert.equal(isSetupError("irrelevant text", code), false, `expected code "${code}" to not be a setup error`);
  }
});

test("isSetupError: falls back to substring matching when code is missing", () => {
  assert.equal(isSetupError("Groundhog isn't set up yet - open the extension's options page."), true);
  assert.equal(isSetupError("Groundhog isn't configured correctly."), true);
  assert.equal(isSetupError("companion request failed"), false);
  assert.equal(isSetupError("companion request timed out after 60s"), false);
});

test("isRetryableError: false for codes where retrying the same video can't help", () => {
  const nonRetryable = ["no_transcript", "not_configured", "misconfigured", "unexpected_verdict_response"];
  for (const code of nonRetryable) {
    assert.equal(isRetryableError("irrelevant", code), false, `expected code "${code}" to not be retryable`);
  }
});

test("isRetryableError: true for transient/retry-worthy codes", () => {
  const retryable = [
    "timeout",
    "companion_unreachable",
    "companion_rate_limited",
    "companion_error_status",
    "gemini_busy",
    "verdict_service_unreachable",
  ];
  for (const code of retryable) {
    assert.equal(isRetryableError("irrelevant", code), true, `expected code "${code}" to be retryable`);
  }
});

test("isRetryableError: falls back to !isSetupError when code is missing/unrecognized", () => {
  assert.equal(isRetryableError("Groundhog isn't set up yet - open the extension's options page."), false);
  assert.equal(isRetryableError("companion request failed"), true);
  assert.equal(isRetryableError("irrelevant", "some_future_code_this_version_does_not_know"), true);
});

test("cannotMarkWatched: true only for no_transcript - add_watched_video needs the same transcript fetch that already failed", () => {
  assert.equal(cannotMarkWatched("no_transcript"), true);
});

test("cannotMarkWatched: false for every other known code, missing code, or unrecognized code", () => {
  const stillWatchable = [
    "timeout",
    "not_configured",
    "misconfigured",
    "companion_unreachable",
    "companion_rate_limited",
    "companion_error_status",
    "gemini_busy",
    "verdict_service_unreachable",
    "unexpected_verdict_response",
    "some_future_code_this_version_does_not_know",
  ];
  for (const code of stillWatchable) {
    assert.equal(cannotMarkWatched(code), false, `expected code "${code}" to still allow Mark as watched`);
  }
  assert.equal(cannotMarkWatched(undefined), false);
  assert.equal(cannotMarkWatched(null), false);
});
