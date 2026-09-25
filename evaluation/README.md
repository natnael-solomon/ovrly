# Evaluation contract and review workflow

**Status: infrastructure only.** [RES-05](https://github.com/natnael-solomon/ovrly/issues/48)
prepares [RES-01](https://github.com/natnael-solomon/ovrly/issues/8); it does not
complete it. `examples/` contains invented text, not clips, consent, independent
human judgments, or measured results. Do not report accuracy or latency from it.

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
| `dataset.json` | Schema version, dataset version, `examples`/`frozen` kind, byte hashes of all three JSONL files |
| `clips.jsonl` | One clip per line: provenance, rights, English language, duration/style, grouping, split, media identity, coverage |
| `annotations.jsonl` | Exactly two independently completed whole-clip passes, each with its own occurrence IDs |
| `adjudications.jsonl` | One final review per clip, references to both passes, original-to-gold mapping and resolution notes |

The version-1 JSON Schemas are in `schemas/`. The dependency-free validator
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

## 3. Two independent annotation passes

Before either person sees the other's annotations **or any model output**,
give each the same approved clip and these instructions. Use two different
pseudonymous annotator IDs. Record `independent: true` and
`blind_to_model_output: true` only when that process actually occurred.
These attestations are workflow evidence, not proof supplied by software.

Each pass lists every candidate factual occurrence and relevant exclusions.
An empty `occurrences` array means the person reviewed the whole clip and found
none; a missing pass does not mean no claims. Keep repeated occurrences rather
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

## 4. Adjudicate without erasing disagreements

Preserve both original passes unchanged. The adjudicator may be a third reviewer
or a documented consensus led by one original reviewer; the issue requires two
independent initial passes, not a mandatory third person.

One adjudication references both passes, including empty ones. Each gold
decision retains source references, a final interval/modality/proposition,
eligibility/reason and a nonempty `resolution`. Retain excluded candidates as
ineligible decisions rather than deleting them. Every original occurrence must
be referenced. Different boundaries, missing occurrences, eligibility and text
disagreements remain recoverable from the original rows and resolution.

A gold decision may have one reference for a missed occurrence or none for an
adjudicator-discovered occurrence; explicitly explain discoveries. Merges may
reference multiple occurrences; splits may reuse an original reference across
gold decisions, with an explanation. Use a common `proposition_id` and identical
normalized text for repeated occurrences of the same proposition in a clip.
An empty `decisions` array plus `review_note` confirms reviewed no-claim gold.
The `no-assessable-claims` tag must agree with the final eligible decisions.

## 5. Freeze and hand off the real corpus

1. Supply 10-20 real English rights-cleared clips, both route types as suitable,
   all coverage tags, and nonempty leakage-safe dev/test splits. Obtain human
   sign-off on provenance, coverage and near-duplicate isolation.
2. Complete both independent passes and adjudication for every clip. Keep
   disagreements; do not reuse these examples as supposed human annotations.
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

CI always runs validator tests and checks the examples. If `evaluation/corpus/`
exists, it also validates frozen **metadata**, without fetching media or running
models. Structural validation is not evaluation on the holdout. Keep test
labels out of prompt-tuning and routine performance regression; RES-03 scores
dev in CI, and formal held-out evaluation is a separately authorized run.
There is no scoring harness, threshold, ASR/OCR benchmark or network service here.
RES-01 remains open until the actual reviewed set is delivered; only RES-05 can
be closed by this infrastructure.
