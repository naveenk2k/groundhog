/**
 * Tests for overlay-state.js's state machine (createOverlayState /
 * applyVerdictResult / toggleCollapsed / dismissOverlay). Plain Node test
 * using the built-in `node:test`/`node:assert` modules, same convention as
 * options-k.test.js/overlay.test.js.
 *
 * Run directly: node extension/overlay-state.test.js
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  createOverlayState,
  applyVerdictResult,
  toggleCollapsed,
  dismissOverlay,
} = require("./overlay-state.js");

test("createOverlayState starts checking, not collapsed, not dismissed", () => {
  assert.deepEqual(createOverlayState(), {
    phase: "checking",
    data: null,
    collapsed: false,
    dismissed: false,
  });
});

test("applyVerdictResult with a verdict object moves to phase verdict, data is the verdict as-is", () => {
  const state = createOverlayState();
  const verdict = { novelty: 7, execution: 8, depth: 6, explanation: "e", recommendation: "r" };
  const next = applyVerdictResult(state, verdict);
  assert.equal(next.phase, "verdict");
  assert.equal(next.data, verdict);
});

test("applyVerdictResult with an { error, code } result moves to phase error, data carries both", () => {
  const state = createOverlayState();
  const next = applyVerdictResult(state, { error: "Groundhog took too long to respond.", code: "timeout" });
  assert.equal(next.phase, "error");
  assert.deepEqual(next.data, { message: "Groundhog took too long to respond.", code: "timeout" });
});

test("applyVerdictResult with an error result missing code still carries message, code is undefined", () => {
  const state = createOverlayState();
  const next = applyVerdictResult(state, { error: "some older message with no code" });
  assert.equal(next.phase, "error");
  assert.equal(next.data.message, "some older message with no code");
  assert.equal(next.data.code, undefined);
});

test("applyVerdictResult does not touch collapsed/dismissed", () => {
  let state = createOverlayState();
  state = toggleCollapsed(state);
  state = dismissOverlay(state);
  const next = applyVerdictResult(state, { error: "x", code: "timeout" });
  assert.equal(next.collapsed, true);
  assert.equal(next.dismissed, true);
});

test("toggleCollapsed flips collapsed without touching phase/data/dismissed", () => {
  const state = createOverlayState();
  const next = toggleCollapsed(state);
  assert.equal(next.collapsed, true);
  assert.equal(next.phase, state.phase);
  assert.equal(next.dismissed, state.dismissed);
  assert.equal(toggleCollapsed(next).collapsed, false);
});

test("dismissOverlay sets dismissed without touching phase/data/collapsed", () => {
  const state = createOverlayState();
  const next = dismissOverlay(state);
  assert.equal(next.dismissed, true);
  assert.equal(next.phase, state.phase);
  assert.equal(next.collapsed, state.collapsed);
});
