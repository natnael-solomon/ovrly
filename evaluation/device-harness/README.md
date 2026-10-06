# RES-02b on-device ASR replay harness

Debug benchmark harness for [#103](https://github.com/natnael-solomon/ovrly/issues/103)
(parent [#14](https://github.com/natnael-solomon/ovrly/issues/14)). Two native
arm64 command-line drivers run on the phone through `adb shell`, replay the
existing RES-02 16 kHz mono PCM16 chunks and write JSON lines. They are not part
of the app and never ship in an APK. Host-side conversion and scoring is
[`../device_benchmark.py`](../device_benchmark.py).

| Driver | Runtime | Model |
| --- | --- | --- |
| `whisper_bench` | whisper.cpp v1.9.4 (MIT), static, CPU only, greedy, `language=en`, `no_context`, no prompt | `ggml-tiny.en.bin`, `ggml-base.en.bin` |
| `vosk_bench` | `libvosk.so` from `com.alphacephei:vosk-android:0.3.75` (Apache-2.0), word times on | `vosk-model-small-en-us-0.15` (Apache-2.0) |

Pins (download from the official sources only; verify before use):

| Input | Source | SHA-256 |
| --- | --- | --- |
| whisper.cpp v1.9.4 source tarball | `github.com/ggml-org/whisper.cpp/archive/refs/tags/v1.9.4.tar.gz` | `57e280cee375ab02425b806ad5146b99f6eb9357e3c2b31357c8a6af2e2e44ae` |
| vosk-android 0.3.75 AAR | Maven Central (`.sha1` `40764b038a882055e1a57c33136c86ab9b7db2ee` matches) | `ab2f8b91ac8051561aa325546b35fed9a68b36b8121bac5c6fb927525c4adfad` |
| `vosk_api.h` | `alphacep/vosk-api` `src/vosk_api.h` | `f82f553a38fbff84a5731fbf086215f107e301a27daad0f038459781dc4a2adc` |
| `ggml-tiny.en.bin` (MIT, OpenAI Whisper weights) | `huggingface.co/ggerganov/whisper.cpp`, matches the published LFS SHA-256 | `921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f` |
| `ggml-base.en.bin` (MIT, OpenAI Whisper weights) | `huggingface.co/ggerganov/whisper.cpp`, matches the published LFS SHA-256 | `a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002` |
| `vosk-model-small-en-us-0.15.zip` (Apache-2.0) | `alphacephei.com/vosk/models` (no upstream checksum published) | `30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498` |

Keep downloads, builds and outputs outside the checkout. Nothing here needs a
provider key or network access on the phone.

## Build

Requires the Android NDK 29.0.13599879 and SDK CMake 3.22.1 (with Ninja), as
installed by the Android SDK manager. From the repository root on Windows:

```powershell
.\evaluation\device-harness\build.ps1 -WhisperSrc <whisper.cpp-1.9.4> -VoskDir <dir with vosk_api.h and arm64-v8a libvosk.so> -BuildDir <out> -Jobs 2
```

ggml is built with `-march=armv8.2-a+fp16` (no `-mcpu=native`, no dotprod),
no OpenMP, no GPU. The Galaxy A21s (Exynos 850, Cortex-A55 part `0xd05`)
reports `fphp` but not `asimddp`, so a dotprod build faults with an illegal
instruction there. Check `/proc/cpuinfo` on any other phone before choosing a
wider `-ArmArch`. The script prints the two binary hashes; record them.

## Run

```text
adb shell mkdir -p /data/local/tmp/res02/audio /data/local/tmp/res02/models
adb push <bundle>/inputs/audio/. /data/local/tmp/res02/audio/
adb push whisper_bench vosk_bench libvosk.so asr-list.tsv /data/local/tmp/res02/
adb push ggml-tiny.en.bin ggml-base.en.bin /data/local/tmp/res02/models/
adb push vosk-model-small-en-us-0.15 /data/local/tmp/res02/models/
adb shell chmod 755 /data/local/tmp/res02/whisper_bench /data/local/tmp/res02/vosk_bench
adb shell "cd /data/local/tmp/res02 && LD_LIBRARY_PATH=. ./vosk_bench models/vosk-model-small-en-us-0.15 asr-list.tsv vosk.jsonl"
adb shell "cd /data/local/tmp/res02 && ./whisper_bench models/ggml-tiny.en.bin asr-list.tsv tiny.jsonl 4"
adb pull /data/local/tmp/res02/vosk.jsonl <private-results>/
```

Generate `asr-list.tsv` (142 chunks, both 10/15-second schedules) with
`python evaluation/device_benchmark.py asr-list --bundle <bundle> --device-dir /data/local/tmp/res02/audio --out asr-list.tsv`.
An optional last argument paces item *i* to start no earlier than *i* x
`pace_ms` after the first, for live-style three-minute resource runs. Remove
`/data/local/tmp/res02` from the phone when finished.

## Export format

One JSON object per line:

- `init`: runtime, model path, `status` (`ok` or `ASR_UNAVAILABLE` when the
  model cannot be loaded, exit code 3), cold `init_ms`, `rss_kib`,
  `peak_rss_kib`, threads/pacing.
- `chunk`: `id`, `samples`, `status`, monotonic `wall_ms` from file read to
  final result, `rss_kib`/`peak_rss_kib`, and the raw recognizer output:
  whisper.cpp `segments` (`start_ms`/`end_ms` chunk-relative, never clamped) or
  Vosk `results` (every intermediate and final result object as emitted, with
  word times in seconds). An unreadable or malformed file is
  `ASR_UNAVAILABLE` with an `error`; WAV headers are parsed and only the data
  chunk is used. Files without a RIFF header are read as raw PCM16, so the
  real AN-04 `audio-16000-mono-s16le.pcm` entries replay unchanged.
- `end`: item and failure counts. Exit code 1 means at least one item failed.

Cold model load is reported separately from per-chunk time. The Vosk
recognizer is created per chunk (its time is `recognizer_new_ms`, inside
`wall_ms`) so that every result is chunk-relative.

## Score

```text
python evaluation/device_benchmark.py asr-score --bundle <bundle> --run vosk.jsonl --model vosk/small-en --model-revision "<pins>" --hardware "<model/Android/ABI>" --provenance "<run notes>" --label vosk-small-en --out <private-results>/scores/vosk
python evaluation/device_benchmark.py groq-baseline --bundle <bundle> --out <private-results>/scores/groq-baseline.json
```

`asr-score` reuses each clip's existing `res02-v1` selected-entity scorer input
from the bundle unchanged (subtitle reference, cue envelopes, entity labels,
chunk boundaries) and replaces only the hypothesis side, so device and hosted
results are paired on identical references. Lexical scoring uses whole-chunk
text envelopes like the hosted baseline; raw recognizer times are audited
separately with `asr_experiment.subtitle_timing` and raw fault counts
(reversed, negative, past the chunk end), never clamped. The summary sums edit
counts and reference tokens per model and chunk length rather than averaging
clip percentages. Feeding the stored Groq responses through `asr-score`
reproduces the bundle's hosted scores exactly, which checks the conversion.

## Other helpers

Captured-device evidence from #103 (all private): `device-info`, `verify-capture`,
`quota-ledger` and the resource sampler are documented in
[BENCHMARKS](../BENCHMARKS.md#device-harness-and-results-res-02b-103).

`device_benchmark.py` also provides `device-info` (model, Android, API, ABI,
CPU features only; no serials or account identifiers), `verify-capture` (real
AN-04 `capture.json` and chunk ZIP checks), `align-capture` (capture-to-media
offset and clock ratio by audio envelope cross-correlation, reported with its
residuals), `sample-resources` and `summarize-resources` (battery level,
temperature, plug state and thermal status from `dumpsys`, over USB or Wi-Fi
adb). Their tests use invented data only.
