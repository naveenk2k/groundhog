/**
 * Options page controller: loads/saves the shared secret, K slider, and
 * model picker to chrome.storage.local, using options-k.js's
 * DEFAULT_K/clampK and options-model.js's DEFAULT_MODEL/resolveModel so
 * this page and background.js agree on the same defaults and valid values.
 *
 * Keys used (also read directly by background.js's readSecret()/readK()/
 * readModel()):
 *   groundhogSecret - the pasted shared secret, string
 *   groundhogK      - the chosen K, integer in [MIN_K, MAX_K]
 *   groundhogModel  - the chosen model tier, one of options-model.js's MODEL_TIERS
 */

const secretInput = document.getElementById("secret");
const kInput = document.getElementById("k");
const kValueLabel = document.getElementById("k-value");
const modelInput = document.getElementById("model");
const saveButton = document.getElementById("save");
const statusEl = document.getElementById("status");

/** Populate the form from whatever's currently persisted, defaulting K/model if unset. */
async function loadForm() {
  const { groundhogSecret, groundhogK, groundhogModel } = await chrome.storage.local.get([
    "groundhogSecret",
    "groundhogK",
    "groundhogModel",
  ]);
  secretInput.value = groundhogSecret || "";
  const k = clampK(groundhogK);
  kInput.value = String(k);
  kValueLabel.textContent = String(k);
  modelInput.value = resolveModel(groundhogModel);
}

/** Keep the numeric label in sync while dragging, before Save is clicked. */
kInput.addEventListener("input", () => {
  kValueLabel.textContent = String(clampK(kInput.value));
});

let statusTimer = null;

async function save() {
  const secret = secretInput.value.trim();
  const k = clampK(kInput.value);
  const model = resolveModel(modelInput.value);

  await chrome.storage.local.set({
    groundhogSecret: secret,
    groundhogK: k,
    groundhogModel: model,
  });

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
