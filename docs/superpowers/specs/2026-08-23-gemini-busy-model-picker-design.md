# Clearer Gemini-busy message + inline model picker

## Problem

When Gemini returns a transient 429/503, the overlay shows "Gemini is busy right now - try again in a bit." - accurate but vague enough that a user can't tell what actually happened or what to do about it beyond blindly retrying. Switching to a different model tier is a real, already-available mitigation (Flash/Flash Lite/Pro/etc. sit on separate free-tier quotas), but it currently requires leaving the video, opening the options page, changing the model, and coming back - real friction for something that could be a single click right where the error already is.

## Goal

1. A clearer one-line message for this specific error.
2. A model picker shown only on this error card, that switches your saved model preference and immediately retries with it.

## Message wording

`extension/overlay.js`'s `_CODE_TO_REASON` map already holds this text client-side, keyed by the `gemini_busy` code the companion sends - no backend change needed.

**Before:** `"Gemini is busy right now - try again in a bit."`
**After:** `"Gemini's overloaded or rate-limited right now - try again shortly, or switch models below."`

Run through the humanizer skill and checked against the sibling messages already in the same lookup table (`"Groundhog took too long to respond."`, `"Couldn't reach the verdict service."`) for voice consistency - short, plain, no explanatory asides, no em dashes (plain hyphen, matching the codebase's existing convention throughout).

## Model picker

Shown only when the error is specifically `gemini_busy` - not on unrelated errors (`no_transcript`, `companion_unreachable`, etc.) where switching models can't help. A new `isGeminiBusyError(message, code)` function in `extension/overlay.js` gates this, following the exact pattern `isSetupError`/`isRetryableError` already establish (prefers `code`, falls back to substring matching on `message`).

**UI:** a `<select>` populated from `MODEL_TIERS` (`extension/options-model.js` - `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.5-pro`, `gemini-3.1-flash-lite`, labeled with the raw model ID strings, matching the options page's own `<option>` labels exactly - no new naming scheme invented), rendered alongside the existing "Retry" button rather than replacing it. Both stay available: a 503 (server overloaded) often just needs a few seconds and the same model works fine on retry; a 429 (rate limited on that specific model) is what the picker actually fixes.

**Wiring, keeping the codebase's existing separation of concerns** (`overlay.js` stays DOM-only; `content.js` owns all `chrome.*` calls):
- `extension/manifest.json`'s content script list gains `options-model.js` (loaded before `overlay.js`), so the overlay can read `MODEL_TIERS` directly instead of duplicating the list - the same sharing `background.js` already does via `importScripts("options-model.js")`.
- Selecting a model calls a new `GroundhogOverlay.onModelChangeClick(videoId, model)` hook (mirrors `onRetryClick`/`onOpenSettingsClick`'s existing pattern).
- `content.js` implements it: writes `{ groundhogModel: model }` to `chrome.storage.local` (the same key the options page already reads/writes via `options.js`), then calls the existing `GroundhogOverlay.onRetryClick(videoId)` - so this becomes your new saved default going forward, not a one-off override, and reuses the exact retry path the "Retry" button already exercises rather than inventing a second one.

## Testing

- `extension/overlay.test.js`: `isGeminiBusyError` gets the same kind of test coverage `isSetupError`/`isRetryableError` already have - recognized code wins over substring mismatch, unrecognized/missing code falls back to substring matching, and a non-Gemini-busy error (e.g. `no_transcript`) returns false.
- No DOM-rendering test for the picker itself or the `onModelChangeClick` wiring - consistent with the rest of `overlay.js`/`content.js` not being unit-tested for DOM output or `chrome.*` side effects (see `overlay.test.js`'s own docstring).

## Out of scope

- No change to the companion - the 429/503 -> `gemini_busy` mapping and the message-selection-by-code mechanism already exist; this only changes the client-side text and adds a client-side picker.
- No persistent/always-visible model picker in the normal verdict view - scoped to this one error card, per the earlier design conversation.
- No splitting `gemini_busy` into separate 429-vs-503 codes/messages - one message covers both, since the same two remedies (wait, or switch model) apply to either.
