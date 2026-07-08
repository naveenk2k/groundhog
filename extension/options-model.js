/**
 * Pure model-selection logic, kept free of any DOM/chrome.* API so it can be
 * unit-tested directly in Node. Shared by options.js (the model <select>) and
 * background.js (reading the persisted value before building the /verdict
 * request body), so both sides agree on the same default and valid set of
 * tiers without duplicating the strings. Mirrors options-k.js's pattern.
 *
 * companion/verdict.py's `get_verdict` accepts a per-request `model` override
 * (companion/app.py's VerdictRequest.model) that, when omitted, falls back to
 * DEFAULT_MODEL = os.environ.get("GROUNDHOG_GEMINI_MODEL", "gemini-2.5-flash").
 * This module's DEFAULT_MODEL is kept in sync with that hardcoded fallback
 * manually, same as options-k.js's DEFAULT_K.
 */

// Matches companion/verdict.py's DEFAULT_MODEL fallback ("gemini-2.5-flash").
const DEFAULT_MODEL = "gemini-2.5-flash";

// The selectable model tiers, in the order they should appear in the
// options page's <select>. Real Gemini model ID strings, matching
// companion/verdict.py's existing naming convention exactly.
const MODEL_TIERS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"];

/**
 * Resolve an arbitrary value (e.g. straight out of a <select>'s .value, or
 * out of chrome.storage.local, which could in principle hold anything a
 * previous version wrote) to one of the known MODEL_TIERS. Falls back to
 * DEFAULT_MODEL for anything that isn't a recognized tier (missing,
 * undefined, null, or a stale/corrupted string) rather than forwarding
 * garbage to the companion as the `model` field.
 */
function resolveModel(value) {
  if (MODEL_TIERS.includes(value)) {
    return value;
  }
  return DEFAULT_MODEL;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { DEFAULT_MODEL, MODEL_TIERS, resolveModel };
}
