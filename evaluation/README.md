# Evaluation contract and review workflow

[RES-05](https://github.com/natnael-solomon/ovrly/issues/48) provides infrastructure for [RES-01](https://github.com/natnael-solomon/ovrly/issues/8). The `examples/` records are invented text, not clips, consent, human annotations or measured results. The reviewed corpus remains outstanding.

The real set requires 10-20 English clips. Live-style clips are limited to 180,000 ms; shared-style clips to 600,000 ms. Dev supports iteration; test is a frozen holdout. Prompt-tuning examples must be separate from both, including renamed copies. There is no training set here.

## Files and commands

Python 3.11+ is sufficient. There are no dependencies, provider calls or uploads. Run from the repository root; use `python3` if needed:

```text
python evaluation/validate.py
python -m unittest discover -s evaluation/tests -p "test_*.py" -v
python evaluation/validate.py evaluation/corpus --frozen
python evaluation/validate.py evaluation/corpus --frozen --media-root evaluation/media
```

The last two commands require the future `evaluation/corpus/`. Without `--media-root`, validation checks metadata/snapshot integrity and reports that media bytes were not verified. With it, every media file must exist within the root, including symlink containment, and match its SHA-256. Validation changes no files; errors go to stderr with a nonzero exit.

| File | Content |
| --- | --- |
| `dataset.json` | Schema/dataset version, `examples` or `frozen` kind, byte hashes of the three JSONL files |
| `clips.jsonl` | Clip provenance, rights, language, duration/style, groups, split, media identity and coverage |
| `annotations.jsonl` | Two independent whole-clip passes per clip, each with its own occurrence IDs |
| `adjudications.jsonl` | Final review, references to both passes, original-to-gold mappings and resolution notes |

Version-1 JSON Schemas are in `schemas/`. Record-format and validator-maintenance details follow the review process below.

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

## 3. Two independent annotation passes

Two different annotators review the same approved clip before seeing each other's annotations or model output. Use pseudonymous IDs. Set `independent: true` and `blind_to_model_output: true` only when that process occurred.

Each pass records all candidate factual occurrences and relevant exclusions. An empty `occurrences` array means the whole clip was reviewed and no candidates were found; a missing pass is not equivalent. Retain repeated occurrences.

Use `[start_ms, end_ms)` relative to the exact media file, with `0 <= start < end <= duration`. Record speech and text separately when intervals differ. Use `both` only for the same occurrence jointly supported by both modalities. Annotate title cards at their actual visibility interval, not sampled-frame times.

Preserve negation, quantities, units, dates, attribution and uncertainty in `proposition`. Normalization must not strengthen the claim.

| Eligibility | Reason |
| --- | --- |
| Eligible | `factual-claim`, `factual-premise` |
| Ineligible | `opinion`, `quoted-not-endorsed`, `insufficient-context`, `not-a-claim` |

Eligibility means assessable, not true. Pure normative judgments are ineligible, but their factual premises can qualify. Quoting misinformation does not automatically endorse it. Retain both occurrences of a later correction and explain their relationship during adjudication.

Missing context makes a candidate ineligible only when it prevents identifying an assessable proposition, not merely because evidence is unavailable. Truth/evidence scoring belongs to RES-03.

Product references are shared privately. Before real annotation, the research owner must reconcile this operational protocol with AC03-AC06 and RFC section 17. RES-02 may add verbatim ASR transcripts and OCR boxes; normalized claims are not WER or full-screen OCR ground truth.

## 4. Adjudicate without erasing disagreements

Keep both original passes unchanged. A third person or documented consensus led by an original reviewer may adjudicate; a third reviewer is not mandatory.

Reference both passes, including empty ones. Each gold decision retains source references, final interval/modality/proposition, eligibility/reason and nonempty `resolution`. Retain excluded candidates as ineligible decisions. Reference every original occurrence so boundaries, missing claims, text and eligibility disagreements remain recoverable.

A missed occurrence may reference one pass. An adjudicator-discovered occurrence may reference neither, with an explicit explanation. Merges may reference several occurrences; splits may reuse a reference across gold decisions. Explain each case.

Repeated occurrences of a proposition within a clip share `proposition_id` and identical normalized text. An empty `decisions` array with `review_note` confirms reviewed no-claim gold. The `no-assessable-claims` tag must agree with final eligible decisions.

## 5. Freeze and hand off the real corpus

1. Supply 10-20 rights-cleared English clips, both route types as suitable, all coverage tags and nonempty isolated dev/test splits. Obtain human sign-off on provenance, coverage and near-duplicate isolation.
2. Complete two independent passes and adjudication for every clip. Preserve disagreements; do not reuse synthetic examples as human annotations.
3. Store approved metadata in `evaluation/corpus/`. Set `synthetic: false` everywhere and `kind: frozen` in `dataset.json`. Assign an immutable version; hash media, then finalized JSONL files. To inspect a file hash: `python -c "from pathlib import Path; from evaluation.validate import digest; print(digest(Path('evaluation/corpus/clips.jsonl')))"`.
4. Run strict metadata validation and then validation with `--media-root`. Record commands, outcomes, dataset version, rights/review sign-off and repository commit. Successful checks are not freeze approval or measured accuracy/latency.
5. Freeze the files together in a reviewed PR. Corrections require a new version, change history and re-review. Do not refresh hashes to conceal edits; hashes detect inconsistency, not deliberate rewrites.

CI tests the validator and examples, plus frozen metadata when `evaluation/corpus/` exists. It fetches no media and runs no models. Keep holdout labels out of prompt tuning and routine regression. RES-03 will score dev in CI; formal held-out evaluation requires separate authorization.

There is no scoring harness, threshold, ASR/OCR benchmark or network service here. RES-01 closes only when the reviewed set is delivered; the infrastructure work belongs to RES-05.

## Record format and validator maintenance

Use UTF-8, LF endings, one JSON object per line, no blank rows, duplicate keys or NaN/infinite values. Snapshot hashes cover exact bytes, including the final newline; `.gitattributes` pins fixture endings on Windows. Physical LF separates JSONL records; Unicode separators inside strings remain field content.

IDs are lowercase opaque slugs, never names, emails or device identifiers. Whitespace is rejected, not trimmed. Clip, pass and adjudication IDs are unique within their files; occurrence IDs are scoped to a pass and gold/proposition IDs to a clip.

ID patterns use `(?![\s\S])` for strict end-of-input, because `$` also matches before a final newline. SHA-256 fields require exactly 64 lowercase hexadecimal characters with the same strict boundary. This prevents whitespace-suffixed hashes from bypassing identical-media isolation.

The dependency-free validator supports only schema features used here: `$schema`, `title`, `description`, `type`, `const`, `enum`, object properties/required/boolean additionalProperties, array items/size/uniqueness, string length/pattern and numeric bounds. Unsupported keywords fail. Patterns use Python regular expressions; retain the portable subset in the schemas. New features require implementation and negative tests.
