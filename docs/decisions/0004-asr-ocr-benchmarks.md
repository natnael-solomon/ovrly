# 0004: ASR and screen-text benchmark gates

**Decision ID and question:** RFC-D27: which zero-cost transcription adapter
should BE-07 use? Related OCR/sampling questions: RFC-D28, D68 and D71.

**Status:** RFC-D27 Accepted by the product owner (natnael-solomon) on 6 October
2026 at 20:17 EAT, as proposed in the 6 October revision below:
- Adapter: Groq `whisper-large-v3-turbo` on 10 s chunks, falling back to
  `whisper-large-v3`.
- No on-device ASR in the submission.
- `ASR_UNAVAILABLE` semantics as in `evaluation/asr_policy.py`.

Recorded Open on 4 October, updated 5 October, revised from device evidence on
6 October (RES-02b, #103). The OCR route and sampling position below are
recommendations that rest on 0005 (Accepted) and #110. The on-device screen-text
route for live capture is recorded separately in
[0005](0005-on-device-screen-text.md) (bundled ML Kit, telemetry upload removed).

**Owner and participants:** Neb-iyu owns RES-02 (#14). Product-owner approval
and the Android/BE-07 handoff remain necessary for cross-cutting choices.
Child [#92](https://github.com/natnael-solomon/ovrly/issues/92) tracks the
non-phone implementation and private device-test handoff; its completion
does not accept this open decision or close parent #14.
The user's direction on 4 October was to proceed with #14 work independent of
#9, using the merged #8 handoff. Subsequent user direction is to reuse existing
media/SRTs and continue after #9 closed. Audio processing permission is a
separate controlled run record, never inferred from either issue's closure.

**Options considered:** Groq Whisper large-v3/turbo on the free tier;
on-device Vosk small-en; on-device whisper.cpp tiny.en/base.en if viable.
ML Kit Latin v2 versus Tesseract eng fast/best on identical frames, with fixed
five-second versus recorded change-triggered sampling.

**Evidence and uncertainties:** Main includes the owner-accepted local
eleven-clip snapshot from PR #85, with seven dev/four holdout clips and
preserved limitations. Its claims are not ASR/OCR ground truth. The earlier
[BC-D03](BC-D03-provider-hosting.md) precheck records one authorized 31.819 s
Groq excerpt for both models; it explicitly does not establish certified WER,
speed rankings, quotas or a model selection. No broader measurement is inferred
by that precheck. The final reviewed social SRTs and clip-d source subtitles
are available locally, as is the original media. The new runner hashes and
reuses them without creating a new corpus or BE-01 extraction-window set.
Its records distinguish subtitle agreement from certified ASR WER and remain
local. Dated public-plan assumptions and response headers are not presented as
an independent account entitlement audit.

The separately authorized seven-dev-clip hosted trial has completed; its raw
responses, subtitle scores and selected AI-assisted critical-span review are
kept in controlled local artifacts. The lexical review includes formatting
differences as well as name mismatches, so aggregate span disagreement is not
a certified semantic-error rate. Per user direction, phone-dependent ASR/OCR
and resource measurements are deferred until the device is available.

The non-phone follow-up also completed the exact-whole-cue timestamp audit,
actual three-minute request-prefix accounting, a pinned Tesseract fast/best
comparison on supplied screenshots, source-video fixed/change sampling, and
deduplication preservation checks. All detailed results remain local. The
timestamp audit exposes out-of-chunk output, including large overruns on short
tails. Successful HTTP status is not proof of usable timing. The local pixel-
change experiment still misses a supplied card window while increasing sample
count; it is not a validated production policy. Recognition accuracy is scoped
to selected screenshot regions, not all readable video text.

**Chosen option and rationale (5 October position, superseded by the 6 October
proposal below):** No model selected. Use the
[benchmark protocol](../../evaluation/BENCHMARKS.md) to validate dev observations,
score errors and expose missing evidence. #9 is now closed. Keep ML Kit as the
hypothesis because it avoids frame uploads and server OCR CPU, not because
comparative accuracy has been measured.

Neither hosted model nor chunk size is preferred by this record. The earlier
turbo/ten-second development preference is withdrawn because its supporting
metrics are not committed and cannot be reviewed from this repository.
Before proposing a preference, publish an authorized metrics-only summary per
model/chunk size with clip and reference-token counts, token-weighted subtitle
agreement, p50/p95 request latency and request count, without transcripts or
responses. No permission to publish the private run is inferred here.
**Do not add production server Tesseract**:
frame transfer and server CPU are outside the accepted budget, regardless of
workstation benchmark speed. The intended OCR route remains on-device ML Kit,
conditional on the same-frame/device measurements. Fixed five-second sampling
alone and the tested global-luminance trigger are not certified complete.

**User-visible consequences:** None yet; the application does not call these
scorers. The required production rule remains: Groq quota exhaustion or outage
must yield a verified on-device fallback or explicit `ASR_UNAVAILABLE`.
Never silently return empty transcription, switch to the microphone or omit
the speech modality. Selecting or implementing that fallback is not implied
by recording an offline failed-chunk observation.

**Technical, privacy, cost and evaluation consequences:** Hosted ASR sends
consented audio to a US processor; record applicable processing/retention
permission separately. On-device recognition does not send media off-device.
The local corpus's acceptance does not grant hosted processing, training or
redistribution. Preserve pending clip rights, provisional reviews and holdout
isolation. Record provider limits from dated account evidence; the issue's
historic quota numbers are not a current guarantee. The opt-in runner records
actual derived WAV sizes/hashes, but those are workstation output, not Android
capture chunks. Quota fractions remain estimates, not billing observations.

**Dependencies / capability gates:** Offline tools and tests can proceed now.
Hosted subtitle-reference trials, selected critical-span review and exact-cue
timing diagnostics are complete. They do not certify independent word timing
or the Android capture clock.
The opt-in runner uses separately authorized audio and dated account assumptions.
#9 supplies real device/provider capture evidence on Galaxy A21s / Android 12,
including a three-minute TikTok capture; the OCR comparison and
sampling conclusions additionally need captured frames and visibility/box
references. Weak-phone runtime, battery and thermal measurements need an
authorized phone. AN-04 (#26) must supply actual chunks before offsets, file
caps and chunk-size selection are signed off. These gates are separate; do not
attribute unavailable recognition/resource measurements or permission to #9.
Current Android capture code writes continuous 16 kHz mono PCM16; final AN-04
chunk envelopes are a separate upstream handoff, not a missing corpus artifact.
The non-phone evidence does not require new media, transcripts or hosted runs.
Actual account-billed audio is not exposed in the observed headers; keep the
accepted assumption and measured request/audio ledger rather than inventing it.

**Rejected alternatives and why:** Paid whisper-1 and server-local
faster-whisper are excluded by #14's budget. No production server Tesseract
choice without frame-upload and hosting CPU evidence. No normalized claims
as verbatim references, cue envelopes as measured word timing, synthetic tests
as device evidence, or silent fallback from a failed recognition stage.

**What evidence would reverse this decision:** This open record becomes a
selection only after the paired candidate results are reviewed alongside
critical-error analysis, dated quota/permission evidence, real device chunk
checks and device cost measurements. Record the chosen adapter, fallback, limitations,
participants/date and evidence hashes in a reviewed revision. A failure of
on-device viability requires an explicit unavailable path, not a paid default.

**Linked contract, test, source and ideation record:** #8, #9, #14, #20,
#26 and #30; RFC-D27/D28/D68/D71; BC-D05; AC03-AC06;
`evaluation/benchmark.py`, `evaluation/asr_experiment.py`, their offline tests,
[evaluation contract](../../evaluation/README.md),
[local snapshot](../../evaluation/corpus-local/README.md). Device revision:
#103, #105, #110, `evaluation/device_benchmark.py`, `evaluation/asr_policy.py`,
`evaluation/device-harness/`, `ScreenTextReplayTest`.

## 6 October 2026 revision: device evidence and proposal (RES-02b, #103)

All measurements below were made on the Samsung Galaxy A21s (SM-A217F, Android 12,
API 31, arm64-v8a, Exynos 850, 8x Cortex-A55, 4 GB), the weakest and only
authorized test phone. Inputs are the immutable `res02-phone-test-handoff` bundle
(1,439 files, manifest SHA-256 `3e801539...d0d5`, verified read-only). They are
identical WAV and image bytes to the #92 hosted and Tesseract baselines, so the
comparisons are paired. No hosted call was made. Raw outputs, transcripts, frames and
the private run manifest with its SHA-256 inventory stay in controlled storage. The
tables are content-free. Publishing them needs the owner's approval, which is
requested in the PR.

### ASR: 142 chunks, seven dev clips, both schedules, 2,279 subtitle tokens

Subtitle agreement (`res02-v1`, reviewed/source SRTs, cue envelopes), not certified
WER. Edits and denominators are summed across clips, never averaged. Selected
entity spans: negation 28, number 14, name 30, date 2, unit 5 per setting.

| Model (where) | Chunk | WER (S/D/I) | Entity errors neg/num/name/date/unit | Chunk wall p50 / p95 | Failed |
| --- | --- | --- | --- | --- | --- |
| Groq large-v3 (hosted, #92) | 10 s | 0.0355 (25/19/37) | 0/2/5/0/0 | 3,497 / 6,873 ms | 0 |
| Groq large-v3-turbo (hosted, #92) | 10 s | 0.0307 (28/14/28) | 1/2/5/0/0 | 3,326 / 6,321 ms | 0 |
| Groq large-v3 (hosted, #92) | 15 s | 0.0325 (23/24/27) | 0/3/6/0/0 | 4,767 / 7,933 ms | 0 |
| Groq large-v3-turbo (hosted, #92) | 15 s | 0.0338 (23/21/33) | 0/2/6/0/0 | 4,590 / 9,438 ms | 0 |
| whisper.cpp tiny.en (phone) | 10 s | 0.0601 (82/16/39) | 0/2/6/0/0 | 4,932 / 9,505 ms | 0 |
| whisper.cpp tiny.en (phone) | 15 s | 0.0579 (66/29/37) | 1/2/6/0/0 | 5,346 / 9,917 ms | 0 |
| whisper.cpp base.en (phone) | 10 s | 0.0478 (46/26/37) | 1/2/6/0/0 | 10,219 / 19,831 ms | 0 |
| whisper.cpp base.en (phone) | 15 s | 0.0491 (34/12/66) | 0/2/5/0/0 | 11,146 / 21,932 ms | 0 |
| Vosk small-en (phone) | 10 s | 0.1075 (165/24/56) | 4/13/7/2/3 | 10,248 / 11,391 ms | 0 |
| Vosk small-en (phone) | 15 s | 0.0952 (146/25/46) | 3/13/6/2/3 | 12,925 / 14,754 ms | 0 |

Phone real-time factor (wall time divided by chunk audio, p50 / p95): tiny.en 0.50 / 1.04 (10 s) and
0.36 / 1.07 (15 s); base.en 1.04 / 2.18 and 0.75 / 2.25; Vosk 1.04 / 1.24 and 0.87 /
1.24. Cold model load was timed separately: tiny.en 3,288 ms, base.en 545 ms (file cache warm), Vosk
2,632 ms. Peak resident memory: tiny.en 239 MB, base.en 373 MB, Vosk 176 MB.
Hosted wall time is a workstation client over the network and is not comparable
with on-device compute.

Conditions are recorded, not normalized. Runs were native arm64 command-line
drivers (whisper.cpp v1.9.4, 4 threads, greedy, CPU only; vosk-android 0.3.75 with
`vosk-model-small-en-us-0.15`) started through `adb shell`, not the app process. The
phone was on USB/AC charging throughout. Vosk ran at thermal status 3 (severe) and
its timing is throttled. tiny.en ran from status 2 to 3. base.en started after a
cooldown at status 0 and ended at 1.

The lexical review of mismatched spans is private. It distinguishes representation from meaning:
- Vosk spells numbers and percentages as words, which explains most of its number
  and unit errors.
- Vosk also changes polarity on one reference span in both schedules (a contracted
  negation is dropped) and changes one year.
- whisper.cpp errors are mostly names. One 15 s tiny.en negation span is garbled
  rather than inverted.
- Groq's selected-span mismatches were reviewed in #92. They are numeral or website
  formatting, one negation phrase that keeps its polarity, and real misses on a
  few proper names.

Timestamps. Independent speech-interval review was not done, so no timestamp-accuracy
claim is made. Exact whole-cue subtitle anchors (pairing coverage, not timing truth):

| Model | Chunk | Segments | Whole-cue matches | Abs. cue endpoint p50 / p95 | Raw segments past chunk end |
| --- | --- | --- | --- | --- | --- |
| Groq large-v3 / turbo | 10 s | 236 / 228 | 36 / 37 | 161 / 960, 270 / 1,100 ms | 2 / 0 |
| Groq large-v3 / turbo | 15 s | 242 / 221 | 42 / 46 | 200 / 1,000, 212 / 1,160 ms | 1 / 2 |
| tiny.en | 10 s / 15 s | 215 / 211 | 33 / 43 | 140 / 640, 140 / 860 ms | 25 / 17 |
| base.en | 10 s / 15 s | 225 / 230 | 34 / 49 | 152 / 757, 140 / 580 ms | 16 / 20 |
| Vosk | 10 s / 15 s | 98 / 69 | 6 / 7 | 260 / 930, 220 / 770 ms | 0 / 3 |

Raw out-of-chunk times are reported and never clamped. No reversed or negative
segments occurred. whisper.cpp also emitted 2 to 3 whole-segment `[BLANK_AUDIO]`
markers per setting. These were excluded from lexical scoring and counted.

Negative and offline cases (phone, local inputs only): every one gave an explicit `ASR_UNAVAILABLE`, never an empty success.
- A missing Vosk model and a corrupt whisper model gave init `ASR_UNAVAILABLE` (exit 3).
- Header-only, truncated, stereo-declared, odd-length and missing audio each gave
  `ASR_UNAVAILABLE` with a reason.
- Ten seconds of silence gave `ok` with no text, or a counted non-speech marker.
- tiny.en transcribed all 23 clip-d 10 s chunks with airplane mode on (radios
  powered off; p50 4,748 ms per chunk). Airplane mode was then restored.

Provider failure paths are exercised with local test doubles in
`evaluation/tests/test_asr_policy.py`: quota, outage, offline, unknown outcome,
invalid response, invalid timestamps, missing samples, file cap. These are
synthetic and are not observed provider incidents.

### Real AN-04 chunks (one authorized three-minute capture)

- **Run:** YouTube Shorts on the phone, main `5f2d475` debug build, driven by
  `CaptureServiceTest#threeMinuteLimitStopsAndReleases`. The test passed: it stopped
  at exactly 180,000 ms with the limit message, a finished manifest, and the
  projection, display, recorder and notification released.
- **Evidence covered:** the test deletes its capture, so the private directory was
  snapshotted every ~2 s. The last snapshot holds seq 0 to 16 (170,000 ms). The
  sealed seq 17 and the finished manifest bytes fell between snapshots, so the
  three-minute boundary rests on the test's assertions, not on a pulled file.
- **Checks on real bytes, seq 0 to 16:** all pass.
  - Contiguous sequence on the 10 s capture grid, no skips, no gaps.
  - Chunk SHA-256 and sizes match the manifest; `chunk.json` matches the manifest.
  - PCM length equals the declared length; ZIP entry times are fixed to 1980.
  - Every frame offset lies inside its chunk.
- **Sizes:** the largest ZIP is 303,903 bytes; the largest PCM wrapped as WAV would
  be 320,584 bytes. Both are far below Groq's 25 MB cap and the 256 MiB backend cap.
- **Audio total:** 80.7 ms short of nominal over 170 s. The worst chunk is -94.9 ms,
  right after start; the others are -4.4 or +16.9 ms.
- **Observations:** 53 frames were kept and 40 refused by the per-minute cap. One
  10 s chunk kept no frame. Every chunk names recognizer 16.0.1.
- **Memory:** app peak 132.6 MB PSS / 219 MB RSS.

### OCR on identical bytes, and sampling

ML Kit Latin 16.0.1 bundled, from the merged #105 code, through the debug-only
`ScreenTextReplayTest`:
- Bytes: the exact six bundle screenshots and 510 source frames, three
  repetitions, full image and production text-region crop.
- Outcome: no failures, and outputs were identical across repetitions.
- Region metric: the #92 selected-region metric (26 regions, 203 reference tokens,
  AI-assisted labels). It is not full-screen OCR ground truth.

| Recognizer | Selected-region WER (S/D/I) | Unmatched words | Screenshot p50 / p95 | 720 px source frame p50 / p95 |
| --- | --- | --- | --- | --- |
| ML Kit, production crop (phone) | 0.187 (21/14/3) | 42 | 540 / 1,198 ms | 340 / 949 ms |
| ML Kit, full image (phone) | 0.207 (23/17/2) | 171 | 333 / 799 ms | 130 / 236 ms |
| Tesseract fast (workstation, #92) | 0.502 (49/49/4) | 64 | 225 / 465 ms | not comparable |
| Tesseract best (workstation, #92) | 0.532 (52/52/4) | not recomputed | 732 / 1,726 ms | not comparable |

Shipped sampler replay of the seven dev clips:
- Method: decoded at the 1 Hz probe times, scaled to 720 px, and passed through
  `FrameSampler` and `ScreenTextReader` (threshold 12, 5 s heartbeat, 20 per 60 s).
  814 probes, no decode or recognition failure.
- Windows: the seven adjudicated on-screen-text windows (four of 3 s or less).
- Fixed 5 s: misses 2 windows temporally, both 2 s cards.
- Shipped change trigger: misses 1. It reads 5 of the 6 labelled cards at 50% or
  more token recall, against 4 for fixed.
- Miss cause: the per-minute cap. Every probe from 162 s to 168 s of that clip was
  refused, so a 2 s card was dropped. The cap refused 56 of 96, 41 of 112 and
  59 of 171 probes on the three caption-dense clips. Filed as
  [#110](https://github.com/natnael-solomon/ovrly/issues/110).
- Recognition time per kept frame on the phone: p50 142 to 671 ms and p95 703 to
  1,284 ms, varying by clip.
- Limits: decoded media frames, not MediaProjection frames. The windows are
  provisional AI-assisted adjudications, so no full-screen missed-text rate is
  claimed. Track preservation through the general dedup scorer is reported per
  clip in the private package.

### Selection (RFC-D27 accepted by the owner on 6 October 2026)

1. **RFC-D27 adapter: Groq `whisper-large-v3-turbo` on 10 s chunks.**
   - Accuracy: it has the lowest paired subtitle disagreement at 10 s (0.0307). The
     #92 review found its number and website mismatches are formatting and its
     negation mismatch keeps polarity. It still misses some proper names, as every
     candidate does, which claim extraction must tolerate.
   - Request size: a real chunk wrapped as WAV is about 0.32 MB.
   - Volume: a full three-minute capture is 18 requests and 180,000 ms of audio.
   - Quota estimate: under the dated public-plan assumptions (7,200 audio-seconds
     per hour, 2,000 requests per day, 10 s minimum) that is 2.5% of the hourly
     audio, about 40 captures per hour or 111 per day. These are estimates;
     account billing evidence is unavailable.
   - Large-v3 is the equivalent alternative if turbo is unavailable.
2. **Fallback and unavailable policy (handed to BE-07 #20):** `evaluation/asr_policy.py`
   is the reference contract.
   - Missing or short audio and over-cap chunks are `ASR_UNAVAILABLE` before any
     call.
   - Quota, outage, offline, invalid response and unknown outcome try the
     fallback (`whisper-large-v3`) once. If it also fails, the chunk is
     `ASR_UNAVAILABLE` with the reason.
   - An observed empty result is `no_speech`, never a failure.
   - Invalid raw timestamps keep their text but lose their timing; they are
     counted and never clamped.
   - Recognizer and seq are recorded locally so an unknown outcome is not silently
     resent.
   - It never switches to the microphone and never returns success-shaped empty
     output.
   - There is no on-device fallback in the submission.
3. **On-device ASR: none in the submission (owner decision).** whisper.cpp `tiny.en`
   is the only viable on-device option measured on the weakest phone. It is faster
   than real time at p50, WER 0.06, 239 MB peak, and works offline. It remains an
   unbuilt fallback option.
   - APK cost: the release APK is about 23 MB today. Bundling tiny.en would add
     about 32 MB (q5_1), about 42 MB (q8_0) or about 75 MB (f16); downloading it
     on first use would add about 2 MB.
   - q5_1 and q8_0 accuracy was not measured on the phone.
   - base.en is not viable live (p95 2.2x real time).
   - Vosk small-en is rejected: higher disagreement, a polarity change, numbers as
     words.
4. **Chunk length: keep the owner-approved 10 s.** Real chunks satisfy every format,
   size and offset check. 15 s gives no consistent accuracy gain: turbo is worse,
   large-v3 slightly better. 10 s also matches the 10 s billing minimum and
   lowers live latency. No separately approved change is requested.
5. **OCR route: on-device ML Kit, as in 0005; no server Tesseract.** On identical
   bytes ML Kit disagrees with the labelled regions less than half as often as
   Tesseract. Server OCR would need frame uploads and CPU outside the budget.
6. **Sampling:** keep the shipped 1 Hz change trigger with 5 s heartbeat over fixed
   5 s sampling. Change the per-minute cap's behavior through #110 (owner decision
   there); until then capped intervals are a known coverage gap.

### #14 checklist closure matrix

| #14 item | Status | Evidence or decision |
| --- | --- | --- |
| Candidates (Groq v3/turbo, Vosk small-en, whisper.cpp tiny.en/base.en) | Evidence | ASR table above, paired on identical bytes |
| Metrics: WER, critical entities, timestamp drift, wall clock, quota, battery/thermal | Evidence, with limitations | Tables above. Drift is cue-anchor disagreement only. Battery drain not measured (charging); thermal status recorded per run |
| Chunked input at AN-04 sizes; offsets survive; chunk at or below the file cap | Evidence | Real AN-04 chunks seq 0 to 16 verified; 180,000 ms boundary asserted by the passing test |
| Data-flow note | Done in #92 | Hosted sends consented audio to a US processor; on-device sends nothing |
| Fallback rule: never silent | Accepted | Item 2 above; `asr_policy.py` and tests; handed to #20 |
| Record RFC-D27 | Accepted | Owner, 6 October 2026 |
| ML Kit vs Tesseract on the same frames | Evidence, with limitation | Identical bundle bytes; Tesseract on newly captured frames not run |
| Decide on-device vs server OCR, with reasoning | Decided | Item 5; 0005 Accepted for the route; no server Tesseract |
| Missed-text rate, 5 s fixed vs change-triggered | Evidence, with limitation | Seven provisional card windows; no full-screen rate claimed |
| Dedupe persistent text; keep frame times and boxes | Evidence | Shipped observations keep `{text, box, frame_pts}`; per-clip track preservation in the private package |
| On-device cost: ms per frame, battery over 3 min on the weakest phone | Partly measured | ms per frame measured; three-minute battery not measured (charging, owner time decision) |

**Not measured (owner time decision, 6 October 2026):**
- Battery drain and repeated capture-only, capture plus OCR and capture plus ASR
  resource runs (one charging capture only).
- Tesseract on newly captured frames.
- ASR on the real captured PCM chunks.
- Independent speech-interval review for timestamp accuracy.
- Bytes of the final seq-17 chunk and the finished manifest.

These remain open limitations, not results.
