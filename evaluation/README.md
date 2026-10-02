# Evaluation contract and review workflow

**Status: contract plus a provisional eleven-clip draft.**
[RES-06](https://github.com/natnael-solomon/ovrly/issues/65) adds
[`draft/`](draft/README.md) to the infrastructure delivered by
[RES-05](https://github.com/natnael-solomon/ovrly/issues/48). It does not complete
[RES-01](https://github.com/natnael-solomon/ovrly/issues/8).
`examples/` contains invented text, not clips, consent, independent human
judgments, or measured results. Neither examples nor draft validation yields
accuracy or latency measurements.

The real set will contain **10-20 English clips**, with live-style clips at most
180,000 ms and shared-style clips at most 600,000 ms. There is no training set
here. Dev supports iterative evaluation, test is a frozen holdout, and examples
used to tune prompts must be separate from both. Assigning a new filename to a
tuning example does not make it a holdout.

## Files and commands

Python 3.11+ is sufficient; there are no dependencies, provider calls or uploads.
Run from the repository root (use `python3` if that is your Python executable):

```text
python evaluation/validate.py
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
python evaluation/validate.py evaluation/draft --draft
python evaluation/validate.py evaluation/draft --draft --media-root evaluation/media
python evaluation/validate.py evaluation/corpus --frozen
python evaluation/validate.py evaluation/corpus --frozen --media-root evaluation/media
```

The last two commands are for the **future** real corpus, which does not exist
yet. Without `--media-root`, only metadata and snapshot integrity are checked;
the CLI explicitly reports that media bytes were not verified. A successful
metadata check is not freeze approval. With `--media-root`, every declared file
must exist under that root and match its SHA-256, including symlink containment.
No files are modified by validation. Errors go to stderr and exit nonzero.

Each dataset directory contains:

| File | Contract |
| --- | --- |
| `dataset.json` | Schema version, dataset version, `examples`/`draft`/`frozen` kind, byte hashes of all included JSONL files; explicit limitations required for drafts |
| `clips.jsonl` | One clip per line: provenance, rights, language, duration/style, grouping, split, media identity, coverage |
| `annotations.jsonl` | Exactly one whole-clip occurrence annotation pass per clip, with occurrence IDs and explicit review provenance |
| `adjudications.jsonl` | One final review per clip, referencing its sole pass, with reviewer provenance, original-to-reference mapping and resolution notes |
| `main-arguments.jsonl` | Required for drafts, optional otherwise: exactly one separately attributed assessment per clip, original human note where present, evidence and source-reading limits |

The version-2 JSON Schemas are in `schemas/`. The dependency-free validator
implements only the keywords used there: metadata (`$schema`, `title`,
`description`), `type`, `const`, `enum`, object properties/required/boolean
additionalProperties, array items/size/uniqueness, string length/pattern, and
numeric bounds. It **rejects unsupported schema keywords**, rather than claiming
to be a general JSON Schema implementation. Patterns use Python's regular
expressions; keep them in the simple portable subset used by these schemas.
Adding a schema feature requires implementation and negative tests.

Use UTF-8, LF endings, one JSON object per line, no blank rows or duplicate
keys, and no NaN/infinite values. Hashes cover exact file bytes, including the
final newline; `.gitattributes` pins the fixture endings on Windows.
Stable IDs are lowercase opaque slugs, not names, emails or device identifiers.
Whitespace in IDs is rejected, never trimmed. Their patterns use
`(?![\s\S])` for a strict end-of-input assertion rather than `$`, which also
matches before a final newline. This keeps the schemas portable between Python
and JSON Schema regular expressions without changing general pattern semantics.
Media and snapshot SHA-256 values use the same strict boundary: exactly 64
lowercase hexadecimal characters, with no whitespace. This also prevents
malformed hashes from bypassing identical-media isolation in metadata-only checks.
JSONL records are separated by physical LF line endings; Unicode separators
inside JSON strings remain part of the field value, not extra records.
Occurrence and gold/proposition IDs are scoped to their pass or clip;
clip/pass/adjudication IDs are unique within their respective files.

## 1. Source and clear rights

Record the provider, original source URL, English language, duration, route
(`live` or `shared`), all creator/topic groups, and repost group. For a trimmed
excerpt, the local media is the exact benchmark interval: all annotation times
start at zero relative to that file. Retain the original URL and describe the
excerpt/time range in the rights reference or accompanying approved provenance
record. Do not change the excerpt after annotation without a new dataset version.

An accessible video is not permission to download, upload, or redistribute it.
Review the actual license/consent scope, including derivative excerpts, required
attribution and intended processing. `rights.basis` is `license`, `consent`, or
`public-domain`; `reference` is the license URL and version or an opaque
reference to a controlled consent record. `attribution` carries the required
notice. `allows_redistribution` records the permission, not a request for it.
Do not commit identifiable consent documents or private contact information.
Automated validation checks fields, not legal validity.

Keep acquired media in ignored `evaluation/media/` or a controlled location
outside the checkout. `media.path` is a portable slash-separated relative path
within the media root, not an absolute path or URL; `sha256` pins exact bytes.
Private download tokens, personal media and device IDs must never enter Git.
Even when redistribution is allowed, committing media needs a separate review;
this infrastructure task adds none. Hosted processing also requires appropriate
permission and retention terms; acquisition permission alone is not upload consent.

## 2. Plan coverage and isolate splits

Choose and review coverage before freezing; labels are corpus-wide, not a
requirement to force every scenario into each clip or each split:

| Tag | Required scenario |
| --- | --- |
| `speech-only` | A factual claim in speech without equivalent on-screen text |
| `text-only` | A claim available only in on-screen text |
| `brief-title-card` | Text short enough that fixed five-second sampling can miss it |
| `negation` | Negation changes the proposition |
| `later-self-correction` | Later speech/text corrects an earlier assertion |
| `quoted-misinformation` | Quoted misinformation with attribution/endorsement context |
| `mixed-evidence` | Evidence does not uniformly support one conclusion |
| `missing-context` | Missing context makes normalization or assessment unsafe |
| `withdrawn-retracted-source` | A withdrawn/retracted source is relevant |
| `no-assessable-claims` | Final gold contains no eligible factual occurrences |
| `normative-factual-premises` | Separate a normative conclusion from its factual premises |

Assign both dev and test, with **no shared creator, topic, or repost group**.
Include all relevant group IDs, not just the uploader or a convenient narrow
topic. Give originals and excerpts/reposts the same repost group. Connected
groups must stay on one side; choose more material if this leaves only one
split. Identical byte hashes also cannot cross splits. Review near-duplicates,
paraphrases and topic identity manually: hashes and labels cannot detect them.
The validator enforces declared group isolation; it cannot prove labels truthful.
No split ratio is prescribed for this small set.

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
Outside draft mode, an empty `occurrences` array means the recorded workflow
reviewed the whole clip and found none. A provisional empty draft does not
certify this; a missing pass never means no claims. Keep repeated occurrences rather
than deduplicating them before review. Use `[start_ms, end_ms)` relative to the
exact media file, with `0 <= start < end <= duration`. Record speech and screen
text independently when their intervals differ; use `both` only when the same
occurrence is jointly supported by both modalities. Mark title cards at their
actual visibility interval rather than rounding to sampled frames.

`proposition` preserves negation, quantities, units, dates, attribution and
uncertainty. Do not strengthen a statement while normalizing it. Eligibility
means it is an assessable factual assertion/premise, **not that it is true**.
`factual-claim` and `factual-premise` imply eligible; `opinion`,
`quoted-not-endorsed`, `insufficient-context` and `not-a-claim` imply ineligible.
Pure normative judgments are not factual claims; their factual premises can be.
Quoted misinformation is not automatically endorsed. For later corrections,
preserve both occurrences and explain context in the adjudication resolution;
never silently replace the earlier assertion. Missing context is ineligible
only when it prevents identifying an assessable proposition, not merely because
evidence is unavailable. Truth/evidence scoring is a later RES-03 concern.

This is an initial operational protocol, not a claimed transcription of the
unavailable full contract/RFC. Before real annotation, the research owner must
align eligibility decisions with AC03-AC06 and RFC section 17. RES-02 may extend
this contract with verbatim ASR transcripts and OCR boxes; current normalized
claim text is **not** a WER or full-screen OCR reference.

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
Outside a provisional draft, an empty `decisions` array plus `review_note`
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
`modality: unverified` are draft-only values; a complete review cannot certify
unknown modality or unknown clip language. A provisional pass or adjudication
cannot use the `no-assessable-claims` coverage tag, even with zero decisions.
The `user-reported-negative` main-argument basis is likewise unconfirmed.

Every draft source occurrence requires a `source` object (opaque reference,
SHA-256 and source units), and both originals and decisions require
`timing_basis`: `caption-envelope`, `user-segment`, `subtitle-cue`,
`user-card-interval`, or `media-reviewed`. Source hashes pin inputs but do not
prove accurate transcription or visibility; the validator does not possess
or verify those source files. Corrected input hashes belong in resolution
notes while the original pass retains the input hash used to draft it.

Frozen validation still rejects pending rights/credits, provisional review,
unverified language/modality, provisional timing bases and an unconfirmed
negative assessment. It still requires all coverage scenarios and 10-20 clips.
Do not promote a draft by deleting limitations or changing flags: complete the
underlying review, retain the historical draft, then create a new snapshot
with documented corrections and approvals. Neither schema validation nor
the word "complete" can attest that a person actually performed that work.

## 5. Freeze and hand off the real corpus

1. Supply 10-20 real English rights-cleared clips, both route types as suitable,
   all coverage tags, and nonempty leakage-safe dev/test splits. Obtain human
   sign-off on provenance, coverage and near-duplicate isolation.
2. Complete one provenance-labeled pass and final adjudication for every clip.
   Keep original decisions and correction history; do not reuse these examples
   as real annotations or claim review that did not occur.
3. Store the approved JSONL metadata under `evaluation/corpus/`, set
   `synthetic: false` everywhere and `kind: frozen` in `dataset.json`. Use an
   immutable dataset version. Hash each media file and then each finalized
   JSONL file. For example, inspect a file hash without modifying it:
   `python -c "from pathlib import Path; from evaluation.validate import digest; print(digest(Path('evaluation/corpus/clips.jsonl')))"`.
4. Run strict metadata validation and then validation with `--media-root`.
   Record exact commands, results, dataset version, rights/review sign-off and
   the repository commit used. No metrics are justified merely by these checks.
5. Freeze files together in a reviewed PR. A later correction requires a new
   version and explicit change history/re-review; do not silently refresh hashes
   to hide edits. JSON hashes detect inconsistency, not deliberate rewrites.

CI always runs validator tests and checks the examples and draft metadata. If `evaluation/corpus/`
exists, it also validates frozen **metadata**, without fetching media or running
models. Structural validation is not evaluation on the holdout. Keep test
labels out of prompt-tuning and routine performance regression; RES-03 scores
dev in CI, and formal held-out evaluation is a separately authorized run.
There is no scoring harness, threshold, ASR/OCR benchmark or network service here.
RES-01 remains open until the actual reviewed set is delivered. RES-06 delivers
the reduced-scope draft, not the parent freeze or pipeline.
