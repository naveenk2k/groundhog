# Fullscreen Auto-Collapse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entering fullscreen on a video collapses the overlay to its existing pill state automatically. Exiting fullscreen expands it back, but only when fullscreen was actually what caused the collapse. A manual collapse (the existing pill button) is never overridden, whether it happened before or during fullscreen.

**Architecture:** A new pure state field and transition function in `overlay-state.js` track whether the current collapse was fullscreen-caused. `content.js` (which already owns all page-level event wiring) listens for the browser's real `fullscreenchange` event and calls a new `overlay.js` entry point that applies the transition and re-renders.

**Tech Stack:** Vanilla JS (`extension/*.js`), Node's built-in `node:test` (matches every existing test file in `extension/`).

## Global Constraints

- Targets the real Fullscreen API (`document.fullscreenElement`) only. YouTube's separate "theater mode" never fires `fullscreenchange` and is explicitly out of scope.
- A manual collapse (via the existing pill button) always wins: it is never auto-expanded by exiting fullscreen, whether the manual collapse happened before entering fullscreen or during it.
- A dismissed overlay is left untouched by fullscreen changes, matching how every other transition in `overlay-state.js` already leaves `dismissed` alone unless that is specifically its job.
- No settings toggle to disable this behavior. Not requested, and the manual-collapse-always-wins rule already gives an escape hatch.
- No test for the `content.js` event-listener wiring or the real browser Fullscreen API, consistent with how this file's other event-driven code is not unit-tested.

---

### Task 1: `setFullscreenState` and its tests

**Files:**
- Modify: `extension/overlay-state.js`
- Modify: `extension/overlay-state.test.js`

**Interfaces:**
- Produces: `setFullscreenState(state, isFullscreen) -> state`, exported alongside the existing functions in `overlay-state.js`'s `module.exports`. Task 2 calls this from a new `overlay.js` entry point.
- Adds `collapsedByFullscreen: boolean` to the state shape `createOverlayState()` returns (default `false`).

- [ ] **Step 1: Write the failing tests**

In `extension/overlay-state.test.js`, update the top-of-file `require` destructure to also pull in `setFullscreenState` (find the existing `const { createOverlayState, applyVerdictResult, ... } = require("./overlay-state.js");` block and add `setFullscreenState` to it).

Find this existing test:

```javascript
test("createOverlayState starts checking, not collapsed, not dismissed, no watch note, not already watched", () => {
  assert.deepEqual(createOverlayState(), {
    phase: "checking",
    data: null,
```

