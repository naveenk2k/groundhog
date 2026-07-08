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
  markContextInvalidated,
  markAlreadyWatched,
  setAlreadyWatchedFlag,
  setWatchNote,
  clearWatchNote,
  toggleCollapsed,
  dismissOverlay,
} = require("./overlay-state.js");

test("createOverlayState starts checking, not collapsed, not dismissed, no watch note, not already watched", () => {
  assert.deepEqual(createOverlayState(), {
    phase: "checking",
    data: null,
    collapsed: false,
    dismissed: false,
    watchNote: null,
    alreadyWatched: false,
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

test("setWatchNote sets watchNote without touching phase/data/collapsed/dismissed", () => {
  let state = createOverlayState();
  state = toggleCollapsed(state);
  const note = { kind: "success", message: "Added to your watch history." };
  const next = setWatchNote(state, note);
  assert.equal(next.watchNote, note);
  assert.equal(next.phase, state.phase);
  assert.equal(next.data, state.data);
  assert.equal(next.collapsed, state.collapsed);
  assert.equal(next.dismissed, state.dismissed);
});

test("markContextInvalidated moves to phase stale with null data", () => {
  const next = markContextInvalidated(applyVerdictResult(createOverlayState(), { novelty: 5 }));
  assert.equal(next.phase, "stale");
  assert.equal(next.data, null);
});

test("markAlreadyWatched moves to phase watched, carries info, and sets alreadyWatched", () => {
  const info = { title: "A Video", watched_at: "2026-01-05T10:00:00Z" };
  const next = markAlreadyWatched(createOverlayState(), info);
  assert.equal(next.phase, "watched");
  assert.equal(next.data, info);
  assert.equal(next.alreadyWatched, true);
});

test("markAlreadyWatched defaults data to null when no info is given", () => {
  const next = markAlreadyWatched(createOverlayState(), null);
  assert.equal(next.data, null);
  assert.equal(next.alreadyWatched, true);
});

test("setAlreadyWatchedFlag sets alreadyWatched without touching phase/data", () => {
  const state = applyVerdictResult(createOverlayState(), { novelty: 5 });
  const next = setAlreadyWatchedFlag(state);
  assert.equal(next.alreadyWatched, true);
  assert.equal(next.phase, state.phase);
  assert.equal(next.data, state.data);
});

test("clearWatchNote resets watchNote to null without touching anything else", () => {
  const state = setWatchNote(createOverlayState(), { kind: "failure", message: "Couldn't add this video." });
  const next = clearWatchNote(state);
  assert.equal(next.watchNote, null);
  assert.equal(next.phase, state.phase);
});
