# Decision records

Lightweight decision log for Ovrly. Each record answers one question with the
RFC section 22 template below. Records distinguish confirmed choices from open
ones; a record exists so the team can see what was decided, by whom, on what
evidence, and what would reverse it.

| Record | Status | Purpose |
| --- | --- | --- |
| [0001: confirmed product scope](0001-confirmed-product-scope.md) | Accepted | The two journeys and their boundaries, from the owner's 19 September 2026 brief. |
| [0002: task tracking](0002-task-tracking.md) | Accepted | GitHub Project replaces the Linear proposal; AC, BC-D and RFC-D namespaces. |
| [0003: CP2 scope review](0003-scope-review-cp2.md) | Accepted | AC10 ships complete with on-device export; AC08 ships guest checking and saved reports, and second-device recovery is disclosed unless verified on two devices before the freeze. |
| [0004: ASR and OCR benchmarks](0004-asr-ocr-benchmarks.md) | Open | Non-phone hosted ASR, Tesseract, cue/quota and sampling evidence completed locally; no production server OCR; phone comparisons and final integration selection remain pending. |
| [0005: on-device screen-text recognizer](0005-on-device-screen-text.md) | Accepted | Bundled ML Kit Latin on the phone with its datatransport upload components removed; frames stay on the device and only text observations go to the backend; device runs showed no Google logging traffic from ovrly. |
| [BC-D02: supported providers and devices](BC-D02-providers-devices.md) | Accepted | Advertise YouTube Shorts, TikTok and Instagram Reels on Android 12 and later; evidence from one Android 12 device, Facebook not advertised. |
| [BC-D03: provider, hosting and BE-08 go](BC-D03-provider-hosting.md) | Conditional | Scholarxiv `auto:cheap` with Groq GPT-OSS 20B as the only fallback, EthioDeploy Free hosting and a limited BE-08 development go; proposed by Neb-iyu from BE-01, confirmed by the product owner on 4 October 2026, Accepted once the metrics summary is committed. |
| [BC-D04: Voxide route and allowlist](BC-D04-voxide-route.md) | Accepted, allowlist conditional | Native route confirmed with the sponsor; session budget; action allowlist pending #35. |
| [BC-D06: demo retention](BC-D06-retention.md) | Proposed | Opt-in workspace lifetime and deletion receipts; no approval or quota decision inferred. |
| [BC-D07: account link flow](BC-D07-account-link.md) | Accepted | Google sign-in only; guest upgraded in place; second device merges saved reports and continues as the account. |
| [BC-D09: hackathon dates](BC-D09-deadline-evidence.md) | Conditional | 25 September submission, 9 October final deadline, 11 October winners announcement. Timezone assumed. |

## Milestones

| Milestone | Date | Evidence |
| --- | --- | --- |
| Project submission | 25 September 2026 | Owner-reported; see [BC-D09](BC-D09-deadline-evidence.md). |
| Final hackathon deadline | 9 October 2026 | Owner-confirmed 28 September 2026. |
| Winners announcement, ALX Ledeta | 11 October 2026 | Owner-confirmed 28 September 2026. Not build time. |

Times are assumed to be East Africa Time (UTC+3) until the organizers state
otherwise. Do not publish an exact countdown until the cutoff time is confirmed.

## Reference namespaces

- `AC01` to `AC10`: acceptance criteria in the build contract (21 September 2026 working contract, reconciled edition 2026-09-28-v2, held privately by the team).
- `BC-D01` to `BC-D09`: the build contract's decisions D01 to D09.
- `RFC-D01` to `RFC-D84`: the research RFC's unprefixed D01 to D84 (section 22 holds D01 to D66 and the template; section 24 adds D67 to D84).
- `AN-`, `BE-`, `RES-`, `REPO-`: GitHub issue codes on the [ovrly development board](https://github.com/users/natnael-solomon/projects/3). Task references, not decision IDs.

The build contract, the owner's main-ideas brief and the research RFC are team
reference documents and are not committed to this repository. Records cite them
by title and section.

## Writing a record

Copy the template. Name files `NNNN-short-title.md` for team decisions and
`BC-Dnn-short-title.md` when the record resolves a build-contract decision.
Record who actually agreed and when. Do not infer consent from silence or mark
a decision Accepted because code exists. Issue ownership does not grant
authority over cross-cutting scope; those decisions need the product owner.

```text
Decision ID and question:
Status: Open / Proposed / Accepted / Rejected / Conditional
Owner and participants:
Options considered:
Evidence and uncertainties:
Chosen option and rationale:
User-visible consequences:
Technical, privacy, cost and evaluation consequences:
Dependencies / capability gates:
Rejected alternatives and why:
What evidence would reverse this decision:
Linked contract, test, source and ideation record:
```