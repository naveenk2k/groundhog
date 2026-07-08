/**
 * Tests for options-model.js's resolveModel. Plain Node test using the
 * built-in `node:test`/`node:assert` modules, matching options-k.test.js's
 * exact style. Run with:
 *
 *   node --test extension/options-model.test.js
 */

const test = require("node:test");
const assert = require("node:assert");

const { DEFAULT_MODEL, MODEL_TIERS, resolveModel } = require("./options-model.js");

test("constants", () => {
  assert.strictEqual(DEFAULT_MODEL, "gemini-2.5-flash");
  assert.deepStrictEqual(MODEL_TIERS, [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
  ]);
});

test("known tiers pass through unchanged", () => {
  for (const tier of MODEL_TIERS) {
    assert.strictEqual(resolveModel(tier), tier);
  }
});

test("missing/empty values fall back to DEFAULT_MODEL", () => {
  assert.strictEqual(resolveModel(undefined), DEFAULT_MODEL);
  assert.strictEqual(resolveModel(null), DEFAULT_MODEL);
  assert.strictEqual(resolveModel(""), DEFAULT_MODEL);
});

test("unrecognized/corrupted values fall back to DEFAULT_MODEL", () => {
  assert.strictEqual(resolveModel("gemini-1.0-pro"), DEFAULT_MODEL);
  assert.strictEqual(resolveModel("not-a-model"), DEFAULT_MODEL);
  assert.strictEqual(resolveModel(123), DEFAULT_MODEL);
  assert.strictEqual(resolveModel({}), DEFAULT_MODEL);
});
