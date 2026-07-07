/**
 * Options page controller (issue #9): loads/saves the shared secret and K
 * slider to chrome.storage.local. Depends on options-k.js (loaded first in
 * options.html) for DEFAULT_K/clampK, the same clamp/default logic
 * background.js uses when reading K back out - see options-k.js's docs on
 * why that's shared rather than duplicated.
 *
 * Keys used (both consumed directly by background.js - see its readSecret()
 * and readK()):
 *   groundhogSecret - the pasted shared secret, string
 *   groundhogK      - the chosen K, integer in [MIN_K, MAX_K]
 */

const secretInput = document.getElementById("secret");
const kInput = document.getElementById("k");
const kValueLabel = document.getElementById("k-value");
const saveButton = document.getElementById("save");
const statusEl = document.getElementById("status");

/** Populate the form from whatever's currently persisted, defaulting K if unset. */
async function loadForm() {
  const { groundhogSecret, groundhogK } = await chrome.storage.local.get([
    "groundhogSecret",
    "groundhogK",
  ]);
  secretInput.value = groundhogSecret || "";
  const k = clampK(groundhogK);
  kInput.value = String(k);
  kValueLabel.textContent = String(k);
}

/** Keep the numeric label in sync while dragging, before Save is clicked. */
kInput.addEventListener("input", () => {
  kValueLabel.textContent = String(clampK(kInput.value));
});

let statusTimer = null;

async function save() {
  const secret = secretInput.value.trim();
  const k = clampK(kInput.value);

  await chrome.storage.local.set({ groundhogSecret: secret, groundhogK: k });

  statusEl.hidden = false;
  if (statusTimer) {
    clearTimeout(statusTimer);
  }
  statusTimer = setTimeout(() => {
    statusEl.hidden = true;
  }, 1500);
}

saveButton.addEventListener("click", save);

loadForm();
