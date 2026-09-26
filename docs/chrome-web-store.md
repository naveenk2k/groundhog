# Chrome Web Store release

Groundhog's browser extension is plain Manifest V3 JavaScript. It does not need a compile or bundle step. The release command follows the manifest's runtime references and packages only the files Chrome needs.

## Build the upload ZIP

From the repository root:

```sh
python3 scripts/package_extension.py
```

Or, from `extension/`:

```sh
npm run package
```

Both commands create `extension/dist/groundhog-extension-<version>.zip` and validate it before reporting success. The ZIP is deterministic: unchanged source files produce the same archive bytes. `manifest.json` is at the archive root, and tests, `package.json`, development files, local data, and secrets are not included.

To check an archive again before uploading it:

```sh
python3 scripts/package_extension.py --check extension/dist/groundhog-extension-0.1.0.zip
```

## Test the exact package locally

These browser steps are manual because Chrome must load the extracted release package and a person must exercise the UI.

1. Build the ZIP.
2. Extract it into a new temporary directory. Do not point Chrome at the source `extension/` directory for this check.
3. Open `chrome://extensions`, turn on **Developer mode**, and click **Load unpacked**.
4. Select the directory containing the extracted `manifest.json`.
5. Start the Groundhog companion and configure the shared secret in the extension's options.
6. Open a YouTube watch page. Confirm that the overlay appears, a verdict completes, the model picker works, and **Mark as watched** updates the state.
7. Watch a video past the threshold and confirm it is added once. Revisit it and confirm there is no duplicate “Added to watch history” notice.
8. Open a transcript from the overlay and confirm the transcript page loads.
9. Check the extension's service-worker console and the options-page debug log for errors.

Record the Chrome version and test result on the release issue. This satisfies the unpacked-extension check in issue #53; it cannot be automated faithfully without controlling a full Chrome profile and the local companion.

## Store listing draft

### Name

Groundhog

### Short description

Check a YouTube video against your watch history before spending time on it.

### Detailed description

Groundhog helps you decide whether a YouTube video is worth watching. On a YouTube watch page, it compares the video with videos you have already watched and shows a compact verdict covering novelty, execution, and depth.

Your watch history stays in the Groundhog companion running on your Mac. The extension connects only to that local companion. Groundhog can add a video to your history after you watch 70% or five minutes, whichever comes first. You can also add or remove a video yourself, view its transcript, choose the Gemini model used for a verdict, and change how many similar videos are compared.

Groundhog requires the separately installed Groundhog companion, a Gemini API key, and access to YouTube transcripts. It does not work as a standalone extension.

### Category

Productivity

### Language

English

## Permission justifications

Use these answers in the Chrome Web Store dashboard.

**`storage`**

Stores the companion's shared secret, the selected Gemini model, the comparison-count setting, and a small troubleshooting log in `chrome.storage.local`. These values let the extension authenticate to the local companion and retain the user's settings.

**Host access to `http://127.0.0.1:8787/*`**

Connects to the Groundhog companion installed on the user's own Mac. The extension requests verdicts, reads transcript progress, and adds, looks up, or removes entries in the user's local watch history. No other network host is available to the extension.

**Content script on `*://www.youtube.com/watch*`**

Reads the current YouTube video ID and playback progress, displays the Groundhog overlay, and detects when the user reaches the watch-history threshold. It does not run on other sites or other YouTube pages.

**Remote code**

No. All extension JavaScript is included in the uploaded package. The extension does not download or execute remote code.

**Single purpose**

Groundhog checks the current YouTube video against the user's watch history and lets the user maintain that history while watching.

## Privacy disclosure draft

The dashboard choices and published privacy policy must match the released companion. Recheck them whenever its data flow changes.

### Data handled

- **Web history:** YouTube video IDs and the resulting local watched-video records.
- **User activity:** Playback progress used in the tab to determine when a video reaches the watched threshold.
- **Website content:** YouTube transcript text used by the companion to compare videos and create a verdict.
- **Authentication information:** A randomly generated secret stored locally so the extension can authenticate to the companion on `127.0.0.1`.
- **Extension settings and diagnostics:** Model choice, comparison count, and a small local debug log.

### How the data is used

- App functionality: compare the current video with the user's watch history, show transcript progress, and maintain that history.
- The companion sends video transcripts and relevant watch-history context to Google's Gemini API when generating a verdict. This transmission happens outside the extension, using the Gemini API key supplied by the user.
- Data is not sold, used for advertising, used for credit decisions, or transferred for purposes unrelated to Groundhog's single purpose.
- Extension settings, the shared secret, and diagnostics stay in `chrome.storage.local`.
- Watch history and cached transcripts are stored by the companion on the user's Mac.

The repository's [`PRIVACY.md`](../PRIVACY.md) is the publishable policy draft.
Review it before submission and use its public GitHub URL in the dashboard. Do
not claim that all data stays on the device: transcript and comparison context
leave the device when the companion calls Gemini.

## Reviewer instructions draft

Groundhog requires its local macOS companion. The extension will show a setup error if the companion is not running or the shared secret is missing.

1. Install the companion from **[PUBLIC RELEASE URL — add before submission]** and follow its setup instructions, including adding a Gemini API key.
2. Confirm `http://127.0.0.1:8787/health` returns a healthy response.
3. Open the Groundhog extension options. Paste the shared secret created by the companion and click **Save**.
4. Open **[REVIEWER YOUTUBE URL — choose a stable public video]**.
5. The Groundhog overlay appears in the upper-right corner and begins checking the video. The first result may take up to a minute while the transcript and local model load.
6. Use the model menu to retry with another model. Click **Mark as watched**, then **Remove from watch history**, to test local-history controls.
7. Click the transcript action to open the transcript page.

Provide the reviewer with a working installation URL and any test-only credentials through the private reviewer-notes field, never in the public listing or upload ZIP.

## Manual submission checklist

The following work happens in Google's dashboards and cannot be completed by the packaging command:

- [ ] Register the Chrome Web Store developer account and pay Google's one-time registration fee.
- [ ] Choose the publisher name and verify its contact email.
- [ ] Create the store item and upload the validated ZIP.
- [ ] Upload a 128×128 store icon. This is a separate listing asset, even though the extension package contains its own icon.
- [ ] Capture and upload at least one 1280×800 screenshot. Remove personal video and watch-history data first.
- [ ] Create and upload the required 440×280 small promotional tile.
- [ ] Add a short YouTube demonstration video if the dashboard requires one for the listing.
- [ ] Optionally create a 1400×560 marquee promotional tile.
- [ ] Paste and proofread the listing copy, permission justifications, privacy answers, and reviewer instructions.
- [ ] Publish the privacy policy and enter its public HTTPS URL.
- [ ] Replace every bracketed placeholder in the reviewer instructions.
- [ ] Complete the unpacked-package test above and record the result on the release issue.
- [ ] Select the initial visibility and distribution regions.
- [ ] Submit the item for review and respond to any reviewer questions.

## Release checklist

The source and Chrome Web Store versions must move together.

1. Update `version` in `extension/manifest.json`. Chrome versions contain one to four dot-separated integers, each from 0 to 65535.
2. Run the JavaScript and packaging tests:

   ```sh
   cd extension
   npm test
   cd ..
   python3 -m unittest discover -s scripts -p 'test_*.py'
   ```

3. Build the ZIP and test that exact package using the unpacked-extension steps.
4. Commit the version change and release notes.
5. Create the matching GitHub release according to the project's release process.
6. Upload the ZIP in the Chrome Web Store dashboard and submit the update.
