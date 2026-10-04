# Evaluation contract and review workflow

**Status: contract, a provisional eleven-clip draft and a rights-pending
local snapshot. The RES-01 frozen corpus is still pending.**
[`corpus-local/`](corpus-local/README.md), version
`res01-local-frozen-2026-10-04`, is a locally validated reduced-scope snapshot
(`kind: frozen-local`, validated with `--frozen-local`). It preserves the
7 dev / 4 test split, 259 original occurrences and 264 final decisions. Every
clip row still records `rights.clearance: pending`, every coverage array is
unconfirmed and every pass and adjudication remains `provisional`. The owner
accepted the clip selection for local use on 2026-10-04; rights clearance,
credits and permission evidence remain pending per the clip rows. It is not
the RES-01 frozen corpus, and `--frozen` rejects it by design.

The following snapshots are historical, not outstanding approval requests:
[`freeze-candidate/`](freeze-candidate/README.md) is the preceding content
revision: 106 social speech intervals reconciled, four EPI inset assertions and
a qualitative meme premise added, and the AP/clip-f questions resolved. It
retains its draft status; the subsequent owner acceptance is in `corpus-local/`.
[`reviewed-draft/`](reviewed-draft/README.md) records the 2026-10-04 user
confirmations for d/k/i/m and supplied-card reconciliation. It preserves the
historical draft and does not constitute a frozen corpus.
[RES-06](https://github.com/natnael-solomon/ovrly/issues/65) adds
[`draft/`](draft/README.md) to the infrastructure delivered by
[RES-05](https://github.com/natnael-solomon/ovrly/issues/48). Neither it nor
the local snapshot completes
[RES-01](https://github.com/natnael-solomon/ovrly/issues/8).
`examples/` contains invented text, not clips, consent, independent human
judgments, or measured results. Neither examples nor draft validation yields
accuracy or latency measurements.

The real set requires 10-20 English clips. Live-style clips are limited to 180,000 ms; shared-style clips to 600,000 ms. Dev supports iteration; test is a frozen holdout. Prompt-tuning examples must be separate from both, including renamed copies. There is no training set here.

## Files and commands

Python 3.11+ is sufficient. There are no dependencies, provider calls or uploads. Run from the repository root; use `python3` if needed:

```text
python evaluation/validate.py
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
python evaluation/validate.py evaluation/draft --draft
python evaluation/validate.py evaluation/draft --draft --media-root evaluation/media
python evaluation/validate.py evaluation/reviewed-draft --draft
python evaluation/validate.py evaluation/reviewed-draft --draft --media-root evaluation/media
python evaluation/validate.py evaluation/freeze-candidate --draft
python evaluation/validate.py evaluation/freeze-candidate --draft --media-root evaluation/media
python evaluation/validate.py evaluation/corpus-local --frozen-local
python evaluation/validate.py evaluation/corpus-local --frozen-local --media-root evaluation/media
python evaluation/validate.py evaluation/corpus --frozen
python evaluation/validate.py evaluation/corpus --frozen --media-root evaluation/media
```

The last two commands require the future `evaluation/corpus/`. Without `--media-root`, validation checks metadata/snapshot integrity and reports that media bytes were not verified. With it, every media file must exist within the root, including symlink containment, and match its SHA-256. Validation changes no files; errors go to stderr with a nonzero exit.

| File | Content |
| --- | --- |
| `dataset.json` | Schema version, dataset version, `examples`/`draft`/`frozen`/`frozen-local` kind, byte hashes of included JSONL files; explicit limitations required for drafts and local snapshots |
| `clips.jsonl` | One clip per line: provenance, rights, language, duration/style, grouping, split, media identity, coverage |
| `annotations.jsonl` | Exactly one whole-clip occurrence annotation pass per clip, with occurrence IDs and explicit review provenance |
| `adjudications.jsonl` | One final review per clip, referencing its sole pass, with reviewer provenance, original-to-reference mapping and resolution notes |
| `main-arguments.jsonl` | Required for drafts and local snapshots, optional otherwise: exactly one separately attributed assessment per clip, original human note where present, evidence and source-reading limits |

Version-2 JSON Schemas are in `schemas/`. Record-format and validator-maintenance details follow the review process below.

## 1. Source and clear rights

Record provider, original URL, English language, duration, route (`live` or `shared`), all creator/topic groups and the repost group. A trimmed file is the exact benchmark interval: annotations start at zero relative to it. Retain the original URL and excerpt range in the rights reference or approved provenance record. Changing the excerpt requires a new dataset version.

Review permission to acquire, process and redistribute the material, including excerpts and required attribution. Accessible media is not automatically licensed for those uses. `rights.basis` is `license`, `consent` or `public-domain`; `reference` identifies the license/version or a controlled consent record; `attribution` retains the required notice. `allows_redistribution` records actual permission. Validation checks fields, not legal validity.

Keep media in ignored `evaluation/media/` or controlled storage outside the checkout. `media.path` is slash-separated and relative to that root, never an absolute path or URL. `sha256` pins its bytes.

Never commit identifiable consent documents, contact details, private download tokens, personal media or device IDs. Even permitted redistribution requires a separate review before committing media. Hosted processing needs appropriate consent and retention terms; acquisition permission alone is insufficient.

## 2. Plan coverage and isolate splits

Review coverage across the corpus before freezing. Each scenario need not appear in every clip or split.

| Tag | Required scenario |
| --- | --- |
| `speech-only` | Factual speech without equivalent screen text |
| `text-only` | A claim available only in screen text |
| `brief-title-card` | Text brief enough for five-second sampling to miss |
| `negation` | Negation changes the proposition |
| `later-self-correction` | Later speech/text corrects an earlier assertion |
| `quoted-misinformation` | Quoted misinformation with attribution/endorsement context |
| `mixed-evidence` | Evidence does not uniformly support one conclusion |
| `missing-context` | Context is insufficient for safe normalization or assessment |
| `withdrawn-retracted-source` | A withdrawn/retracted source is relevant |
| `no-assessable-claims` | Final gold has no eligible factual occurrences |
| `normative-factual-premises` | Distinguish normative conclusions from factual premises |

Both dev and test must be nonempty, with no shared creator, topic or repost group. Record all relevant groups, not just the uploader or a convenient narrow topic. Originals, excerpts and reposts share a repost group; connected groups stay on one side. Add material if necessary to retain two splits. No split ratio is prescribed.

Identical media hashes cannot cross splits. Review near-duplicates, paraphrases and topic identity manually. The validator enforces declared isolation but cannot prove the labels truthful or discover undeclared leakage.

## 3. One provenance-labeled annotation pass

Create exactly one whole-clip occurrence pass. A second annotator and a
model-blind human pass are not required. Record a pseudonymous `annotator_id`,
`annotator_kind` (`human` or `ai-assisted`), and nonempty `provenance` describing
the actual source material, drafting method, model/tool where known, and human
review performed or not performed. AI-generated drafts, including drafts later
edited or accepted by a person, remain `ai-assisted`; selection is not human
authorship or proof of whole-clip review.

Retain truthful boolean `independent` and `blind_to_model_output` attestations.
Here `independent` means independently human-produced, not merely a separate
file or AI run. Both must be `false` for an AI-assisted pass. A human-authored
pass may also use `false` when it was not independent or model-blind. Software
checks declared consistency, not the truth of the review history.

The pass lists every candidate factual occurrence and relevant exclusions.
In full-coverage frozen mode, an empty `occurrences` array means the recorded workflow
reviewed the whole clip and found none. A provisional empty draft does not
certify this; a missing pass never means no claims. Keep repeated occurrences
rather than deduplicating them before review.

Use `[start_ms, end_ms)` relative to the exact media file, with `0 <= start < end <= duration`. Record speech and text separately when intervals differ. Use `both` only for the same occurrence jointly supported by both modalities. Annotate title cards at their actual visibility interval, not sampled-frame times.

Preserve negation, quantities, units, dates, attribution and uncertainty in `proposition`. Normalization must not strengthen the claim.

| Eligibility | Reason |
| --- | --- |
| Eligible | `factual-claim`, `factual-premise` |
| Ineligible | `opinion`, `quoted-not-endorsed`, `insufficient-context`, `not-a-claim` |

Eligibility means assessable, not true. Pure normative judgments are ineligible, but their factual premises can qualify. Quoting misinformation does not automatically endorse it. Retain both occurrences of a later correction and explain their relationship during adjudication.

Missing context makes a candidate ineligible only when it prevents identifying an assessable proposition, not merely because evidence is unavailable. Truth/evidence scoring belongs to RES-03.

Product references are shared privately. Before real annotation, the research owner must reconcile this operational protocol with AC03-AC06 and RFC section 17. RES-02 may add verbatim ASR transcripts and OCR boxes; normalized claims are not WER or full-screen OCR ground truth.

Main-argument assessments may be retained as separately attributed
companion notes. They are not another occurrence pass and do not establish
that every occurrence was individually reviewed. Broad caption windows
are useful in working drafts but must be labeled approximate; do not infer
word-level timing or report unsupported timestamp accuracy from them.

## 4. Adjudicate the single pass without erasing corrections

Preserve the original pass unchanged. One final adjudication is still required
for each clip, including an empty pass. The same reviewer or workflow
may perform it; another annotator is not required. Record `adjudicator_id`,
`adjudicator_kind` (`human` or `ai-assisted`), and the actual review method and
limitations in `review_note`. Adjudication does not change the original pass's
authorship or independence.

One adjudication references the sole pass in `annotation_ids`. Each final
decision retains source references, a final interval/modality/proposition,
eligibility/reason and a nonempty `resolution`. Retain excluded candidates as
ineligible decisions rather than deleting them. Every original occurrence must
be referenced. Different boundaries, missing occurrences, eligibility and text
corrections remain recoverable from the original rows and resolution.

A final decision normally references its original occurrence, or has no
reference for an adjudicator-discovered occurrence; explicitly explain discoveries. Merges may
reference multiple occurrences; splits may reuse an original reference across
gold decisions, with an explanation. Use a common `proposition_id` and identical
normalized text for repeated occurrences of the same proposition in a clip.
Outside preserved provisional records, an empty `decisions` array plus `review_note`
confirms reviewed no-claim gold.
The `no-assessable-claims` tag must agree with the final eligible decisions.

The historical `gold_id` field names identify final reference decisions; they
do not certify human authorship or factual truth. Report reference-generation
limitations with any evaluation results. A single-pass protocol cannot measure
inter-annotator agreement.

### Version-1 migration and reduced-scope work

Version 2 intentionally replaces the two-pass protocol with one pass plus
adjudication, tracked in [the RES-01 follow-up](https://github.com/natnael-solomon/ovrly/issues/65).
The validator rejects version-1 or mixed-version snapshots rather than
silently changing their meaning. To migrate an existing snapshot, preserve it,
explicitly select the source pass, record actual provenance, regenerate final
decisions/references and review notes, and publish a new dataset version with
`schema_version: 2` on the manifest and all rows and new byte hashes. Do not
just drop a reviewer, overwrite historical attestations or relabel AI output.
There is no automatic migration or legacy-validation mode.

This protocol change does not waive frozen-corpus rights, coverage, media or
split safeguards. Missing-scenario acquisition and rights/credit completion
are outside the reduced-scope follow-up, but remain required for the full
RES-01 freeze. Keep incomplete exports in a separately labeled working draft;
neither `examples` nor `frozen` is a valid status for incomplete real data.

### Draft metadata, not freeze approval

Use `kind: draft` and explicitly request `--draft`. A draft requires real source
URLs, local media identities/hashes, one selected pass and one adjudication per
clip, all original references, valid intervals and leakage-safe dev/test groups.
It also requires nonempty manifest `limitations` and `main-arguments.jsonl`,
whose byte hash, schema and exact clip coverage are checked.

Declare `rights.clearance` as `pending` or `cleared`; `rights.basis: pending`
is available when permission evidence is absent. Pending basis cannot claim
clearance or redistribution. A known license with unfinished credit obligations
can retain its license basis and pending clearance. These are not permissions.
Draft coverage can be incomplete. An empty coverage array means unconfirmed,
not that the clip has none of the listed scenarios.

Both pass and adjudication require `review_status: provisional|complete`.
Keep provisional status whenever whole-media review is incomplete, even after
reconciling every supplied row. `language: unverified` and occurrence
`modality: unverified` are provisional-record values; a complete review cannot certify
unknown modality or unknown clip language. A provisional pass or adjudication
cannot use the `no-assessable-claims` coverage tag, even with zero decisions.
The `user-reported-negative` main-argument basis is likewise unconfirmed.

`user-confirmed-negative` separately records an explicit user confirmation of
no assessable claims, with its provenance and remaining limitations. It requires
no eligible final decisions. It does not make an original provisional pass
complete, certify language or rights, or automatically add the whole-review
`no-assessable-claims` coverage tag. A later revision can therefore record a
confirmed negative without rewriting the original pass or pretending the entire
clip has freeze approval.

Every draft source occurrence requires a `source` object (opaque reference,
SHA-256 and source units), and both originals and decisions require
`timing_basis`: `caption-envelope`, `user-segment`, `subtitle-cue`,
`user-card-interval`, or `media-reviewed`. Source hashes pin inputs but do not
prove accurate transcription or visibility; the validator does not possess
or verify those source files. Corrected input hashes belong in resolution
notes while the original pass retains the input hash used to draft it.

Full-coverage `--frozen` validation still rejects pending rights/credits, provisional review,
unverified language/modality, provisional timing bases and an unconfirmed
negative assessment. It still requires all coverage scenarios and 10-20 clips.
Do not promote a draft by deleting limitations or changing flags: complete the
underlying review, retain the historical draft, then create a new snapshot
with documented corrections and approvals. Neither schema validation nor
the word "complete" can attest that a person actually performed that work.
`frozen-local` below is a separately named snapshot kind that applies the
draft rules to preserved rows; it does not promote a draft and does not
satisfy section 5.

## Owner-accepted local snapshot

Use `kind: frozen-local` with the explicit `--frozen-local` command for the
reduced-scope local evaluation snapshot whose clip selection the project owner
accepted for local use. This is a distinct mode, never automatic fallback from
strict validation, and it is not the RES-01 freeze. It requires 10-20 real
clips, the exact accepted clip list, a timezone-bearing acceptance date, the
private rights-checklist hash, source-manifest hash, the schema constant
`credits_signoff: approved` and explicit limitations. That constant is a
schema requirement, not evidence that credits were completed; the clip rows
record the actual rights and credit state. The recorded scope is fixed to local
evaluation without training, hosted processing or redistribution.

This mode retains original review statuses and timing/language limitations
instead of rewriting history. It applies the draft provenance checks to the
preserved rows, so pending rights, provisional reviews, unverified language
and empty coverage arrays pass, and additionally requires known final
modalities and explicit user-confirmed negative assessments for clips with no
eligible decisions. Integrity, media containment/hashes, all-occurrence
traceability and split isolation remain mandatory. Coverage is accepted as
recorded; missing or unconfirmed slices cannot be claimed as evaluated.

The acceptance is the project owner's agreement to use this fixed set
locally, not a rights clearance, a third-party license or a claim of
independent review. Rights clearance, credits and permission evidence remain
pending per the clip rows until they are completed and recorded there. The
schema cannot verify the truth of the underlying private evidence.
`freeze_approval` is required only in this mode and rejected in all others.
Keep the snapshot immutable and preserve its private acceptance receipt;
changes need a new version. A valid `--frozen-local` result is not benchmark
certification and does not close RES-01.

## 5. Full-coverage freeze and handoff

1. Supply 10-20 rights-cleared English clips, both route types as suitable, all coverage tags and nonempty isolated dev/test splits. Obtain human sign-off on provenance, coverage and near-duplicate isolation.
2. Complete one provenance-labeled pass and final adjudication for every clip. Preserve original decisions and correction history; do not reuse synthetic examples as real annotations or claim review that did not occur.
3. Store approved metadata in `evaluation/corpus/`. Set `synthetic: false` everywhere and `kind: frozen` in `dataset.json`. Assign an immutable version; hash media, then finalized JSONL files. To inspect a file hash: `python -c "from pathlib import Path; from evaluation.validate import digest; print(digest(Path('evaluation/corpus/clips.jsonl')))"`.
4. Run strict metadata validation and then validation with `--media-root`. Record commands, outcomes, dataset version, rights/review sign-off and repository commit. Successful checks are not freeze approval or measured accuracy/latency.
5. Freeze the files together in a reviewed PR. Corrections require a new version, change history and re-review. Do not refresh hashes to conceal edits; hashes detect inconsistency, not deliberate rewrites.

CI tests the validator, examples, historical draft, review revision, candidate and local snapshot metadata,
plus frozen metadata when `evaluation/corpus/` exists. It fetches no media and
runs no models. Keep holdout labels out of prompt tuning and routine regression.
RES-03 will score dev in CI; formal held-out evaluation requires separate authorization.

There is no scoring harness, threshold, ASR/OCR benchmark or network service here.
RES-01 closes only when the reviewed set is delivered through this section's
steps. The `frozen-local` snapshot is a reduced-scope local reference with
pending rights; it is not that delivery. RES-06 remains the historical draft
delivery.

## Record format and validator maintenance

Use UTF-8, LF endings, one JSON object per line, no blank rows, duplicate keys or NaN/infinite values. Snapshot hashes cover exact bytes, including the final newline; `.gitattributes` pins fixture endings on Windows. Physical LF separates JSONL records; Unicode separators inside strings remain field content.

IDs are lowercase opaque slugs, never names, emails or device identifiers. Whitespace is rejected, not trimmed. Clip, pass and adjudication IDs are unique within their files; occurrence IDs are scoped to a pass and gold/proposition IDs to a clip.

ID patterns use `(?![\s\S])` for strict end-of-input, because `$` also matches before a final newline. SHA-256 fields require exactly 64 lowercase hexadecimal characters with the same strict boundary. This prevents whitespace-suffixed hashes from bypassing identical-media isolation.

The dependency-free validator supports only schema features used here: `$schema`, `title`, `description`, `type`, `const`, `enum`, object properties/required/boolean additionalProperties, array items/size/uniqueness, string length/pattern and numeric bounds. Unsupported keywords fail. Patterns use Python regular expressions; retain the portable subset in the schemas. New features require implementation and negative tests.
