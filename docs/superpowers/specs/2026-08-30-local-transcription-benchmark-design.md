# Local transcription fallback benchmark

**Status:** benchmarking in progress

## Problem

Some YouTube videos have no usable English transcript. Groundhog currently
stops at `no_transcript`, so the companion needs a fast local speech-to-text
fallback without making the normal caption path slower.

## Goals

- Measure end-to-end latency for audio-only acquisition and local transcription.
- Compare practical Whisper runtimes and model sizes on this Mac.
- Compare full-video transcription with representative segment sampling.
- Measure warm and cold model behavior separately.
- Identify an accuracy/latency point suitable for the interactive overlay.

## Candidate matrix

- `whisper.cpp` on Apple Silicon Metal/Core ML where available.
- `faster-whisper` using CTranslate2, with CPU and available accelerator settings.
- Whisper model sizes: `tiny.en`, `base.en`, `small.en`, and a faster high-quality
  model such as `distil-small.en` or `large-v3-turbo` when hardware permits.
- Quantized variants where the runtime supports them.

## Sampling strategies

- Full audio for short videos.
- Three representative windows for longer videos: introduction, middle, and
  conclusion. Windows are selected by duration rather than fixed timestamps.
- Record download, decode, model-load, transcription, and total wall-clock
  latency independently.

## Decision criteria

The preferred fallback must preserve the caption fast path, keep the model warm
in the companion, avoid downloading video when audio is sufficient, and return
within a bounded interactive budget. Sampling is acceptable only if its verdict
quality is adequate for Groundhog's novelty comparison; otherwise it is a
latency fallback with an explicit confidence limitation.

## Planned workflow

1. Verify current hardware and installed media/ASR tools.
2. Read the official installation and performance guidance for each selected
   runtime.
3. Build a throwaway benchmark harness with repeatable local audio fixtures.
4. Run warm/cold benchmarks across model/runtime/quantization combinations.
5. Run full versus introduction/middle/conclusion sampling benchmarks.
6. Record results and choose the production fallback design.

This is a benchmark/prototype artifact. Keep the conclusions; delete or absorb
the harness after the implementation decision is made.

## Initial benchmark results

Environment: Apple Silicon macOS, Homebrew `whisper-cpp` 1.9.2, `ffmpeg`
9.0.1. The fixture is the official 11-second `whisper.cpp` JFK speech sample
looped to 176 seconds; the sampled fixture concatenates 20-second windows from
the introduction, middle, and conclusion positions for about 60 seconds total.

Cold CPU runs, including process/model startup:

| Runtime/model | 60-second sample | 176-second full fixture |
| --- | ---: | ---: |
| `whisper.cpp` `tiny.en` | 1.11s | 3.80s |
| `whisper.cpp` `base.en` | 1.94s | 6.46s |
| `whisper.cpp` `base.en` Q5_0 | 1.81s | 5.72s |
| `faster-whisper` `tiny.en`, int8 CPU | 2.82s warm run | pending full run |
| `faster-whisper` `base.en`, int8 CPU | 5.95s first run | pending full run |

`faster-whisper` model load was about 2.6–3.6s after model download. A second
warm `base.en` run took 4.86s versus 7.38s on the first transcription in that
process. `whisper.cpp` Metal mode crashed with exit 139 on the Homebrew build;
CPU mode succeeded. The downloaded `small.en` artifact failed to initialize
and needs to be re-fetched or validated before it is included.

These are speed-only measurements on repeated speech, not an accuracy study.
The early signal favors a warmed `whisper.cpp` `base.en` or Q5_0 model and
three-segment sampling for the interactive fallback, subject to validation on
real videos with varied speech, music, silence, and topic structure.

## Metal and caption-path findings

The Homebrew `whisper-cpp` 1.9.2 binary reproducibly exits 139 with Metal on
this Mac (Apple M5 Pro, macOS 26.6.2), even with `--no-flash-attn`, one thread,
or an explicit device. `-ng` succeeds. The upstream `whisper.cpp` 1.9.3-dev
build, compiled locally with Metal enabled, reproduces the same failure; its
CPU path succeeds. `GGML_METAL_TENSOR_DISABLE=1`, suggested for a related M5
tensor-API issue, did not change the result. This currently points to a
Metal/ggml/macOS runtime incompatibility rather than a Homebrew-only linkage
problem. Keep CPU as the production fallback until a fixed upstream build or
macOS runtime is verified.

`yt-dlp` already supports captions directly. Its Python API returns authored
tracks in `subtitles` and generated tracks in `automatic_captions`; its CLI
supports `--skip-download --write-subs --write-auto-subs --sub-langs`. The
current companion already uses the Python API to inspect those dictionaries
and fetches the selected VTT URL directly, so a separate caption downloader is
not required. On a public test video, the newer Homebrew CLI took 1.16s to
write English VTT captions; the repo-pinned Python API took 2.13s for metadata
plus caption fetch. This is directional because the versions differ, but it
suggests benchmarking a single reused `YoutubeDL` object and the current pin
before changing the path.

For media fallback, `android_vr` is not reliable: the current yt-dlp run
returned HTTP 403 for the audio URL without a GVS PO token. `tv_simply`
successfully downloaded the public test video without a token, but exposed only
a muxed 360p format, so the fallback must strip its audio locally. This keeps
the caption client and media-download client separate and leaves PO-token
handling out of the first local-ASR implementation.
