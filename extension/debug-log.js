/**
 * Persisted breadcrumb log for diagnosing background-worker lifecycle bugs
 * that console.log can't help with - Safari's Develop menu shows this
 * extension's MV3 service worker as "not loaded" moments after a request,
 * and if the worker is actually being torn down mid-request (the leading
 * theory for why verdict/watched results sometimes never reach the tab),
 * whatever it would have logged to console dies with it, unobserved.
 *
 * Writing each step to chrome.storage.local instead means every breadcrumb
 * up to the moment of death survives - readable later from any other
 * extension page (options.html's debug section, currently) regardless of
 * whether the worker that wrote it is still around by the time you look.
 *
 * Shared between background.js (writes, via importScripts) and
 * options.html/options.js (reads/clears, via a <script> tag) - same
 * loading pattern as options-k.js/options-model.js.
 */

const DEBUG_LOG_KEY = "groundhogDebugLog";
const DEBUG_LOG_MAX_ENTRIES = 100;

/** Append one breadcrumb - `event` a short machine-readable tag, `details` a plain-object payload (video ID, tab ID, status, etc). */
async function logBreadcrumb(event, details) {
  const { [DEBUG_LOG_KEY]: existing } = await chrome.storage.local.get(DEBUG_LOG_KEY);
  const log = Array.isArray(existing) ? existing : [];
  log.push({ t: Date.now(), event, details: details || {} });
  while (log.length > DEBUG_LOG_MAX_ENTRIES) {
    log.shift();
  }
  await chrome.storage.local.set({ [DEBUG_LOG_KEY]: log });
}

/** Read the full breadcrumb log, oldest first. */
async function readDebugLog() {
  const { [DEBUG_LOG_KEY]: existing } = await chrome.storage.local.get(DEBUG_LOG_KEY);
  return Array.isArray(existing) ? existing : [];
}

/** Wipe the log - options.js's "Clear" button. */
async function clearDebugLog() {
  await chrome.storage.local.remove(DEBUG_LOG_KEY);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { DEBUG_LOG_KEY, DEBUG_LOG_MAX_ENTRIES, logBreadcrumb, readDebugLog, clearDebugLog };
}
