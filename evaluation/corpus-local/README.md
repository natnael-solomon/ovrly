# Frozen local evaluation corpus

**Frozen version: `res01-local-frozen-2026-10-04`.**
The project owner approved the eleven clips and credits in the controlled
rights checklist and explicitly requested freeze on **2026-10-04 at
19:26:06.979 +03:00**. This is the agreed **local evaluation, no model training**
scope. No additional captions, cards, scenarios or repeated approval are needed
for this freeze.

| Item | Frozen value |
| --- | --- |
| Clips | 11, unchanged media identities |
| Split | 7 dev / 4 test, unchanged |
| Original occurrences | 259 |
| Final decisions | 264, including five disclosed visual discoveries |
| Negative clip | clip-m, user-confirmed, zero eligible decisions |
| Protocol | One provenance-labeled pass plus adjudication |
| Kind / command | `frozen-local` / `--frozen-local` |

All four JSONL files are byte-identical to the
[content candidate](../freeze-candidate/README.md). Original passes, actual
AI-assisted authorship, corrections, source hashes, caption/card timing bases,
four human main-argument notes and evidence assessments are preserved.
The manifest pins each file and the preceding candidate manifest.

## Approval and limits

`dataset.json.freeze_approval` records the owner's local-use acceptance,
completed credit sign-off, exact clip IDs and hash of the updated private
rights checklist. The private checklist and receipt remain in controlled
storage; no private paths or permission documents are included here.

Historical `rights.clearance: pending` fields in the unchanged clip records
describe evidence before this approval. The manifest's separate local approval
is the current authorization for this frozen scope. It does not invent a new
published license, assert that the project owner owns third-party material, or
represent project approval as rights-holder consent. Existing source license
and attribution information remains available in those clip records.

The freeze accepts the fixed set and recorded review limitations. It does not
turn original provisional annotations into an independently reviewed human
pass, change cue envelopes into word timestamps, or assert complete scenario
coverage. Unknown/absent scenario slices must be reported as not evaluated.
Clip-m's confirmed negative does not certify its language; exclude it from
English ASR claims unless separately established.

No model training, hosted processing, media redistribution or deployment
is authorized by this freeze. Metadata delivery in Git is a separate action;
it does not expand permitted media use. Dev supports local evaluation and
iteration; test remains held out from prompt tuning and routine regression.
Freezing test labels does not authorize running a held-out model evaluation.

The stricter `--frozen` full-coverage benchmark contract remains unchanged and
rejects this local profile. Local freeze is complete; broader certification
and any future expanded use are separate work, not blockers to this snapshot.

## BE-01 handoff

This reference complements [BE-01](https://github.com/natnael-solomon/ovrly/issues/11)
without changing its router, experiment schema or provider decisions.
The corpus manifest is **not** a BE-01 input file: `frozen-local` describes
reference approval, while BE-01's `be01-experimental-v2` input describes
transcript observations and windows.

Only the seven dev clips are candidates for that experiment: social-01,
social-02, social-03, social-04, social-05, social-07 and clip-d. Keep clip-k,
clip-f, clip-i and clip-m, including their labels, out of prompt tuning and
the 50-window router comparison.

Build any separately versioned BE-01 input from actual controlled captions or
source subtitles, not the normalized `proposition` fields here. Preserve exact
text, source hashes, supplied speaker labels (or null), parent/cue envelopes and
window selection provenance. BE-01's observation-relative character offsets
are not this corpus's media-relative millisecond intervals. Neither the corpus
schema version `2` nor `gold_id`/`proposition_id` replaces BE-01's experiment
version or observation IDs.

Local freeze approval does not change BE-01's
`hosted_processing_approved` attestation. Any separately granted provider-use
approval must stay attached to its exact experiment input and scope; do not
infer it from this freeze or retroactively revoke an independent approval.
Keep real inputs and recordings outside Git. Plan mode and mocked tests are
not a hosted run or evidence of free-tier entitlements.

## Verify and use

```text
python evaluation/validate.py evaluation/corpus-local --frozen-local
python evaluation/validate.py evaluation/corpus-local --frozen-local --media-root <controlled-intake-root>
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
```

The first command checks metadata only; the second also checks every exact
media hash without uploading anything. Media paths are relative to the
controlled intake, not this directory. Source captions and screenshots remain
outside Git; normalized decisions are not verbatim ASR or full-screen OCR
ground truth.

Freeze manifest SHA-256:
`ac11b53dd4063ad79b02f94f4af3b509fd782527731c1b619d0ceadfe596bc41`

Owner-updated rights-checklist SHA-256:
`dd46f6016858a5df85243e2233ae8a913cefda5a64f9f0db175239468b594981`

Keep this version immutable. A correction requires a new version, an explicit
change record and renewed approval for affected content/scope; do not refresh
hashes to conceal edits. Tests pin this manifest and preserve its predecessor.
Hash integrity detects changes, not forged approval; human provenance remains
part of the review record. Repository commit/PR delivery requires separate
authorization and does not alter the already-created local freeze.
