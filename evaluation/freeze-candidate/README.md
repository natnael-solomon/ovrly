# RES-01 content-complete candidate

**Version: `res01-content-2026-10-04`. Status: draft, not frozen.**
Historical candidate: the owner subsequently accepted the clip selection for
local use. The [local snapshot](../corpus-local/README.md) preserves these
records with that acceptance; its rights clearances remain pending and it is
not the RES-01 frozen corpus. The remaining-state table below describes this
earlier candidate, not new requests for user action.
This is the latest [RES-01](https://github.com/natnael-solomon/ovrly/issues/8)
content revision, following the [review revision](../reviewed-draft/README.md).
It resolves the supplied card-content questions and remaps the social speech
occurrences. It does not certify rights clearance or successful frozen validation.
Original snapshots, media, caption files and private evidence are preserved.

## Completed content reconciliation

- All **106 spoken occurrences** across six social clips now use the user's
  reviewed SRT cues. Each decision records its SRT hash, cue range, previous
  interval and corrected interval. Normalized claims, eligibility, IDs and
  original references remain unchanged.
- All **20 previously recorded card occurrences** retain their separate
  visibility intervals. Their contents are not moved to speech timestamps.
- **Four additional EPI inset assertions** and **one qualitative opening-meme
  premise** are recorded as adjudicator discoveries with empty source-reference
  arrays and explicit provenance, not invented original annotations.
- The user confirmed the AP secondary box contains the author and publication
  time, not an additional content claim. No separate claim is created for it.
- The user confirmed clip-f has no on-screen text card. Previous confirmations
  for d/k, i's spoken-plus-caption modalities, m's negative result and all six
  social captions remain in force.

The original **259 occurrences** remain unchanged; the final reference has
**264 decisions**. Five discoveries explain the difference. The original
7 dev / 4 test split, media identities, four human main-argument notes and
evidence assessments are unchanged. This is not a claim that the video's
assertions are true.

### EPI graph

The higher-resolution image supplied on 2026-10-04 has SHA-256
`f859b4e56a6c9c4c646ed539806daaffbe2c9d41f7f7f280e03575338a0cf1c6`.
The following four claims use the existing **01:13-01:20** graph visibility
envelope:

| Labeled period | Average annual productivity growth | Average annual compensation growth |
| --- | --- | --- |
| 1948-1979 | 2.5% | 2.1% |
| 1979-2025 | 1.4% | 0.6% |

These are average annual percentages, not cumulative growth or percentage-point
differences. Preserve the graph's index base (1948=100) and existing footer
figures separately; do not assign a period to a footer figure lacking one.
The earlier unreadable-inset limitation is resolved by this image.

### Opening meme

The supplied image has SHA-256
`0bf84c2fb806b955f6b4ad68d74c305afad62a7e4702de4a22f4a5e575cf4dc0`.
It labels taxes, take-home pay and the amount a boss "steals". The large
underwater iceberg portion makes a qualitative comparison, with **no numerical
amounts or measured ratios**. This is a displayed argumentative premise, not a
legal determination of wage theft.

Local 30 fps video-frame inspection establishes the meme at the first frame,
still visible at 12.900 seconds and absent on the next frame at 12.933333
seconds. The stored half-open interval is **[0, 12933) ms**, with the end rounded
to integer milliseconds. This is separate from the spoken meme description.
No additional screenshot or numerical transcription is needed from the user.
Full-screen OCR of decorative background artwork is outside the reference scope.

## Freeze boundary

There are **no remaining requests for the supplied AP/EPI/meme content or for
clip-f's screen-card status**. Reconciliation of the six social speech tracks
is also complete. The current references use cue envelopes, not forced-aligned
word timestamps; sentence/context envelopes may intentionally overlap.

This candidate is not promoted to `kind: frozen` because the existing evidence
still records:

| Requirement | Actual remaining state |
| --- | --- |
| Social clip rights | No applicable license/permission evidence recorded for the six social clips |
| Commons credits | Local-use license approval exists for d/k/f/i, but embedded-credit completion is pending |
| Archival clip-m | Negative result confirmed; archival rights and English/language-neutral applicability not established by that confirmation |
| Frozen acceptance | Fixed eleven-clip, limited-scenario scope must be reflected in an explicit acceptance revision; the full-coverage frozen contract remains unchanged |
| Review provenance | Original provisional annotations are preserved; confirmations and corrections are in final decisions, not fabricated historical review flags |

The request to freeze is not evidence of third-party permission. No hosted
processing, redistribution or publication is authorized by this snapshot.
Do not delete limitations, invent language/rights fields, or relabel historical
timing as media-reviewed to obtain a successful validation result. The remaining
rights records are specific evidence needs, not requests to repeat completed
caption/card checks.

## Verification

```text
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
python evaluation/validate.py evaluation/freeze-candidate --draft
python evaluation/validate.py evaluation/freeze-candidate --draft --media-root <controlled-intake-root>
```

Metadata-only CI validates this candidate without fetching images, subtitles
or media. Tests preserve previous snapshots and verify the five discoveries,
106 speech mappings, four EPI values, negative result and 7/4 split. A
candidate relabeled frozen must still fail on pending rights.
