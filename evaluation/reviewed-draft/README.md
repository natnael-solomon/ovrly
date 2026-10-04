# RES-01 review revision

**Version: `res01-review-2026-10-04`. Status: draft, not frozen.**
This revision is preserved. The later
[content-complete candidate](../freeze-candidate/README.md) resolves the AP,
EPI and meme questions below, records clip-f's confirmation and reconciles
social speech intervals. Do not repeat the historical requests listed here.
This revision records additional user review for [RES-01](https://github.com/natnael-solomon/ovrly/issues/8).
It preserves the historical [RES-06 draft](../draft/README.md), including its
original source annotations. It does not upload or redistribute media, screenshots,
full transcripts or private rights records.

## Recorded confirmations

The user's 2026-10-04 16:55 +03:00 message confirms:

| Clip | Recorded result | Boundary |
| --- | --- | --- |
| d, k | No on-screen claims; final modalities remain speech | Not a new subtitle-alignment or rights attestation |
| i | Claims are spoken and have on-screen captions; final modalities change from `unverified` to `both` | Existing cue envelopes retained, including the 123 ms final cap to 70,021 ms; exact speech/caption boundary coincidence is not measured |
| m | **User-confirmed negative: no assessable claims**; final decisions remain empty | Language applicability and archival rights are not inferred |

Clip-m's `main-arguments.jsonl` record now has
`basis: user-confirmed-negative`. The validator rejects that basis when eligible
final decisions exist. This is a confirmed claim-absence result, no longer a
negative candidate awaiting the same confirmation. Historical annotation
provenance remains unchanged. The original provisional pass, unresolved language
and incomplete freeze requirements are not relabeled complete; consequently the
whole-review `no-assessable-claims` coverage tag is not yet asserted. The new
basis distinguishes a confirmed negative from both an empty unreviewed pass and
full corpus approval.

The user's earlier 16:26 +03:00 confirmation covers caption wording/timing for
the six social clips. The caste ending is outro, revolution says "buy-in", and
the Harvard "Mm-hmm" restored from older text was removed from the separately
stored reviewed SRTs. These are caption confirmations, not factual truth verdicts.
Speech claim intervals have not yet been remapped to those reviewed SRTs.

## Completed supplied-card reconciliation

All six supplied screenshot hashes match the intake record. All eight card
subjects map to exactly **20 original occurrences and 20 final decisions**,
with no lost assertions or duplicated Guardian occurrence. Source references,
propositions, eligibility and supplied visibility intervals remain unchanged;
each final resolution records its card ID, screenshot hash and reconciliation.

| Clip | Supplied interval | Card subjects | Occurrences |
| --- | --- | --- | --- |
| social-01 | 00:18-00:20 | CEPR/VoxEU race and redistribution column | 4 |
| social-05 | 00:20-00:23 | AP tax-bill headline | 1 |
| social-05 | 00:23-00:30 | Newsweek income-tax headline | 1 |
| social-05 | 01:13-01:20 | EPI CEO-pay headline and productivity/pay graph | 6 |
| social-05 | 02:35-02:38 | Guardian and CNBC pay headlines | 3 |
| social-05 | 02:43-02:45 | Cooperatives 101 diagram | 5 |

These intervals were supplied by the user and are retained as
`user-card-interval`. A screenshot does not measure its entire visibility
duration. We do not replace a card interval with the time its subject is spoken,
infer frame precision, or require the user to resupply already recorded intervals.
Keep screen and speech occurrences separate where their intervals differ.
The graph's indexed axis and footer growth figures are not interchangeable;
the CEO-pay headlines describe different populations/measures. The cooperative
diagram describes the pictured model, not every cooperative.

## Exact remaining human input

The main cards above are accounted for. Three small or unsupplied visual areas
remain outside the reference annotations, not silently assumed claim-free:

| Clip / location | Needed input |
| --- | --- |
| social-05 opening meme; inspect the opening 00:00-00:05.119 spoken introduction | A readable crop showing any numerical labels or other assertions beyond the transcribed tax/take-home/boss comparison, plus their visible interval; or confirmation that no additional assessable assertion is present. This inspection window is not a measured meme visibility interval. |
| social-05 00:20-00:23, tiny secondary box on the AP screenshot | A readable crop and exact text if it contains an additional assertion. If unreadable in the actual video or non-claim material, record that instead. |
| social-05 01:13-01:20, small annual-growth inset on the EPI graph | A readable crop with values, units and period labels. Do not derive these from the article or neighboring footer figures. If unreadable in the video, record that limitation. |

No further input is needed for the supplied main-card text or recorded intervals
unless the user identifies an error. Do not count the unreadable regions as
successful full-screen OCR coverage. Once their status is resolved, preserve it
in the next review revision rather than inventing content.

Separate from text cards, clip-f still lacks confirmation about additional
on-screen-only claims. Its source SRT contains twelve adjacent overlaps of
**1 ms each** (near 23.169, 43.929, 52.909, 135.069, 159.569, 179.439, 185.519,
211.959, 230.579, 245.319, 280.699 and 287.860 seconds). These encoding-scale
overlaps do not warrant a request to manually retime the clip.

Remaining implementation work includes remapping spoken-claim intervals to the
reviewed captions. Remaining owner decisions/evidence include rights and credits,
clip-m language applicability, and formalizing the fixed eleven-clip limited
scenario scope. No more scenario acquisition is planned; unsupported slices
must be reported as not evaluated. The existing full-coverage frozen validator
is not weakened by this revision.

## Integrity and checks

`clips.jsonl` and `annotations.jsonl` are byte-identical to the historical draft.
All eleven media identities, 259 original occurrences, four original human
main-argument notes and the **7 dev / 4 test** split are preserved. Changes are
restricted to final review notes/resolutions, clip-i final modalities, clip-m's
negative assessment and the new manifest.

From the repository root:

```text
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
python evaluation/validate.py evaluation/reviewed-draft --draft
python evaluation/validate.py evaluation/reviewed-draft --draft --media-root <controlled-intake-root>
```

CI validates this revision without retrieving media or calling providers.
Pending rights, provisional original reviews and remaining coverage requirements
still prevent frozen validation. No accuracy, recall or latency result is
established by this dataset revision.
