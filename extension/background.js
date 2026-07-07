/**
 * Background service worker: does the actual fetch() to the companion.
 *
 * Kept out of the content script deliberately - the content script runs in
 * the YouTube page's isolated world, and cross-origin fetches from that
 * context can get tangled up in the host page's own CSP. The service worker
 * has no such page context, only the extension's own `host_permissions`
 * (scoped to http://127.0.0.1:8787/* - see manifest.json), so it's the
 * right place for outbound requests to the companion to live.
 */

const COMPANION_ORIGIN = "http://127.0.0.1:8787";

// TODO(#endpoint): the companion doesn't have a real ingestion endpoint yet
// as of this issue (#4) - #2/#3/#5 add transcript fetch / corpus / Claude
// scoring on top of the FastAPI app from #1, which currently only exposes
// `/health` (unauthenticated) and a placeholder authenticated `/`. This path
// is a placeholder; update it to match whatever route #2/#3/#5 land with.
const VIDEO_OPENED_PATH = "/videos/opened";

// Header name must match companion/auth.py's SECRET_HEADER exactly
// ("X-Groundhog-Secret") or every request gets a 401.
const SECRET_HEADER = "X-Groundhog-Secret";

// TODO(#9): there is no options page yet (that's a separate issue - "Extension:
// options page for secret paste and K slider"). Until it exists, the secret
// has to be seeded into chrome.storage.local some other way - e.g. from the
// service worker's console in chrome://extensions during manual testing:
//   chrome.storage.local.set({ groundhogSecret: "<value from .groundhog-secret>" })
// Once the options page lands, it should write to this same key so this
// read path doesn't need to change.
async function readSecret() {
  const { groundhogSecret } = await chrome.storage.local.get("groundhogSecret");
  return groundhogSecret || null;
}

async function postVideoOpened(videoId) {
  const secret = await readSecret();
  if (!secret) {
    console.warn(
      "Groundhog: no secret configured in chrome.storage.local (key 'groundhogSecret'); " +
        "skipping companion request for video " + videoId
    );
    return;
  }

  try {
    const response = await fetch(COMPANION_ORIGIN + VIDEO_OPENED_PATH, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [SECRET_HEADER]: secret,
      },
      body: JSON.stringify({ video_id: videoId }),
    });
    if (!response.ok) {
      console.warn(
        "Groundhog: companion responded " + response.status + " for video " + videoId
      );
    }
  } catch (err) {
    // The companion may simply not be running - fail quietly rather than
    // spamming the console on every video open. See issue #10 (graceful
    // failure / "can't evaluate" badge) for the eventual user-facing story.
    console.warn("Groundhog: companion request failed", err);
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message && message.type === "GROUNDHOG_VIDEO_OPENED" && message.videoId) {
    postVideoOpened(message.videoId);
  }
});
