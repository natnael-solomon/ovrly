# 0003: CP2 submission scope review

**Question:** Will AC08 (optional account with saved-report recovery, AN-10
#36) and AC10 (export and accessibility pass, AN-11 #39) be complete in the
submission, or disclosed as incomplete?

**Status:** Open. To be decided at the CP2 review before the 9 October 2026
deadline. Nothing here is a decision yet.

**Owner and participants:** natnael-solomon decides. Nattyy-1 (backend,
contracts) and Neb-iyu (research, pipeline) participate because AC08 depends on
BE-05 identity (#19) and BE-10 reports (#33). Record who agreed and when.

**Options considered:** Complete both; disclose specific incomplete parts of
one or both; change the submission plan after explicit agreement. A `stretch`
label alone is not a scope decision.

**Evidence and uncertainties:** Issue #5 estimates roughly 60 person-days of
backlog against about 33 available. That estimate has not been revalidated.
AN-10 and AN-11 are both Later priority and blocked behind #19, #33 and #34.
No end-to-end completion evidence exists for either criterion.

**Chosen option and rationale:** Pending. Until a decision is recorded, both
criteria remain in the contract and no reduced submission is presented as
complete.

**User-visible consequences:** Any missing export, recovery or accessibility
capability must be described accurately in the app, the demo and the
submission. Do not promise recovery from session-local sample saves or full
accessibility from static previews.

**Technical, privacy, cost and evaluation consequences:** List each unmet
subcondition, its dependencies and the evidence needed to complete it later.
Accessibility is not expendable because it shares AC10 with export.

**Dependencies / capability gates:** BC-D07, BC-D08,
[BC-D09](BC-D09-deadline-evidence.md); real integrated results; authorized
device and accessibility checks.

**Rejected alternatives and why:** Silent cuts, retroactively marking criteria
passed, or presenting a fixture as a delivered capability.

**What evidence would reverse this decision:** Once recorded, new completion
evidence or a changed organizer or owner requirement triggers another review.

## Disclosure wording to complete at the review

> This submission does not yet complete [specific AC08/AC10 subconditions].
> It demonstrates [verified subset and evidence]. [User-visible consequences]
> remain limitations. This boundary was agreed by [participants] on [date];
> the remaining requirements are [retained or explicitly revised].

**Links:** Build contract sections 6 and 7; issues #5, #36, #39; RFC-D66.
