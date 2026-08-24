# Gemini-Busy Message and Inline Model Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vague "Gemini is busy right now" overlay message with a clearer one, and let a user switch Gemini model tiers directly from that error card (updating their saved preference and immediately retrying), instead of requiring a trip to the options page.

**Architecture:** Purely client-side. `companion/verdict.py` already sends `{"error": "...", "code": "gemini_busy"}` unchanged; only `extension/overlay.js`'s client-side message-lookup table and a new gated picker UI change, wired through `extension/content.js`'s existing `chrome.storage.local`/retry plumbing.

**Tech Stack:** Vanilla JS (`extension/*.js`), Node's built-in `node:test` (matches every existing test file in `extension/`).

## Global Constraints

- No backend/companion changes. `companion/verdict.py` keeps returning `"code": "gemini_busy"` exactly as today.
- Message text, exact and final (already run through the humanizer skill and checked for dash-style punctuation per this project's style rule): `"Gemini's overloaded or rate-limited right now. Try again shortly, or switch models below."`
- The model picker must appear only for the `gemini_busy` error, never for other error codes (`no_transcript`, `companion_unreachable`, etc.), where switching models cannot help.
- Selecting a model must both (a) persist to `chrome.storage.local`'s existing `groundhogModel` key (the same key `options.js` already reads/writes, so it becomes the new saved default) and (b) immediately retry the same video, reusing the existing `GroundhogOverlay.onRetryClick` path rather than inventing a second retry mechanism.
- The existing "Retry" button stays, unchanged, alongside the new picker.
- No new test file for DOM rendering or `chrome.*` wiring, consistent with the rest of `overlay.js`/`content.js` not being unit-tested for DOM output or `chrome.*` side effects.

---

### Task 1: Message wording, `isGeminiBusyError`, and their tests

**Files:**
- Modify: `extension/overlay.js`
- Modify: `extension/overlay.test.js`

**Interfaces:**
- Produces: `isGeminiBusyError(raw, code) -> boolean`, exported alongside the existing `classifyOverlayError`/`isSetupError`/`isRetryableError`/`cannotMarkWatched` in `overlay.js`'s `module.exports`. Task 2 calls this directly to gate the picker's visibility.

- [ ] **Step 1: Write the failing tests**

In `extension/overlay.test.js`, find this existing test:

```javascript
test("Gemini's own transient overload/rate-limit signal maps to a distinct busy reason", () => {
  assert.equal(
    classifyOverlayError("Gemini is busy right now - try again in a bit."),
    "Gemini is busy right now - try again in a bit."
  );
});
```

Replace it with (the raw backend message is unchanged, since `companion/verdict.py` isn't being touched; only the expected mapped-to text changes):

```javascript
test("Gemini's own transient overload/rate-limit signal maps to a distinct busy reason", () => {
  assert.equal(
    classifyOverlayError("Gemini is busy right now - try again in a bit."),
    "Gemini's overloaded or rate-limited right now. Try again shortly, or switch models below."
  );
});
```

Then add this new test near it, plus update the top-of-file `require` destructure to also pull in `isGeminiBusyError` (find the existing line importing `classifyOverlayError, isSetupError, isRetryableError, cannotMarkWatched` from `./overlay.js` and add `isGeminiBusyError` to that list):

```javascript
test("isGeminiBusyError: true for the gemini_busy code, regardless of message text", () => {
  assert.equal(isGeminiBusyError("this text matches nothing recognizable", "gemini_busy"), true);
});

test("isGeminiBusyError: false for every other known code", () => {
  assert.equal(isGeminiBusyError("Gemini is busy right now - try again in a bit.", "no_transcript"), false);
  assert.equal(isGeminiBusyError("companion request timed out after 60s", "timeout"), false);
});

test("isGeminiBusyError: falls back to substring matching when code is missing", () => {
  assert.equal(isGeminiBusyError("Gemini is busy right now - try again in a bit."), true);
  assert.equal(isGeminiBusyError("companion request timed out after 60s"), false);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd extension && npm test`
Expected: the updated test fails (old code still returns the old text), and the three new `isGeminiBusyError` tests fail with a `TypeError` (the function doesn't exist yet).

- [ ] **Step 3: Update the message text**

In `extension/overlay.js`, in the `_CODE_TO_REASON` map, change:

```javascript
  gemini_busy: "Gemini is busy right now - try again in a bit.",
```

to:

```javascript
  gemini_busy: "Gemini's overloaded or rate-limited right now. Try again shortly, or switch models below.",
```

Then find this block further down in `classifyOverlayError` (the substring-matching fallback path, used when `code` is missing or unrecognized):

```javascript
  if (msg.includes("is busy right now")) {
    return "Gemini is busy right now - try again in a bit.";
  }
```

Change its return value to match:

```javascript
  if (msg.includes("is busy right now")) {
    return "Gemini's overloaded or rate-limited right now. Try again shortly, or switch models below.";
  }
```

- [ ] **Step 4: Add `isGeminiBusyError`**

In `extension/overlay.js`, add this function directly after `isSetupError` (which ends around the `_UNWATCHABLE_CODES` comment block, right before that block, immediately after `isSetupError`'s closing brace):

```javascript
/**
 * True if this error is specifically Gemini being transiently busy or
 * rate-limited (companion/verdict.py's gemini_busy code): the one error
 * where switching to a different model tier is a real, available fix,
 * since each model tier sits on its own free-tier quota. Same code-first,
 * substring-fallback precedence as isSetupError/isRetryableError above.
 */
function isGeminiBusyError(raw, code) {
  if (code === "gemini_busy") {
    return true;
  }
  if (typeof raw !== "string") {
    return false;
  }
  return raw.toLowerCase().includes("is busy right now");
}
```

Then update the `module.exports` line to include it:

```javascript
  module.exports = { classifyOverlayError, isSetupError, isRetryableError, cannotMarkWatched, isGeminiBusyError };
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd extension && npm test`
Expected: all tests pass, including the updated and new ones.

- [ ] **Step 6: Commit**

```bash
git add extension/overlay.js extension/overlay.test.js
git commit -m "Rewrite the Gemini-busy overlay message and add isGeminiBusyError"
```

---

### Task 2: Inline model picker

**Files:**
- Modify: `extension/manifest.json`
- Modify: `extension/overlay.js`
- Modify: `extension/content.js`

**Interfaces:**
- Consumes: `isGeminiBusyError` (Task 1) and `MODEL_TIERS` (`extension/options-model.js`, the array `["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-3.1-flash-lite"]`), available as a plain top-level `const` global once loaded as a content script, the same sharing pattern `background.js` already uses via `importScripts("options-model.js")`.
- Produces: `GroundhogOverlay.onModelChangeClick(videoId, model)` in `content.js`, called by `overlay.js`'s new picker `<select>`'s `change` handler.

- [ ] **Step 1: Load `options-model.js` as a content script**

In `extension/manifest.json`, find:

```json
      "js": ["debug-log.js", "video-id.js", "watch-tracker.js", "overlay-state.js", "overlay.js", "content.js"],
```

Change it to (adding `options-model.js` right before `overlay.js`, so `MODEL_TIERS` exists as a global by the time `overlay.js` runs):

```json
      "js": ["debug-log.js", "video-id.js", "watch-tracker.js", "overlay-state.js", "options-model.js", "overlay.js", "content.js"],
```

- [ ] **Step 2: Add the picker's CSS**

In `extension/overlay.js`, find the existing `.ghog-cant-evaluate-action:hover` rule:

```css
    .ghog-cant-evaluate-action:hover {
      background: var(--ghog-track);
    }
```

Add this new rule directly after it:

```css
    .ghog-model-picker {
      display: inline-block;
      margin-top: 6px;
      margin-left: 6px;
      padding: 4px 8px;
      font-size: 11px;
      font-weight: 500;
      color: var(--ghog-fg);
      background: var(--ghog-bg);
      border: 1px solid var(--ghog-border);
      border-radius: 999px;
      cursor: pointer;
    }
```

- [ ] **Step 3: Render the picker**

In `extension/overlay.js`, find this existing block (the "Retry" button, inside the `else if (isRetryableError(...))` branch of the error-phase rendering):

```javascript
      } else if (isRetryableError(state.data.message, state.data.code)) {
        const action = document.createElement("button");
        action.className = "ghog-cant-evaluate-action";
        action.textContent = "Retry";
        action.addEventListener("click", () => {
          if (typeof GroundhogOverlay.onRetryClick === "function" && currentVideoId) {
            GroundhogOverlay.onRetryClick(currentVideoId);
          }
        });
        text.appendChild(action);
      }
```

Change it to (adding the picker after the Retry button, inside the same `else if` branch, only when `isGeminiBusyError` is true):

```javascript
      } else if (isRetryableError(state.data.message, state.data.code)) {
        const action = document.createElement("button");
        action.className = "ghog-cant-evaluate-action";
        action.textContent = "Retry";
        action.addEventListener("click", () => {
          if (typeof GroundhogOverlay.onRetryClick === "function" && currentVideoId) {
            GroundhogOverlay.onRetryClick(currentVideoId);
          }
        });
        text.appendChild(action);

        // Model picker: only for gemini_busy (see isGeminiBusyError), the
        // one error where switching models is a real fix, since each tier
        // sits on its own free-tier quota. Picking one both saves it as the
        // new default (content.js writes it to the same chrome.storage.local
        // key options.js uses) and immediately retries.
        if (isGeminiBusyError(state.data.message, state.data.code) && typeof MODEL_TIERS !== "undefined") {
          const picker = document.createElement("select");
          picker.className = "ghog-model-picker";
          picker.title = "Switch model and retry";
          MODEL_TIERS.forEach((tier) => {
            const option = document.createElement("option");
            option.value = tier;
            option.textContent = tier;
            picker.appendChild(option);
          });
          picker.addEventListener("change", () => {
            if (typeof GroundhogOverlay.onModelChangeClick === "function" && currentVideoId) {
              GroundhogOverlay.onModelChangeClick(currentVideoId, picker.value);
            }
          });
          text.appendChild(picker);
        }
      }
```

- [ ] **Step 4: Wire the `content.js` side**

In `extension/content.js`, find the existing `onRetryClick` definition:

```javascript
GroundhogOverlay.onRetryClick = (videoId) => {
  GroundhogOverlay.reset(videoId);
  safeSendMessage({ type: "GROUNDHOG_VIDEO_OPENED", videoId });
};
```

Add this new function directly after it:

```javascript
// Lets the overlay's inline model picker (shown only on gemini_busy errors,
// see overlay.js's isGeminiBusyError) switch the saved model preference and
// immediately retry, instead of requiring a trip to the options page.
// Writes to the same chrome.storage.local key (groundhogModel) options.js
// already reads/writes, so this becomes the new saved default too, not a
// one-off override, then reuses onRetryClick's existing retry path rather
// than inventing a second one.
GroundhogOverlay.onModelChangeClick = (videoId, model) => {
  chrome.storage.local.set({ groundhogModel: model }, () => {
    GroundhogOverlay.onRetryClick(videoId);
  });
};
```

- [ ] **Step 5: Run the extension test suite**

Run: `cd extension && npm test`
Expected: all tests still pass (this step adds no new test file, per the Global Constraints note on why).

- [ ] **Step 6: Manual smoke test**

Reload the extension (Chrome: `chrome://extensions` reload button; Safari: reload then open a fresh tab, per this repo's own documented Safari-reload workaround). Trigger a `gemini_busy` error (the easiest real way: temporarily set a very low `GROUNDHOG_GEMINI_TIMEOUT_SECONDS` or wait for a genuine rate-limit; alternatively, ask the human partner to trigger one manually since this can't be forced deterministically). Confirm:
- The new message text shows, not the old one.
- A model `<select>` appears next to the "Retry" button, populated with the four model tiers.
- Picking a different model closes/refreshes the error card and a fresh check starts.
- Opening the options page afterward shows the newly picked model as the saved preference.
- A non-`gemini_busy` error (e.g. open a video with no transcript, or check the options page's "Debug log" for one already fired) shows no picker.

- [ ] **Step 7: Commit**

```bash
git add extension/manifest.json extension/overlay.js extension/content.js
git commit -m "Add an inline model picker to the gemini_busy error card"
```
