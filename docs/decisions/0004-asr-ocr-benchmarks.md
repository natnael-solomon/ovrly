# 0004: ASR and screen-text benchmark gates

**Decision ID and question:** RFC-D27: which zero-cost transcription adapter
should BE-07 use? Related OCR/sampling questions: RFC-D28, D68 and D71.

**Status:** Open, recorded 4 October 2026; updated 5 October. Offline scoring
and an opt-in paired Groq trial runner are implemented. No winner or production
chunk size is accepted.

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

**Chosen option and rationale:** No model selected. Use the
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
[local snapshot](../../evaluation/corpus-local/README.md).