Read the rest of that test in the file (it continues past what's shown here) to see its full expected object, and add `collapsedByFullscreen: false` to that expected object so it still passes once Task 1's Step 2 change lands.

Then add these new tests:

```javascript
test("setFullscreenState collapses an expanded, non-dismissed overlay on entering fullscreen", () => {
  const state = createOverlayState();
  const next = setFullscreenState(state, true);
  assert.equal(next.collapsed, true);
  assert.equal(next.collapsedByFullscreen, true);
});

test("setFullscreenState does not touch an already-collapsed overlay on entering fullscreen", () => {
  let state = createOverlayState();
  state = toggleCollapsed(state); // manual collapse
  const next = setFullscreenState(state, true);
  assert.equal(next.collapsed, true);
  assert.equal(next.collapsedByFullscreen, false);
});

test("setFullscreenState does not touch a dismissed overlay on entering fullscreen", () => {
  let state = createOverlayState();
  state = dismissOverlay(state);
  const next = setFullscreenState(state, true);
  assert.equal(next.collapsed, false);
  assert.equal(next.collapsedByFullscreen, false);
  assert.equal(next.dismissed, true);
});

test("setFullscreenState expands a fullscreen-caused collapse back on exiting fullscreen", () => {
  let state = createOverlayState();
  state = setFullscreenState(state, true);
  const next = setFullscreenState(state, false);
  assert.equal(next.collapsed, false);
  assert.equal(next.collapsedByFullscreen, false);
});

test("setFullscreenState leaves a manual collapse alone on exiting fullscreen", () => {
  let state = createOverlayState();
  state = toggleCollapsed(state); // manual collapse, before ever going fullscreen
  state = setFullscreenState(state, true); // entering fullscreen: no-op, already collapsed
  const next = setFullscreenState(state, false); // exiting: must stay collapsed
  assert.equal(next.collapsed, true);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd extension && npm test`
Expected: the updated `createOverlayState` test fails (missing `collapsedByFullscreen` key), and the five new tests fail with a `TypeError` (`setFullscreenState` doesn't exist yet).

- [ ] **Step 3: Add `collapsedByFullscreen` to the state shape**

In `extension/overlay-state.js`, update the state-shape doc comment at the top of the file:

```
 *     collapsed: boolean,   // true = shown as a small corner badge only
 *     dismissed: boolean,   // true = fully hidden until the next navigation
```

to also document the new field right after `collapsed`:

```
 *     collapsed: boolean,   // true = shown as a small corner badge only
 *     collapsedByFullscreen: boolean,  // true if `collapsed` was set by entering fullscreen (not a manual click; see setFullscreenState)
 *     dismissed: boolean,   // true = fully hidden until the next navigation
```

Then change `createOverlayState`:

```javascript
function createOverlayState() {
  return { phase: "checking", data: null, collapsed: false, dismissed: false, watchNote: null, alreadyWatched: false };
}
```

to:

```javascript
function createOverlayState() {
  return {
    phase: "checking",
    data: null,
    collapsed: false,
    collapsedByFullscreen: false,
    dismissed: false,
    watchNote: null,
    alreadyWatched: false,
  };
}
```

- [ ] **Step 4: Add `setFullscreenState`**

In `extension/overlay-state.js`, add this function directly after `toggleCollapsed`:

```javascript
/**
 * Reacts to the browser's real Fullscreen API changing state (see
 * content.js's fullscreenchange listener). Entering fullscreen collapses an
 * expanded, non-dismissed overlay and records that fullscreen did it.
 * Exiting fullscreen only expands it back if collapsedByFullscreen is true:
 * a manual collapse (via toggleCollapsed, before or during fullscreen)
 * always wins and is never overridden here. A dismissed overlay is left
 * alone entirely, same as every other transition in this file.
 */
function setFullscreenState(state, isFullscreen) {
  if (state.dismissed) {
    return state;
  }
  if (isFullscreen) {
    if (state.collapsed) {
      return state;
    }
    return { ...state, collapsed: true, collapsedByFullscreen: true };
  }
  if (state.collapsedByFullscreen) {
    return { ...state, collapsed: false, collapsedByFullscreen: false };
  }
  return state;
}
```

Then update `module.exports` to include it:

```javascript
    toggleCollapsed,
    setFullscreenState,
    dismissOverlay,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd extension && npm test`
Expected: all tests pass, including the updated `createOverlayState` test and the five new `setFullscreenState` tests.

- [ ] **Step 6: Commit**

```bash
git add extension/overlay-state.js extension/overlay-state.test.js
git commit -m "Add setFullscreenState: collapse-on-fullscreen that never overrides a manual collapse"
```

---

### Task 2: Wire the real Fullscreen API in

**Files:**
- Modify: `extension/overlay.js`
- Modify: `extension/content.js`

**Interfaces:**
- Consumes: `setFullscreenState` (Task 1).
- Produces: `GroundhogOverlay.setFullscreen(isFullscreen)` in `overlay.js`, called by `content.js`'s new `fullscreenchange` listener.

- [ ] **Step 1: Add the `overlay.js` entry point**

In `extension/overlay.js`, find the module's top-of-file doc comment listing the entry points `content.js` calls into:

```
 *   GroundhogOverlay.reset(videoId)               - fresh "checking..." panel
 *   GroundhogOverlay.setResult(videoId, r)        - fill in verdict or error
 *   GroundhogOverlay.setWatchedResult(videoId, r) - corpus-add note (see below)
```

Add a fourth line documenting the new one:

```
 *   GroundhogOverlay.reset(videoId)               - fresh "checking..." panel
 *   GroundhogOverlay.setResult(videoId, r)        - fill in verdict or error
 *   GroundhogOverlay.setWatchedResult(videoId, r) - corpus-add note (see below)
 *   GroundhogOverlay.setFullscreen(isFullscreen)  - auto-collapse/expand on real browser fullscreen
```

Then find where `GroundhogOverlay.reset`/`setResult`/`setWatchedResult` are actually defined further down in the file (read the surrounding code yourself to find the exact spot and match its style: each of these applies a pure `overlay-state.js` function to `state`, then calls `render()`). Add `GroundhogOverlay.setFullscreen` alongside them, following that same pattern:

```javascript
GroundhogOverlay.setFullscreen = (isFullscreen) => {
  state = setFullscreenState(state, isFullscreen);
  render();
};
```

- [ ] **Step 2: Add the `content.js` listener**

In `extension/content.js`, find:

```javascript
document.addEventListener("yt-navigate-finish", handleNavigation);
document.addEventListener("timeupdate", handleTimeUpdate, true);
```

Change it to:

```javascript
document.addEventListener("yt-navigate-finish", handleNavigation);
document.addEventListener("timeupdate", handleTimeUpdate, true);

// Auto-collapses the overlay to a pill on real fullscreen (not YouTube's
// separate theater mode, which never fires this) and expands it back on
// exit, unless the user manually collapsed it (see overlay-state.js's
// setFullscreenState for the exact rule). Both the unprefixed event and the
// Safari-prefixed one are listened for, since this project explicitly
// supports Safari.
function handleFullscreenChange() {
  const isFullscreen = Boolean(document.fullscreenElement || document.webkitFullscreenElement);
  if (typeof GroundhogOverlay.setFullscreen === "function") {
    GroundhogOverlay.setFullscreen(isFullscreen);
  }
}
document.addEventListener("fullscreenchange", handleFullscreenChange);
document.addEventListener("webkitfullscreenchange", handleFullscreenChange);
```

- [ ] **Step 3: Run the extension test suite**

Run: `cd extension && npm test`
Expected: all tests still pass (this step adds no new test file, per the Global Constraints note on why).

- [ ] **Step 4: Manual smoke test**

Reload the extension (Chrome: `chrome://extensions` reload button; Safari: reload then open a fresh tab, per this repo's own documented Safari-reload workaround). This step also confirms an assumption that could not be verified live while writing this plan: that YouTube's own fullscreen button really does trigger the browser's real Fullscreen API. On a video with a verdict already showing:
- Enter fullscreen (the actual fullscreen button, not theater mode). Confirm the overlay collapses to a pill.
- Exit fullscreen. Confirm it expands back to the full panel.
- Manually collapse the overlay (the existing pill button), then enter and exit fullscreen. Confirm it stays collapsed the whole time.
- If YouTube's fullscreen button turns out not to trigger `fullscreenchange` at all (the assumption above turns out wrong), report back: this would mean the detection approach needs to change, not just a tuning tweak.

- [ ] **Step 5: Commit**

```bash
git add extension/overlay.js extension/content.js
git commit -m "Auto-collapse the overlay on real fullscreen, expand back on exit"
```
