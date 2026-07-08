"""Tests for companion/transcript.py.

Exercises `_pick_subtitle_url` and `_vtt_to_text` directly - both are pure,
network-free functions (dict-in/str-out) even though the only caller today,
`fetch_transcript`, reaches them via a live yt-dlp fetch. No network, no
yt-dlp calls, no mocking needed here.

Run directly: python -m companion.test_transcript
(also discoverable by unittest/pytest as usual)
"""

import unittest

from companion.transcript import _extract_creator, _pick_subtitle_url, _vtt_to_text


class PickSubtitleUrlTest(unittest.TestCase):
    def test_picks_manual_english_vtt_track(self):
        info = {
            "subtitles": {
                "en": [
                    {"ext": "vtt", "url": "https://example.com/manual-en.vtt"},
                ],
            },
        }
        self.assertEqual(_pick_subtitle_url(info), "https://example.com/manual-en.vtt")

    def test_picks_auto_caption_with_regional_english_variant(self):
        info = {
            "automatic_captions": {
                "en-US": [
                    {"ext": "vtt", "url": "https://example.com/auto-en-US.vtt"},
                ],
            },
        }
        self.assertEqual(_pick_subtitle_url(info), "https://example.com/auto-en-US.vtt")

    def test_prefers_manual_subtitles_over_automatic_captions(self):
        info = {
            "subtitles": {
                "en": [
                    {"ext": "vtt", "url": "https://example.com/manual-en.vtt"},
                ],
            },
            "automatic_captions": {
                "en": [
                    {"ext": "vtt", "url": "https://example.com/auto-en.vtt"},
                ],
            },
        }
        self.assertEqual(_pick_subtitle_url(info), "https://example.com/manual-en.vtt")

    def test_returns_none_when_no_english_track_present(self):
        info = {
            "subtitles": {
                "es": [
                    {"ext": "vtt", "url": "https://example.com/manual-es.vtt"},
                ],
            },
            "automatic_captions": {
                "es": [
                    {"ext": "vtt", "url": "https://example.com/auto-es.vtt"},
                ],
            },
        }
        self.assertIsNone(_pick_subtitle_url(info))

    def test_falls_back_to_first_format_when_no_vtt_entry(self):
        info = {
            "subtitles": {
                "en": [
                    {"ext": "srv3", "url": "https://example.com/manual-en.srv3"},
                    {"ext": "ttml", "url": "https://example.com/manual-en.ttml"},
                ],
            },
        }
        self.assertEqual(_pick_subtitle_url(info), "https://example.com/manual-en.srv3")


class ExtractCreatorTest(unittest.TestCase):
    def test_uses_uploader_when_present(self):
        info = {
            "uploader": "Marques Brownlee",
            "channel": "MKBHD",
            "uploader_id": "@mkbhd",
            "channel_id": "UCBJycsmduvYEL83R_U4JriQ",
        }
        self.assertEqual(_extract_creator(info), "Marques Brownlee")

    def test_falls_back_to_channel_when_uploader_absent(self):
        info = {
            "channel": "MKBHD",
            "uploader_id": "@mkbhd",
            "channel_id": "UCBJycsmduvYEL83R_U4JriQ",
        }
        self.assertEqual(_extract_creator(info), "MKBHD")

    def test_falls_back_to_stripped_uploader_id_when_uploader_and_channel_absent(self):
        info = {
            "uploader_id": "@BigTechnologyPodcast",
            "channel_id": "UCsomeChannelId",
        }
        self.assertEqual(_extract_creator(info), "BigTechnologyPodcast")

    def test_falls_back_to_channel_id_as_last_resort(self):
        info = {"channel_id": "UCsomeChannelId"}
        self.assertEqual(_extract_creator(info), "UCsomeChannelId")

    def test_returns_none_when_everything_absent(self):
        self.assertIsNone(_extract_creator({}))


class VttToTextTest(unittest.TestCase):
    def test_normal_vtt_collapses_to_plain_text(self):
        vtt = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
Today we're making sourdough bread from scratch.

2
00:00:04.000 --> 00:00:07.500
First you need a healthy starter.
"""
        self.assertEqual(
            _vtt_to_text(vtt),
            "Today we're making sourdough bread from scratch. First you need a healthy starter.",
        )

    def test_inline_tags_are_stripped(self):
        vtt = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
<c>Today</c> we're <c.colorE5E5E5>making</c> sourdough bread.
"""
        self.assertEqual(_vtt_to_text(vtt), "Today we're making sourdough bread.")

    def test_consecutive_duplicates_collapsed_but_non_consecutive_kept(self):
        vtt = """WEBVTT

1
00:00:01.000 --> 00:00:02.000
hello world

2
00:00:02.000 --> 00:00:03.000
hello world

3
00:00:03.000 --> 00:00:04.000
goodbye now

4
00:00:04.000 --> 00:00:05.000
hello world
"""
        # The second "hello world" cue (consecutive with the first) is
        # deduped, but the third occurrence - after "goodbye now" broke the
        # run - is kept since dedup only tracks the immediately previous line.
        self.assertEqual(_vtt_to_text(vtt), "hello world goodbye now hello world")

    def test_kind_and_language_metadata_lines_are_skipped(self):
        vtt = """WEBVTT
Kind: captions
Language: en

1
00:00:01.000 --> 00:00:04.000
Today we're making sourdough bread.
"""
        self.assertEqual(_vtt_to_text(vtt), "Today we're making sourdough bread.")

    def test_empty_vtt_returns_empty_string(self):
        self.assertEqual(_vtt_to_text(""), "")

    def test_whitespace_only_vtt_returns_empty_string(self):
        self.assertEqual(_vtt_to_text("   \n\n\t\n   "), "")


if __name__ == "__main__":
    unittest.main()
