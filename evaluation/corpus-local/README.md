# Local evaluation snapshot (rights pending)

**Version: `res01-local-frozen-2026-10-04`. Status: locally validated
reduced-scope snapshot, `kind: frozen-local`; not the RES-01 frozen corpus.**
The project owner accepted the eleven-clip selection for **local evaluation,
no model training** on **2026-10-04 at 19:26:06.979 +03:00** and asked for the
set to be fixed at this version. Rights clearance, credits and permission
evidence remain pending on every clip, as the clip rows record. No further
captions, cards or scenarios are planned for this local scope; completing the
pending rights, coverage and review work remains
[RES-01](https://github.com/natnael-solomon/ovrly/issues/8).

| Item | Recorded value |
| --- | --- |
| Clips | 11, unchanged media identities |
| Split | 7 dev / 4 test, unchanged |
| Original occurrences | 259 |
| Final decisions | 264, including five disclosed visual discoveries |
| Negative clip | clip-m, user-confirmed, zero eligible decisions |
| Protocol | One provenance-labeled pass plus adjudication |
| Kind / command | `frozen-local` / `--frozen-local` |
| Rights | `rights.clearance: pending` on all 11 clips; `rights.basis: pending` on 7; `allows_redistribution: false` everywhere |
| Coverage | Empty (unconfirmed) on all 11 clips |
| Review status | All 11 passes and 11 adjudications `provisional`, `ai-assisted` |

All four JSONL files are byte-identical to the
[content candidate](../freeze-candidate/README.md). Original passes, actual
AI-assisted authorship, corrections, source hashes, caption/card timing bases,
four human main-argument notes and evidence assessments are preserved.
The manifest pins each file and the preceding candidate manifest.

## Acceptance and limits

`dataset.json.freeze_approval` records the owner's local-use acceptance, the
exact clip IDs and the hash of the updated private rights checklist. The
private checklist and receipt remain in controlled storage; no private paths
or permission documents are included here.

The `rights.clearance: pending` fields in the clip records are the current
rights state, not a superseded one. The manifest's local acceptance is the
owner's agreement to use this fixed set locally. It does not invent a new
published license, assert that the project owner owns third-party material,
represent project acceptance as rights-holder consent, or clear any clip.
Existing source license and attribution information remains available in
those clip records.

The acceptance covers the fixed set and recorded review limitations. It does
not turn original provisional annotations into an independently reviewed human
pass, change cue envelopes into word timestamps, or assert any scenario
coverage. Unknown/absent scenario slices must be reported as not evaluated.
Clip-m's confirmed negative does not certify its language; exclude it from
English ASR claims unless separately established.

Known inconsistencies left for the owner to resolve in a new version:

- `freeze_approval.credits_signoff` is `approved` (a schema constant) while
  the four Commons clips still record unfinished credits and the six social
  clips and clip-m record no permission evidence (`rights.basis: pending`).
  The clip rows are authoritative for rights state; the manifest field is left
  unchanged here because editing it would alter a validated artifact.
- `freeze_approval.approved_by` is the schema constant `project-owner`, not a
  named person.

No model training, hosted processing, media redistribution or deployment
is authorized by this snapshot. Metadata delivery in Git is a separate action;
it does not expand permitted media use. Dev supports local evaluation and
iteration; test remains held out from prompt tuning and routine regression.
Fixing test labels does not authorize running a held-out model evaluation.

The stricter `--frozen` full-coverage benchmark contract remains unchanged and
rejects this snapshot by design. Rights clearance, coverage confirmation,
complete review and the section 5 handoff in the
[evaluation README](../README.md#5-full-coverage-freeze-and-handoff) remain
open work for RES-01.

## BE-01 handoff

This reference complements [BE-01](https://github.com/natnael-solomon/ovrly/issues/11)
without changing its router, experiment schema or provider decisions.
The corpus manifest is **not** a BE-01 input file: `frozen-local` describes
the owner's local-use acceptance, while BE-01's `be01-experimental-v2` input describes
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

Local-use acceptance does not change BE-01's
`hosted_processing_approved` attestation. Any separately granted provider-use
approval must stay attached to its exact experiment input and scope; do not
infer it from this snapshot or retroactively revoke an independent approval.
Keep real inputs and recordings outside Git. Plan mode and mocked tests are
not a hosted run or evidence of free-tier entitlements.

## Verify and use

```text
python evaluation/validate.py evaluation/corpus-local --frozen-local
python evaluation/validate.py evaluation/corpus-local --frozen-local --media-root <controlled-intake-root>
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
```

The first command checks metadata only; the second also checks every exact
media hash without uploading anything. `--frozen` must reject this directory.
Media paths are relative to the
controlled intake, not this directory. Source captions and screenshots remain
outside Git; normalized decisions are not verbatim ASR or full-screen OCR
ground truth.

Snapshot manifest SHA-256:
`ac11b53dd4063ad79b02f94f4af3b509fd782527731c1b619d0ceadfe596bc41`

Owner-updated rights-checklist SHA-256:
`dd46f6016858a5df85243e2233ae8a913cefda5a64f9f0db175239468b594981`

Keep this version immutable. A correction requires a new version, an explicit
change record and renewed acceptance for affected content/scope; do not refresh
hashes to conceal edits. Tests pin this manifest and preserve its predecessor.
Hash integrity detects changes, not forged acceptance; human provenance remains
part of the review record. Repository commit/PR delivery requires separate
authorization, does not alter this snapshot and does not close RES-01.
