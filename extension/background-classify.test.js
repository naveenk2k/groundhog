/**
 * Tests for background-classify.js's classifyVerdictResponse/
 * classifyVerdictError - the pure, DOM-free/chrome.*-free decision logic
 * extracted from background.js's requestVerdict(). Plain Node test using
 * the built-in `node:test`/`node:assert` modules, same convention as
 * options-k.test.js/overlay.test.js.
 *
 * Run directly: node extension/background-classify.test.js
 * (also discoverable via `node --test extension/`, same as any node:test file)
 *
 * The error strings produced here are asserted to match exactly what
 * overlay.js's classifyOverlayError (see overlay.test.js) expects to
 * receive, since that's the contract requestVerdict() has always upheld.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  VERDICT_TIMEOUT_MS,
  classifyVerdictResponse,
  classifyVerdictError,
} = require("./background-classify.js");

test("VERDICT_TIMEOUT_MS is 60s, matching the documented client-side safety net", () => {
  assert.strictEqual(VERDICT_TIMEOUT_MS, 60000);
});

test("classifyVerdictResponse: ok response returns null (caller should use response.json())", () => {
  assert.strictEqual(classifyVerdictResponse({ ok: true, status: 200 }), null);
});

test("classifyVerdictResponse: non-ok response returns the companion-status error, for various status codes", () => {
  assert.deepStrictEqual(classifyVerdictResponse({ ok: false, status: 500 }), {
    error: "companion responded with status 500",
  });
  assert.deepStrictEqual(classifyVerdictResponse({ ok: false, status: 404 }), {
    error: "companion responded with status 404",
  });
  assert.deepStrictEqual(classifyVerdictResponse({ ok: false, status: 401 }), {
    error: "companion responded with status 401",
  });
  assert.deepStrictEqual(classifyVerdictResponse({ ok: false, status: 503 }), {
    error: "companion responded with status 503",
  });
});

test("classifyVerdictError: AbortError maps to the timeout message, in seconds", () => {
  const abortErr = new Error("aborted");
  abortErr.name = "AbortError";
  assert.deepStrictEqual(classifyVerdictError(abortErr), {
    error: "companion request timed out after 60s",
  });
});

test("classifyVerdictError: any other error maps to the generic failure message", () => {
  assert.deepStrictEqual(classifyVerdictError(new TypeError("Failed to fetch")), {
    error: "companion request failed",
  });
  assert.deepStrictEqual(classifyVerdictError(new Error("NetworkError when attempting to fetch resource.")), {
    error: "companion request failed",
  });
  assert.deepStrictEqual(classifyVerdictError(null), { error: "companion request failed" });
  assert.deepStrictEqual(classifyVerdictError(undefined), { error: "companion request failed" });
});

// Confirms the output of these functions still routes through overlay.js's
// classifyOverlayError to the same user-facing reasons it always has -
// guards against wording drift during the extraction.
test("outputs classify the same as before through overlay.js's classifyOverlayError", () => {
  const { classifyOverlayError } = require("./overlay.js");

  const abortErr = new Error("aborted");
  abortErr.name = "AbortError";
  assert.equal(
    classifyOverlayError(classifyVerdictError(abortErr).error),
    "Groundhog took too long to respond."
  );
  assert.equal(
    classifyOverlayError(classifyVerdictError(new Error("boom")).error),
    "Couldn't reach the Groundhog companion."
  );
  assert.equal(
    classifyOverlayError(classifyVerdictResponse({ ok: false, status: 500 }).error),
    "Groundhog companion returned an error."
  );
});
