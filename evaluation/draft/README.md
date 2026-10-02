# RES-06 working dataset

**Version: `res06-draft-2026-10-02`. Status: provisional, not frozen.**
This is the reduced-scope metadata handoff for
[RES-06](https://github.com/natnael-solomon/ovrly/issues/65), not completion of
[RES-01](https://github.com/natnael-solomon/ovrly/issues/8).
No media, screenshots, full transcripts, private permission records or model
outputs from a scoring run are included. No training, uploads or metrics were
performed.

## Inventory and split

| Split | Clips |
| --- | --- |
| Dev (7) | social-01 (racism/welfare), social-02 (social democracy), social-03 (elite-led revolutions), social-04 (Harvard admissions), social-05 (wage theft/cooperatives), social-07 (caste/class), clip-d (public money/public code) |
| Test (4) | clip-k (continuous sidewalks), clip-f (solar geoengineering), clip-i (microplastics), clip-m (The True Glory negative candidate) |

The user approved this split on 2026-10-02, superseding 9/2 by moving f and i
to test. All six social clips belong to one connected topic component and
stay together. Repost groups are eleven user-reported singletons; repost
examples are not required. Byte identity and declared creator/topic/repost
groups cannot cross splits. These checks cannot establish undeclared
near-duplicate, prompt-tuning or semantic isolation.

The eleven media files total 1,480,665 ms and 492,428,279 bytes. Each clip
retains its public source URL and exact media SHA-256. Media paths are
relative to the controlled intake root, not this directory. Existing creator
group punctuation is encoded as `-dot-` and `-underscore-` for schema-safe
IDs; no group was split or combined.

## Annotation and adjudication

There are eleven selected passes, eleven adjudications, **259 original
occurrences and 259 mapped decisions**. The original set includes 20
screenshot-derived occurrences across eight subjects. Stable source IDs
remain intact, including their historical `-ai-` infix.

Each pass is one logical drafting workflow, including screenshot additions,
not multiple independent annotators. Provenance fields record the actual
`ai-assisted` origin for both annotation and adjudication; independence and
model-blindness flags are false. Neutral display names do not change
authorship. All reviews remain `provisional`: reconciliation of supplied
material is not an exhaustive whole-media pass. The historical `gold_id`
field does not certify truth, human authorship or benchmark readiness.

The selected pass preserves the preceding 259-row timing revision. Final
decisions incorporate the latest supplied racism timings:

| Original ID | Preserved source window | Corrected decision |
| --- | --- | --- |
| social-01-ai-013 | 55-57 s | 57-59 s |
| social-01-ai-014/015/016 | 57-70 s | 59-70 s |

The 55-57 s gap is not silently filled. Original and corrected timing-source
hashes are retained separately. The Newsweek card uses the supplied 23-30 s
interval. Broad windows remain labeled by `timing_basis`; supplied segments
are not word timestamps and screenshot intervals are not frame verification.

Three original exclusions become eligible in adjudication:
`social-01-ai-010` (contextual denial of the described welfare portrayal),
`social-04-ai-013` (race-neutral admissions counterfactual), and
`clip-i-ai-002` (universal 25-particles-per-glass claim). They identify
assessable assertions despite verification/context limitations. This does
not establish their truth. Every original exclusion remains recoverable.
Repeated social-05 income-gap/tax comparisons share a normalized proposition
ID while keeping separate occurrence IDs and intervals.

## Main arguments and evidence

`main-arguments.jsonl` is a separate layer, not another occurrence pass.
Its `human_note` field preserves the four completed `checks.txt` sections
verbatim: social-01, social-03, social-04 and social-07. The social-democracy
heading was empty and wage theft had no section; no human note is fabricated
for those or the Commons clips. Human notes are source assessments, not
independent occurrence gold; model blindness is not attested.

The conclusion/evidence fields retain the selected source-weighted
assessments separately from those notes, with actual synthesis provenance
and 22 distinct source URLs, reading scope and limitations. Evidence does
not become independent merely because multiple links describe the same
study. The exact Harvard 0.67% figure remains unverified, not established
false. Clip-m carries a user-reported negative expectation, not an evidence
verdict or certified empty gold.

## Review limitations

| Material | Remaining review |
| --- | --- |
| All clips | Exhaustive media reconciliation and fine-grained timing are not attested; source assertions and truth assessment are separate |
| social-01 | Supplied timing gap at 55-57 s and uncovered 95-95.734 s tail |
| social-02 | Opening depicted identities unresolved; 38-39 s gap and 58-58.729 s tail |
| social-07 | Uncovered 75-79.179 s tail |
| social-05 | Opening meme quantities unavailable; tiny AP secondary box and EPI inset unreadable; do not invent their claims |
| clip-f | Original subtitle cues include small overlaps; cue envelopes are not word-level timing |
| clip-i | Modality remains `unverified`; final subtitle end was capped by 123 ms to the 70,021 ms media duration |
| clip-m | Sampled visuals contain no readable claims, but audio semantics/language remain unreviewed; provisional empty arrays are not a confirmed negative |

Coverage arrays are deliberately unconfirmed, not assertions of absence.
Final scenario sign-off and acquisition for later self-correction, quoted
misinformation and withdrawn/retracted sources are deferred to RES-01.

All rights clearances remain pending: four Commons clips have published
license references but unfinished credits; the six social clips lack
permission evidence; archival clip-m clearance remains unresolved. Public
availability is not permission. False redistribution flags do not negate a
published license; they avoid asserting completed permission review here.
Repository licensing does not override any third-party rights. No hosted
processing or media redistribution is authorized by this snapshot.

## Validation and changes

From the repository root:

```text
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
python evaluation/validate.py evaluation/draft --draft
python evaluation/validate.py evaluation/draft --draft --media-root <controlled-intake-root>
```

The media root must contain the relative paths declared in `clips.jsonl`.
No download is performed. CI checks metadata and byte hashes of the four
JSONL companions, not media bytes or any pipeline. `--frozen` must reject
this dataset. The [contract](../README.md) documents all validation modes.

Input hashes in `dataset.json`, per-occurrence source references and
adjudication resolutions allow comparison with the controlled intake.
Source files are not redistributed here; reproducing their semantic review
requires authorized access to that intake. Each JSONL file is UTF-8 with LF
endings and is hashed byte-for-byte in `dataset.json`.

Before freeze, complete the outstanding rights, scenarios and media review,
resolve uncertain modality/language, verify the negative, and review final
timings. Preserve this original draft when producing a new version and
record the corrections; do not silently relabel it frozen. A reviewed PR
is still required before issue closure.
