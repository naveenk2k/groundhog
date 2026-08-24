# Auto-collapse the overlay on fullscreen

## Problem

The overlay stays at full size even when a video goes fullscreen, sitting on top of the video itself with no way to see it without exiting fullscreen or manually collapsing it each time.

## Goal

Entering fullscreen collapses the overlay to its existing pill state automatically. Exiting fullscreen expands it back, but only when fullscreen was actually what caused the collapse. A manual collapse from the existing pill button, whether done before or during fullscreen, is never overridden.

## Scope note

This targets the browser's real Fullscreen API (`document.fullscreenElement`), which is what YouTube's own player fullscreen button uses. It does not cover YouTube's separate "theater mode," which expands the player within the page but never enters real fullscreen and so never fires `fullscreenchange`.

## Design

### `extension/overlay-state.js`

- New state field `collapsedByFullscreen: boolean`, added to `createOverlayState`'s initial shape (defaulting to `false`) and to the module's documented state shape comment, alongside the existing `collapsed`/`dismissed` fields.
- New pure function `setFullscreenState(state, isFullscreen)`:
  - `isFullscreen === true`, `state.collapsed === false`, `state.dismissed === false`: collapse it and record that fullscreen did it. Returns `{ ...state, collapsed: true, collapsedByFullscreen: true }`.
  - `isFullscreen === true`, `state.collapsed === true` already (a manual collapse, or already fullscreen-collapsed): no change. Return `state` as is.
  - `isFullscreen === true`, `state.dismissed === true`: no change. A dismissed overlay stays dismissed, matching how every other transition already leaves `dismissed` alone unless that is specifically its job.
  - `isFullscreen === false`, `state.collapsedByFullscreen === true`: expand it back and clear the flag. Returns `{ ...state, collapsed: false, collapsedByFullscreen: false }`.
  - `isFullscreen === false`, `state.collapsedByFullscreen === false`: no change. Either it was never auto-collapsed, or it is collapsed for a reason other than fullscreen (a manual collapse), and stays that way.

### `extension/overlay.js`

- New entry point `GroundhogOverlay.setFullscreen(isFullscreen)`, added alongside the three already documented at the top of the file (`reset`, `setResult`, `setWatchedResult`). Applies `setFullscreenState` to the current state and re-renders, the same shape as how the file's own collapse-pill click handler already applies `toggleCollapsed` and re-renders.

### `extension/content.js`

- A `fullscreenchange` listener, plus the `webkitfullscreenchange` fallback for Safari (this project explicitly supports Safari as a first-class target, and older Safari versions need the prefixed event), added alongside the existing `yt-navigate-finish`/`timeupdate` document-level listeners this file already owns.
- The handler checks `document.fullscreenElement || document.webkitFullscreenElement` and calls `GroundhogOverlay.setFullscreen(Boolean(...))`.

## Testing

- `extension/overlay-state.test.js`: `setFullscreenState` gets direct unit coverage, since it is pure and DOM-free, matching how `toggleCollapsed`/`dismissOverlay` are already tested there. Covers all five branches above: entering fullscreen while expanded, entering while already manually collapsed, entering while dismissed, exiting after a fullscreen-caused collapse, and exiting after a manual collapse (must stay collapsed).
- No test for the `content.js` event-listener wiring itself or the real browser Fullscreen API, consistent with how this file's other event-driven code is not unit-tested (see the rest of `content.js`).
- Manual smoke test required before merge, since no agent can drive a real fullscreen transition: open a video, let it get a verdict, enter fullscreen and confirm the overlay collapses to a pill, exit and confirm it expands back. Then manually collapse it first, enter and exit fullscreen, and confirm it stays collapsed. This also confirms the underlying assumption that YouTube's own fullscreen button really does trigger `fullscreenchange`, which could not be verified live in this session.

## Out of scope

- Theater mode, which is a separate, non-Fullscreen-API expansion state (see Scope note above).
- Any change to the existing manual collapse button or its pure `toggleCollapsed` function.
- A settings toggle to disable this behavior. Not requested, and the manual-collapse-always-wins rule already gives an escape hatch for anyone who does not want it to auto-expand.
