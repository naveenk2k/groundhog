# Groundhog privacy policy

Last updated: September 26, 2026

Groundhog consists of a browser extension and a companion service that runs
on your Mac. It uses your YouTube watch history to help you decide whether a
video covers material you have already watched.

## Data Groundhog handles

The extension handles the current YouTube video ID, playback progress,
Groundhog settings, a local authentication secret, and a small troubleshooting
log. The companion handles video metadata, transcripts, embeddings, verdicts,
and the watch-history corpus you build with Groundhog.

## Where data goes

Extension settings, the authentication secret, and diagnostics are stored in
Chrome's local extension storage. The watch-history corpus, transcript cache,
and companion logs are stored on your Mac.

When you request a verdict, the companion sends the current video's transcript
and transcripts from relevant videos in your Groundhog watch history to the
Google Gemini API. Google processes that request under the terms that apply to
the Gemini API and the API key you provide. Groundhog does not operate an
intermediary server and does not receive a copy of that request.

The companion also contacts YouTube through `yt-dlp` to retrieve video
metadata and available transcripts. Optional local transcription stays on
your Mac.

## How data is used

Groundhog uses this data only to provide its video comparison, transcript,
watch-history, configuration, and troubleshooting features. It does not sell
data, use it for advertising, or use it for credit decisions.

## Retention and deletion

Chrome retains extension settings until you clear the extension's data or
remove the extension. The companion retains its corpus and transcript cache
until you remove entries through Groundhog or delete its data directory.
Homebrew installations store that data under `$(brew --prefix)/var/groundhog`.
A normal Homebrew uninstall leaves this directory in place so an upgrade or
reinstall does not erase your history. Delete the directory yourself if you
want to remove all locally retained Groundhog data.

Requests already processed by Gemini are subject to Google's applicable
retention policy. Consult the Gemini API terms for the account and service tier
you use.

## Contact

Questions and privacy requests can be filed through the
[Groundhog issue tracker](https://github.com/naveenk2k/groundhog/issues).
