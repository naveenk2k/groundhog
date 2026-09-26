# Releasing Groundhog

Groundhog has two separately installed parts: the Python companion distributed
through Homebrew and the Chrome extension distributed through the Chrome Web
Store. They use one release version even though users install them separately.

The first releasable version is `0.1.0`. Groundhog supports macOS on Apple
Silicon (`arm64`) and Intel (`x86_64`). The release artifacts contain Python
and JavaScript source rather than architecture-specific binaries, so the same
artifacts serve both architectures.

## Version contract

The repository's root `VERSION` file is the source of truth. It contains a
stable three-part version such as `0.1.0`, followed by one newline. Each part
must be an integer from 0 through 65535 with no leading zeroes. Pre-release and
build suffixes are not allowed because the same value must be valid in the
Chrome extension manifest.

Every release must use that value in these places:

- `VERSION`: `0.1.0`
- `extension/manifest.json`: `"version": "0.1.0"`
- Git tag: `v0.1.0`
- GitHub release: `v0.1.0`
- Homebrew formula: `version "0.1.0"`

The companion does not keep a second version in Python package metadata.
Release and packaging tools read `VERSION` instead.

## Release artifacts

A GitHub release for version `0.1.0` contains these files:

| File | Required contents |
| --- | --- |
| `groundhog-companion-0.1.0.tar.gz` | One top-level `groundhog-companion-0.1.0/` directory containing `VERSION`, `companion/`, `requirements.txt`, `README.md`, and `LICENSE`. It must not contain the extension, tests, credentials, local data, caches, logs, or a virtual environment. |
| `groundhog-extension-0.1.0.zip` | The Chrome Web Store upload. `manifest.json` is at the archive root, alongside only the runtime files referenced by the manifest. Tests, development files, credentials, and local data are excluded. |
| `SHA256SUMS` | SHA-256 entries for both archives, using their exact filenames. |

The Homebrew formula downloads the companion archive from the matching GitHub
release and pins its SHA-256 from `SHA256SUMS`. The Chrome Web Store receives
the exact extension ZIP attached to that release. A release must not rebuild or
substitute either archive after publication; publish a new patch version
instead.

## Public rollout plan

The first release has a few one-time account and trust decisions that should
not be hidden inside automation. Later releases reuse those decisions and are
mostly automatic.

| Step | Who does it | Automation and tracking |
| --- | --- | --- |
| 1. Confirm the release version and supported architectures. | Automated | `scripts/validate_version.py`; issue #52 |
| 2. Build and validate the companion archive. | Automated | `scripts/package_companion.py`; issue #52 |
| 3. Build and validate the Chrome Web Store ZIP. | Automated | `scripts/package_extension.py`; issue #53 |
| 4. Run companion, extension, release-tooling, and Homebrew-template tests. | Automated | CI and the release workflow; issues #52-#55 |
| 5. Test the exact extension ZIP in a real Chrome profile against a running companion. | Manual | Chrome UI and YouTube behavior need a person; issue #53 |
| 6. Create and push `vMAJOR.MINOR.PATCH`. | Manual release approval | Pushing the tag is the deliberate release trigger. The workflow performs the remaining GitHub work; issue #55 |
| 7. Publish immutable archives, checksums, and release notes on GitHub. | Automated | `.github/workflows/release.yml`; issue #55 |
| 8. Create the public `naveenk2k/homebrew-tap` repository and choose its protection rules. | Manual, one time | This changes public account state; issue #57 |
| 9. Add a least-privilege `HOMEBREW_TAP_TOKEN` repository secret and install the tap's dispatch workflow. | Manual, one time | The release workflow then notifies the tap automatically; issues #55 and #57 |
| 10. Generate the first formula's Python resources, render it with the release checksum, and run `brew style`, `brew audit`, `brew install`, `brew test`, and `brew services` checks. | Automated commands, manually approved first publication | The scaffold is under `packaging/homebrew/`; issue #57 |
| 11. Register the Chrome Web Store publisher, enable two-step verification, accept its agreement, and pay its one-time fee. | Manual, one time | Google requires the account holder; issue #56 |
| 12. Publish a privacy policy and supply store screenshots, promotional art, contact details, distribution choices, and declarations. | Manual review with prepared copy | Drafts and required sizes are in `docs/chrome-web-store.md`; issue #56 |
| 13. Upload the released ZIP, use deferred publishing, and submit it for review. | Manual for the first release | Later uploads can use the Chrome Web Store API after OAuth or service-account setup; issue #56 |
| 14. Install both public packages on a clean Mac and test setup, verdicts, watch history, restart, upgrade, uninstall, and rollback. | Manual end-to-end verification | Issue #58 |
| 15. Update public README instructions and publish the staged extension. | Manual final approval | Issue #58 |

Homebrew, a GitHub tap, and GitHub Releases do not require a paid Apple
Developer account. Chrome Web Store registration has a one-time fee. Safari
distribution remains deferred because a persistent Safari Web Extension needs
an Apple-signed container app.

Build and independently check the companion archive with:

```sh
python3 scripts/package_companion.py
python3 scripts/package_companion.py \
  --check dist/groundhog-companion-0.1.0.tar.gz
```

## Bumping the version

1. Choose the next stable `MAJOR.MINOR.PATCH` version. Use a patch release for
   compatible fixes, a minor release for compatible features, and a major
   release for breaking changes to installation, stored data, or the local API.
2. Update `VERSION`.
3. Copy the same value into `extension/manifest.json`'s `version` field.
4. Run the validator and both test suites:

   ```sh
   python3 scripts/validate_version.py
   python3 -m unittest discover -s scripts -p 'test_*.py'
   .venv/bin/python -m unittest discover -s companion -p 'test_*.py'
   npm --prefix extension test
   ```

5. Commit the version bump and release changes together.
6. Tag the commit with the exact `vMAJOR.MINOR.PATCH` value. Before pushing the
   tag, validate it explicitly:

   ```sh
   python3 scripts/validate_version.py --tag v0.1.0
   ```

7. Push the commit and tag. Release automation is responsible for testing,
   building the two archives, generating `SHA256SUMS`, publishing the GitHub
   release, and proposing the matching Homebrew formula update.
8. Upload the released extension ZIP to the Chrome Web Store. Do not build a
   separate store ZIP from a working tree.

The validator exits nonzero for an invalid `VERSION`, a manifest mismatch, or
a tag that does not match `v$(cat VERSION)`. Release automation must run it
with the incoming tag before creating any public artifacts.
