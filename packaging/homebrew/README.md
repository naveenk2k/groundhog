# Homebrew tap packaging

This directory stages the formula that will live in `naveenk2k/homebrew-tap`.
It is a template until a tagged release exists because Homebrew formulae
require immutable download URLs and checksums.

The formula keeps mutable state outside its versioned Cellar directory:

- `$(brew --prefix)/var/groundhog/corpus.db` contains the corpus and transcript cache.
- `$(brew --prefix)/var/groundhog/secret` contains the extension's shared secret.
- `$(brew --prefix)/var/groundhog/cache` contains model and library caches.
- `$(brew --prefix)/var/groundhog/log` contains service logs.
- `$(brew --prefix)/etc/groundhog/groundhog.env` contains `GEMINI_API_KEY` and any optional environment settings.

Homebrew preserves `var` and `etc` data across upgrades. A normal `brew uninstall groundhog` also leaves those files in place; users must remove them explicitly if they want to erase their Groundhog data.

## Preparing a release formula

1. Publish `groundhog-companion-VERSION.tar.gz` on the matching `vVERSION` GitHub release.
2. Compute its SHA-256 checksum.
3. Copy the template into the tap as `Formula/groundhog.rb`, temporarily replacing the source URL and SHA tokens with the release values and the Python resource token with a harmless comment.
4. In the tap, run `brew update-python-resources naveenk2k/tap/groundhog`.
   The `pypi_packages` stanza records Groundhog's direct dependencies so
   Homebrew can generate checksummed transitive resource blocks.
5. Save the generated resource blocks to a file and render the final formula:

   ```sh
   packaging/homebrew/prepare-formula.sh VERSION SOURCE_SHA256 resources.rb /path/to/homebrew-tap/Formula/groundhog.rb
   ```

6. Run the tap checks:

   ```sh
   brew style naveenk2k/tap/groundhog
   brew audit --strict naveenk2k/tap/groundhog
   brew install --build-from-source naveenk2k/tap/groundhog
   brew test naveenk2k/tap/groundhog
   brew services start groundhog
   curl http://127.0.0.1:8787/health
   brew services restart groundhog
   brew services stop groundhog
   ```

The source URL, checksum, and Python resources cannot be filled in before the tagged release exists. Do not publish a formula with template tokens or placeholder checksums.

Run the local template checks with:

```sh
packaging/homebrew/test.sh
```
