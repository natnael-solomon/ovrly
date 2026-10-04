# Decision records

Lightweight decision log for Ovrly. Each record answers one question with the
RFC section 22 template below. Records distinguish confirmed choices from open
ones; a record exists so the team can see what was decided, by whom, on what
evidence, and what would reverse it.

| Record | Status | Purpose |
| --- | --- | --- |
| [0001: confirmed product scope](0001-confirmed-product-scope.md) | Accepted | The two journeys and their boundaries, from the owner's 19 September 2026 brief. |
| [0002: task tracking](0002-task-tracking.md) | Accepted | GitHub Project replaces the Linear proposal; AC, BC-D and RFC-D namespaces. |
| [0003: CP2 scope review](0003-scope-review-cp2.md) | Open | Explicit submission decision for AC08 and AC10 with disclosure wording. |
| [BC-D04: Voxide route and allowlist](BC-D04-voxide-route.md) | Accepted, allowlist conditional | Native route confirmed with the sponsor; session budget; action allowlist pending #35. |
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