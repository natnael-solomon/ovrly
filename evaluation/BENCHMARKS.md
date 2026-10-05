# RES-02 ASR and screen-text benchmarks

`benchmark.py` is the offline part of [#14](https://github.com/natnael-solomon/ovrly/issues/14).
The non-phone tooling, evidence and private device-test handoff are tracked in
child [#92](https://github.com/natnael-solomon/ovrly/issues/92); completing that
slice does not close #14's device measurements or final decision.
It prepares a dev-only inventory and scores operator-supplied observations.
It never runs a model, downloads media, uploads audio/frames, loads a provider
key or changes a corpus snapshot. Its synthetic tests are not model results.
The separate opt-in `asr_experiment.py` command can prepare and execute explicitly
authorized Groq trials; it is never invoked for hosted inference in CI.
The adapter decision remains [RFC-D27](../docs/decisions/0004-asr-ocr-benchmarks.md).

## Start from the merged RES-01 handoff

Use `corpus-local/`, version `res01-local-frozen-2026-10-04`, not the historical
draft or content candidate. The accepted local selection, reconciled speech
intervals, supplied cards and clip-m confirmation do not need to be repeated.
The snapshot still records pending rights, provisional reviews and unconfirmed
coverage. Its normalized propositions are neither verbatim transcripts nor
full-screen OCR ground truth. Do not transform them into either.

The existing RES-01 intake contains six final user-reviewed social SRTs and
clip-d's source SRT. Reuse those files read-only, with their recorded hashes;
the earlier two-file caption candidate is superseded by the final reviewed
six-file revision. The existing BE-01 50-window set is a separate extraction
experiment and must not be regenerated for this benchmark.

From the repository root, Python 3.11+, no additional dependencies:

```text
python evaluation/benchmark.py plan
python evaluation/benchmark.py schema asr
python evaluation/benchmark.py schema ocr
python evaluation/benchmark.py score .scratch/res02/asr-observations.json
python evaluation/benchmark.py score .scratch/res02/ocr-observations.json
python evaluation/benchmark.py score .scratch/res02/asr-observations.json --media-root <controlled-intake-root>
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
```

`--corpus <directory>` before the subcommand selects a different validated
`frozen-local` or `frozen` snapshot. No automatic downgrade to draft is allowed.
`plan` checks corpus metadata/hashes and reports missing inputs; it does not
open media. `score` reads one complete JSON observation object and prints a
JSON result to stdout. Keep inputs and redirected results in ignored
`.scratch/res02/` or controlled storage, never Git by default.

Exit codes: **0** means scoring/validation completed; **1** means scoring
completed but at least one ASR chunk or OCR frame explicitly failed; **2**
means invalid input or a local I/O failure. Errors go to stderr. Accuracy
errors do not make the CLI fail: no quality threshold has been approved.
Scored outputs keep `decision: pending_measurements_and_review`. Missing metrics
are `null`, not zero or a passing score.

## Observation contract

`schema asr|ocr` prints the authoritative, closed `res02-v1` input schema.
Unknown/missing fields, duplicate JSON keys, non-finite numbers and unknown
models fail. A contract change needs a new version rather than rewriting
historical results. `tests/test_benchmark.py` contains complete invented ASR
and OCR examples; do not reuse them as real evidence.

Common fields identify the run, clip, model and exact `model_revision`, actual
`hardware` (model/OS only, no serials), `provenance`, and nonempty `limitations`.
Use `kind: synthetic` and a null corpus hash for invented observations.
`kind: local-measurement` requires the exact corpus manifest SHA-256, a known
**dev** clip and its full duration on the `media` timebase. This first version
does not score a shortened excerpt or a live-capture reference against a
whole-media clip. Synthetic cases can test the `capture` timebase.

The scorer rejects the four holdout IDs in real runs. Holdout evaluation needs
separate authorization and a separately reviewed runner path, not changing a
clip's split. The fixed dev IDs are social-01/02/03/04/05/07 and clip-d.

Local Groq observations require their own nonempty, opaque
`hosted_processing_approval` reference. Other observations may use null.
This records a separate attestation, not automatic rights verification, and
never grants permission to run a provider. No private consent document belongs
in the input or output intended for publication.

Input and reference hashes are included in results. OCR additionally hashes
the ordered frame IDs, presentation times and image hashes: compare candidates
only with equal frame/reference identities, scopes, thresholds and sampling
selections. Match ASR references, audio bytes, chunk boundaries, format and
settings across candidates; preserve the source audio/chunk hashes and raw
provider/model outputs in the controlled run record. The scorer cannot prove
that declared outputs came from the model or hardware named by the operator.
Without `--media-root`, results explicitly say media bytes were not verified.
With it, the existing validator checks all corpus media containment and hashes;
this does not verify the derived chunk/frame bytes or model execution.

## ASR protocol and metrics

Candidates are Groq `whisper-large-v3`/`whisper-large-v3-turbo`, on-device
Vosk small-en and, if a phone can handle them, whisper.cpp tiny.en/base.en.
Pin the downloaded model revision/hash and runtime in provenance. No server
faster-whisper or paid OpenAI whisper-1 route is provided.

| Field / metric | Meaning |
| --- | --- |
| `reference` | Ordered nonoverlapping `{start_ms, end_ms, text}` segments covering the spoken reference; silence need not have a segment. Use exact reviewed speech, including negation, numbers and repetitions. |
| `reference_basis` | `media-reviewed-verbatim` or `subtitle-reference`. Subtitle agreement must be reported as such, not certified transcription accuracy. |
| `chunks` | One attempt per consecutive `seq`, covering the whole clip contiguously from zero, with actual encoded `size_bytes`, elapsed `wall_ms`, `status` and segment observations. This version rejects gaps/overlaps/missing tails rather than silently excluding them. |
| Segment offsets | Each hypothesis segment is relative to its chunk. The scorer validates bounds and adds `chunk.start_ms` exactly once, returning timebase-relative intervals without text. |
| Failure | `ASR_UNAVAILABLE` requires no segments. Retain the failed chunk; missing speech counts as deletions and the failure count remains visible. `ok` with no segments means an observed no-speech result, not an exception fallback. |
| WER | Minimum token edit distance, with substitution/deletion/insertion counts, divided by total reference tokens. It can exceed one. Empty-reference WER is null; hallucinated insertions remain counted. |
| Normalization | NFKC, case-folding and punctuation removal; retain apostrophes, signs, numeric separators, percent and degree symbols. Do not convert spelled-out numbers, units or `favour`/`favor`. No semantic normalization. |
| `entities` | `{category, start_token, end_token}` spans in the concatenated normalized reference, zero-based, end-exclusive. Categories: negation, number, name, date, unit. Report incorrect spans and denominators independently. |
| Entity errors | Any substituted/deleted span token or insertion strictly inside a span makes it incorrect. Mark a full polarity-bearing phrase to catch inserted negation. Insertions outside annotated spans are only in WER; this is not automatic detection of every critical error. Alignment ties prefer diagonal, deletion, then insertion. |
| `timestamp_pairs` | Explicit reviewer-matched reference index and hypothesis `(chunk_seq, segment_index)` for the same spoken interval, one-to-one. Never pair by array position alone. |
| Timestamp drift | Signed start/end and absolute endpoint p50/p95/mean/max; only with `timing_basis: media-reviewed`. Cue-envelope input must leave pairs empty. Unpaired reference segments are reported, not assigned zero drift. |
| Latency | Nearest-rank p50/p95 plus mean/max of elapsed time per attempt, including failed attempts. It is not end-to-end live capture latency. Record cold/warm, transport and runtime conditions in provenance; compare separate runs, not pooled machines. |

The reference/hypothesis token ceiling is 5,000 each. A score covers one clip,
candidate and run; do not average clip WER percentages for a corpus WER.
Sum edit counts and reference denominators, retain failed cases and report
per-clip/critical-entity slices before any selection. No candidate is selected
by this implementation.

### Chunk and free-tier planning

AN-04 (#26) proposes **10 and 15 seconds**, not an accepted format. The plan
lists 18 or 12 requests per three-minute clip. A hypothetical 16 kHz, mono,
16-bit PCM WAV with a minimal 44-byte header is respectively **320,044** or
**480,044 bytes**. These are calculated fixtures, not encoded-device evidence.
Actual metadata, encoding, final tails, uploads and timestamps must be tested
with AN-04 output before acceptance.

Groq inputs require `limits`: `file_cap_bytes`, `minimum_audio_ms`,
`audio_ms_per_hour`, `requests_per_day` and dated `provenance` from the actual
account/provider. No historic issue quota or upload cap is hard-coded as a
current entitlement. Actual chunk bytes must be at or below the recorded cap.
On-device inputs normally use `limits: null`.

The scorer sums `max(chunk duration, minimum_audio_ms)` per attempted request
and reports fractions of supplied hourly audio/daily request limits. These
are **estimates**, not billed consumption. `is_three_minute_clip` says whether
the result actually covers three minutes; shorter clips are not relabeled.
Retries are excluded from this one-attempt contract; record them separately
and reconcile the full request ledger with before/after account counters.
No-card status, current free-tier limits and shared-account contention need
actual evidence, not a calculated request count.

## Opt-in hosted trial using existing SRTs

`asr_experiment.py prepare` verifies the unchanged corpus and original media,
reads existing hash-pinned SRTs, and writes only derived WAV chunks and a run
plan to new controlled storage. It does not create a corpus or a transcript
window dataset. UTF-8/BOM and CRLF SRTs are supported; malformed numbering,
overlaps, overruns, empty speech and unreviewed markup fail rather than being
silently repaired. Wording and cue envelopes remain unchanged.

Supply a private JSON specification with `approval` (an opaque recorded audio
permission reference), `references` (one `{clip_id, path, sha256}` per selected
dev clip, pointing to existing SRT files) and the ASR `limits` object above.
The specification cannot select holdouts. For development under the accepted
BE-01 account assumptions, limits provenance must explicitly distinguish
dated public-plan assumptions from actually observed account headers.
Neither a local freeze nor transcript-to-another-provider permission is
audio-to-Groq consent. Do not commit the specification or its private paths.

From the repository root, with `uv` and existing backend dependencies:

```text
uv run --project backend --frozen --with av==17.1.0 python evaluation/asr_experiment.py prepare --media-root <existing-intake> --specification <private-spec.json> --output <new-private-run-directory>
uv run --project backend --frozen --env-file backend/.env.experiments python evaluation/asr_experiment.py run <private-run-directory>/plan.json --approve-plan-sha256 <printed-plan-digest>
uv run --project backend --frozen python evaluation/asr_experiment.py summarize <private-run-directory>/plan.json --approve-plan-sha256 <same-plan-digest>
```

PyAV 17.1.0 is an optional preparation-only dependency, not a backend service
dependency. HTTPX comes from the existing backend lock. Credentials are read
only from `GROQ_API_KEY`, not CLI arguments, plans or outputs. `backend/.env.experiments`
is ignored. Never paste credentials into logs, tests, chat or artifacts.

The decoder retains the media origin, uses mono 16 kHz PCM16, and creates
paired nonoverlapping 10/15-second trials including the exact tail. Coarse
container timestamps are reconstructed from the continuous audio sample clock
only within one source timestamp tick; larger discontinuities fail. The plan
records quantization adjustments, zero-filled leading/trailing samples and
samples outside the pinned video interval. Derived PCM is not AN-04 output.

`run` rechecks the approved plan, source-reference and chunk hashes before
uploading. It sends identical bytes to both Whisper aliases, English,
temperature zero, verbose JSON, no prompt, and alternates model order per
chunk. Requests start at least 3.1 seconds apart globally (below 20 RPM,
not a guarantee against other account traffic). It never retries or follows
redirects and stops on any transport or non-200 response. Private attempt
records preserve raw responses, wall time and quota headers without credentials.
An exclusive start marker prevents accidental replay after interruption;
re-running the same completed plan skips recorded successes, not reuploads.
Failed or interrupted attempts need explicit review, not deletion of the ledger
to force retries. Exit 2 means failure; partial runs are not complete comparisons.

`summarize` refuses partial runs and existing output directories. It produces
per-clip scorer inputs/results and a token-weighted summary, never an average
of clip WER percentages. It compares full chunk response text with the original
SRT reference. Hypothesis intervals are **chunk envelopes**, not the model's
word boundaries; raw model timestamps remain in the attempt records. No
timestamp drift or critical-entity rate is inferred without reviewed pairings
or span labels. Model aliases are recorded honestly, not claimed to be immutable
weights. Keep recordings, plans, responses and scores local unless separately
authorized for publication.

## OCR and sampling protocol

Compare ML Kit Latin v2 and Tesseract eng fast/best on identical captured
frames. Tesseract may be a local workstation comparator; its timing must not
be labeled phone timing or production-server feasibility.

### Completed workstation evidence and reproducible helpers

The controlled non-phone run reuses the six supplied RES-01 screenshots and
all seven dev videos. It adds only derived frame observations and selected
image-region labels, not a new corpus or transcript set. Measurements and
source paths remain private. Two complementary scopes must stay separate:

- **Screenshot recognition:** image-only, AI-assisted labels for selected
  nonoverlapping text regions, fixed before OCR output inspection. Both engines
  see the same complete original image, without oracle cropping. Words are
  assigned to a region by their center in native reading order. Unannotated
  words are reported separately, not scored as false positives. This metric is
  not the line-IoU metric of the general scorer and must not be pooled with it.
- **Source-video sampling:** actual media presentation times from decoded
  frames, not filenames or invented capture times. The experiment probes at
  1 Hz, compares 64x64 grayscale mean absolute change against the last selected
  image (threshold 12), and adds a 5-second heartbeat. Its fixed comparator
  takes the first probe at/after each 5-second tick. Parameters were fixed before
  recognition or window scoring. Coverage uses distinct existing visual-card
  intervals, not duplicate claim rows or newly inferred visibility bounds.
  Source video can end before the audio-derived clip duration: an unavailable
  final 1 Hz probe is explicitly recorded, never filled with an invented frame.
  Missing fixed samples, internal probe gaps and a tail beyond one probe period
  fail. The controlled tail audit records two unavailable final fractional-tail
  probes; neither changes the supplied-card-window coverage denominators.

Source samples use a 720-pixel long edge and Pillow JPEG quality 72, matching
the current Android capture *settings*, not Android encoder byte parity. The
supplied screenshots remain at their original resolution. References cover
only supplied windows/selected screenshot regions; neither result establishes
a full-screen missed-text rate. A change-triggered sample can still miss a
brief card. Do not replace the Android policy with this experimental threshold.

`ocr_experiment.py` exposes `sample_video`, `recognize`,
`selected_region_errors`, `normalized_box`, and `window_coverage`. It performs
no network calls or model downloads. Optional local execution dependencies:

```text
uv run --project backend --frozen --with av==17.1.0 --with pillow==11.3.0 --with tesserocr==2.9.1 python <controlled-ocr-run-script> prepare
uv run --project backend --frozen --with pillow==11.3.0 --with tesserocr==2.9.1 python <controlled-ocr-run-script> run
uv run --project backend --frozen --with pillow==11.3.0 --with tesserocr==2.9.1 python <controlled-ocr-run-script> summarize
```

Keep the operator script, source-reference specification, model manifests and
outputs together in controlled storage. The executed script is part of that
private handoff, not a repository file containing personal paths or references.
For a new run, configure Tesseract LSTM-only, sparse-text segmentation, English,
`OMP_THREAD_LIMIT=1`, and disabled adaptive learning; preserve native word
confidence and pixel boxes. Screenshot timings use three repetitions per model
with alternating model order, with initialization recorded separately. Score
accuracy once per screenshot, not three times to inflate the denominator.

The executed runtime was tesserocr 2.9.1 / Tesseract 5.5.1 / Leptonica 1.85.0.
Model pins from the public `tesseract-ocr` repositories:

| Model | Repository revision | `eng.traineddata` SHA-256 |
| --- | --- | --- |
| `tessdata_fast` | `87416418657359cb625c412a48b6e1d6d41c29bd` | `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` |
| `tessdata_best` | `e12c65a915945e4c28e237a9b52bc4a8f39a0cec` | `8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba` |

The native engine emitted box warnings on some source frames; retain that
execution disclosure. A textless iterator entry initially stopped the run.
The wrapper now checks `Empty(WORD)` before retrieving text, with a regression
test; it does not catch arbitrary recognition exceptions as empty success.
Completed observations were retained and only unfinished clips resumed.

Both models' source observations feed the existing dedup scorer with native
word boxes and no invented full-screen gold. An independent multiset audit
verifies every input word/time/box occurs exactly once across output tracks,
and both models' frame-set identities match. This proves preservation, not
semantic track precision: common words in changing captions can still need
line/context-aware review. Do not infer continuous visibility from a track.

### Timestamp and quota audit without new uploads

`asr_experiment.subtitle_timing(reference, chunks)` audits raw provider segments
against complete SRT cue envelopes. Token alignment must find an exact,
contiguous match beginning and ending on cue boundaries; a segment may cover
multiple whole cues. Partial cues and lexical errors are left unmatched.
The report retains coverage counts, signed/absolute endpoint differences and
raw out-of-chunk segments. This is **selected subtitle-cue disagreement**,
not independently verified word timing or Android capture-clock drift.
It does not relax the stricter `timestamp_pairs` contract of the general scorer.

The existing clip-d trials include exact 0..180,000 ms prefixes: their recorded
10-second and 15-second chunks provide measured three-minute request counts,
submitted audio duration, WAV bytes and observed daily-limit headers without
additional requests. The headers do not expose audio consumption; minimum-
billed audio/hourly fractions remain explicitly labeled estimates under the
accepted account assumptions. Request-balance deltas alone are not billing
proof and can reflect refill or shared-account traffic.

Raw provider timestamp overrun is a real failure mode, especially on short
tails; a successful HTTP response does not establish valid timing. Validate
segment bounds before adding the chunk offset. Never silently clamp an invalid
segment or expose the WER runner's whole-chunk envelope as model word timing.
BE-07/AN-04 must explicitly handle invalid timing while preserving the raw
failure and the required unavailable/fallback behavior.

### Non-phone conclusions and remaining integration

The controlled handoff contains the paired hosted scores, critical-span review,
cue audit, three-minute request ledger, same-image Tesseract comparison,
source-window sampling, exact dedup-preservation audit and encoded-image
bandwidth/local-wall-time accounting. No further model uploads are needed for
those artifacts. Raw results are not published by this repository change.

Tesseract remains a workstation comparator, **not a production server OCR
route**: that route adds frame transfer and CPU outside the accepted hosting
budget. ML Kit remains the intended on-device comparison. The current Android
`CaptureFiles` implementation writes continuous mono 16 kHz signed PCM16 and
sampled JPEGs; it does not yet supply final AN-04 chunk envelopes. The WAV trial
matches its PCM format but does not prove its capture clock or chunk producer.

The remaining empirical handoff requires the phone recognizers, ML Kit on the
same frames, runtime/battery/thermal measurements, and real AN-04 output.
Final adapter/fallback and device sampling acceptance remain conditional on
that evidence. Do not reopen #8's corpus/transcript work or #9's closed capture
compatibility matrix to obtain it.

### General OCR observation contract

`reference` holds each text occurrence's ID, visibility interval, verbatim text
and box. Reappearance is a new occurrence, not globally deduplicated gold.
Boxes are `[left, top, right, bottom]` in integer coordinates normalized to
0..10,000 against the actual frame dimensions; all must have positive area.
Split ground truth at text/layout changes. Supply `full-screen-media-reviewed`
only after reviewing all readable text; supplied cards alone are
`selected-regions`. Neither label certifies chart interpretation.

`frames` have unique IDs, increasing actual `presentation_ms`, SHA-256 of the
exact captured image bytes, measured `wall_ms`, status and detected text/boxes.
`OCR_UNAVAILABLE` has no detections and remains a failed frame.
Visible references use half-open intervals: text ending at a frame's time is
not present. Frame matching greedily takes highest box IoU first, one-to-one,
above the declared `iou_threshold_permille`; ties use input order. Keep the
threshold fixed across candidates. No text similarity is used to cherry-pick
the spatial assignment.

The report includes frame-weighted word edits, unmatched detections and failed
frames. Missing detections count as deletions. Full-screen references count
unmatched text as insertions; selected-region references report unmatched
detections separately, without pretending unannotated text is false positive
text. OCR line segmentation can affect box matching; retain raw outputs and
inspect split/merged-line failures rather than treating this metric as a
segmentation-independent OCR score.

Provide `fixed_frame_ids` for scheduled times 0, 5,000, 10,000 ms, etc., and
declare `fixed_tolerance_ms` (0..1,000) for capture delay. Each selected actual
presentation time must fall on or after its scheduled tick and within that
tolerance. Missing samples fail the input; do not quietly drop them.
`change_triggered_frame_ids` identifies actual samples from an independently
recorded change-triggered run. Record the trigger, thresholds and latency in
provenance; this tool does not implement Android's trigger or choose samples
from the ground-truth labels.

Both policies report **temporal misses** (no selected frame while text was
visible) separately from **recognition misses** (no exact normalized text
with a matching box). A temporal hit is not proof OCR read it. Empty reference
denominators are null. Five-second misses on selected cards do not establish
a full-screen missed-text rate.

Deduplicated tracks join matching normalized text and overlapping boxes in
successive supplied frames, stopping on absence/failure. Every observation
retains its frame ID, actual presentation time and box. A track's first/last
sample is not a measured continuous visibility interval; no duration is
inferred across unsampled gaps. Inspect the original frames for that evidence.

## Device resources and outstanding evidence

`resources` is null until measured. Otherwise record the exact interval,
start/end battery in permille, start/end temperature in millicelsius, thermal
status names and measurement provenance. Keep charging/screen/network state,
temperature sensor source and repetitions in provenance. A three-minute run
on the weakest authorized phone is required by #14; shorter readings and
workstation timing do not replace it.

| Work | Gate, not a blanket wait |
| --- | --- |
| Offline input validation, scoring, timestamp-offset tests, synthetic failure tests and run protocol | Implemented without #9. CI performs only these offline checks and metadata planning. |
| Real dev ASR comparison | Hosted trials, selected critical-span review, cue-envelope diagnostics and actual three-minute request-prefix audits completed locally; phone candidates and capture-clock timing remain separate. |
| Final 10/15-second chunk choice | Real AN-04 capture output and compatibility evidence; proposals alone do not decide it. |
| ML Kit comparison, fixed/change sampling measurements | Local Tesseract comparison and supplied-window source-video sampling completed. ML Kit and actual device-trigger behavior remain unmeasured; limited windows are not full-screen gold. |
| On-device latency, battery, heat and offline model viability | Authorized physical phone evidence, including the weakest test phone; mocks do not substitute. |
| Production integration and failure fallback | BE-07 / AN-06 after reviewed decisions. This scorer implements no production adapter or fallback transport. |

#9's main-branch evidence records the Samsung Galaxy A21s (SM-A217F), Android
12, readable captures for YouTube Shorts/TikTok/Instagram, and a three-minute
TikTok capture. That establishes capture feasibility, not on-device ASR/OCR
speed, comparative accuracy or battery/thermal cost. Those measurements remain
necessary; #9 is no longer a blanket blocker.

No final adapter or chunk-size selection is made here. Private model
measurements must retain their reference and device limitations. #8's recorded
rights/coverage limitations are preserved, and #14 is not closed by offline tests.
