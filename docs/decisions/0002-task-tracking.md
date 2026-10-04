# 0002: Task tracking and reference IDs

**Question:** Where are tasks and decisions tracked, and how are the
overlapping ID schemes told apart?

**Status:** Accepted. The GitHub Project has been the working board since
24 September 2026; recorded here on 4 October 2026.

**Owner and participants:** natnael-solomon (repository owner). Collaborators
Nattyy-1 and Neb-iyu have write access to the repository and the board.

**Options considered:** The 21 September contract's Linear proposal versus the
configured GitHub Project; separate PR cards versus issues as the only cards.

**Evidence and uncertainties:** [WORKFLOW.md](../../WORKFLOW.md) section 1 and
the [ovrly development board](https://github.com/users/natnael-solomon/projects/3)
with 40 issues coded AN-, BE-, RES- and REPO-. The contract's statement that
the project had "no Git history" is stale: the repository was published on
23 September 2026.

## Chosen option and rationale

Use the GitHub Project "ovrly development". Issues are the task cards, each
with one owner, an outcome, dependencies and a completion checklist. PRs link
to their issue; no duplicate cards. Stages are Backlog, Ready, In progress,
In review and Done; fields are Area, Priority and Chain.

Reference namespaces:

| Prefix | Meaning | Source |
| --- | --- | --- |
| `AC01` to `AC10` | Acceptance criteria | Build contract section 6 |
| `BC-D01` to `BC-D09` | Build-contract decisions | Build contract section 5 |
| `RFC-D01` to `RFC-D84` | Research RFC decisions (bare D01 to D84 in the RFC) | RFC sections 22 and 24 |
| `AN-`, `BE-`, `RES-`, `REPO-` | Task codes | GitHub issues |

A bare `D24` in old material is ambiguous; check whether it means `BC-D` or
`RFC-D` before citing it. Task codes are never decision IDs.

**User-visible consequences:** One place for status. A draft PR or an
unapproved decision is not completion.

**Technical, privacy, cost and evaluation consequences:** Every PR needs
another developer's review, the required checks and device evidence where
relevant. Issues and the board are public; private reference material stays
out of both.

**Dependencies / capability gates:** None.

**Rejected alternatives and why:** Linear would split tracking from the code
host and the ruleset. PR cards would duplicate issue state.

**What evidence would reverse this decision:** A recorded team decision to
move tooling, with a migration plan.

**Links:** [WORKFLOW.md](../../WORKFLOW.md); issue #5; RFC-D41 and RFC-D66.
