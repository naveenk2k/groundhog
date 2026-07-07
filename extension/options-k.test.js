/**
 * Tests for options-k.js's clampK (issue #9). Plain Node test using the
 * built-in `node:test`/`node:assert` modules - no test framework dependency
 * to add for a project that otherwise has no JS package.json. Run with:
 *
 *   node --test extension/options-k.test.js
 */

const test = require("node:test");
const assert = require("node:assert");

const { DEFAULT_K, MIN_K, MAX_K, clampK } = require("./options-k.js");

test("constants", () => {
  assert.strictEqual(DEFAULT_K, 5);
  assert.strictEqual(MIN_K, 1);
  assert.strictEqual(MAX_K, 10);
});

test("missing/empty values fall back to DEFAULT_K", () => {
  assert.strictEqual(clampK(undefined), DEFAULT_K);
  assert.strictEqual(clampK(null), DEFAULT_K);
  assert.strictEqual(clampK(""), DEFAULT_K);
});

test("non-numeric garbage falls back to DEFAULT_K", () => {
  assert.strictEqual(clampK("abc"), DEFAULT_K);
  assert.strictEqual(clampK(NaN), DEFAULT_K);
  assert.strictEqual(clampK({}), DEFAULT_K);
});

test("values below MIN_K clamp up to MIN_K", () => {
  assert.strictEqual(clampK(0), MIN_K);
  assert.strictEqual(clampK(-5), MIN_K);
});

test("values above MAX_K clamp down to MAX_K", () => {
  assert.strictEqual(clampK(11), MAX_K);
  assert.strictEqual(clampK(999), MAX_K);
});

test("in-range values pass through unchanged", () => {
  assert.strictEqual(clampK(1), 1);
  assert.strictEqual(clampK(10), 10);
  assert.strictEqual(clampK(7), 7);
});

test("string input (e.g. straight off a range input's .value) is coerced", () => {
  assert.strictEqual(clampK("7"), 7);
  assert.strictEqual(clampK("3.6"), 4); // rounds, doesn't truncate
});
