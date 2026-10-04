# 0003: CP2 submission scope review

**Question:** Will AC08 (optional account with saved-report recovery, AN-10
#36) and AC10 (export and accessibility pass, AN-11 #39) be complete in the
submission, or disclosed as incomplete?

**Status:** Accepted. Decided by natnael-solomon on 4 October 2026, five days
before the 9 October 2026 deadline, instead of at the end of CP2, so the team
plans against a known scope.

**Owner and participants:** natnael-solomon decided. Nattyy-1 (backend,
contracts) and Neb-iyu (research, pipeline) participate because AC08 depends
on BE-05 identity (#19) and BE-10 reports (#33). Both agreed with the owner
outside GitHub on 4 October 2026; the owner confirmed that agreement when
merging the PR that adds this record.

**Options considered:** Complete both; disclose specific incomplete parts of
one or both; change the submission plan after explicit agreement. A `stretch`
label alone is not a scope decision.

**Evidence and uncertainties:**

- Capacity, checked 4 October 2026: about 45 person-days of open work by the
  original estimates, against about 30 achievable at the pace of 3 and
  4 October. The earlier 60 against 33 estimate in issue #5 is superseded.
- AC08 status: guest principals, owner-scoped access and Google account
  linking per [BC-D07](BC-D07-account-link.md) are on `main` (#19, PRs #74
  and #80). The saved-reports API (#33) and the Android sign-in and recovery
  flow (#36) do not exist. Second-device recovery also needs a Google OAuth
  client registered for both the debug and the release signing certificates,
  and evidence from two physical devices.
- AC10 status: the export contents and the accessibility checks in #39 are
  not started. The report screens they apply to (#34) are not started either.
  Export can use the report the device already holds, so it does not need a
  server export endpoint.

## Chosen option and rationale

| Criterion | Submission scope | Owner issues |
| --- | --- | --- |
| AC10 export | Complete. The device exports the loaded report through the Android share sheet: claim summary, source links, limitations, provisional label, report version and retrieval date; no raw media or transcript. The server export endpoint in #33 is not required for the submission. | #39, #34 |
| AC10 accessibility | Complete. Capture, stop and evidence controls work with large text, TalkBack and busy video backgrounds, checked on a physical device. | #39 |
| AC08 guest checking | Complete. | #19 (done), #36 |
| AC08 saved reports | Complete. An explicit save stores the report for its owner on the server. | #33, #36 |
| AC08 second-device recovery | Disclosed as incomplete by default. Work on it starts only after the AC01 to AC07 and AC09 paths work. If it is verified on two physical devices before the freeze, the disclosure is removed. | #36 |

Rationale: accessibility shares AC10 with export and is not expendable, and
on-device export is small once the report screen exists. Second-device
recovery has the highest remaining cost (OAuth client setup for two
certificates and two-device evidence) and the least demo value, and its
backend half is already built, so finishing it later costs little.

**User-visible consequences:** The app and the demo must not suggest that a
saved report can be restored on another device unless the disclosure is
removed. Recovery copy explains what is and is not recovered. Any missing
export or accessibility capability found at the freeze must be described
accurately; it is not silently cut.

**Technical, privacy, cost and evaluation consequences:** No server export
endpoint is built for the submission; export runs on the device from data it
already holds, so no new data leaves the device. Guest and saved-report data
follow the existing retention rules. Accessibility evidence follows WORKFLOW
section 4 device-evidence rules.

**Dependencies / capability gates:** BC-D07, BC-D08,
[BC-D09](BC-D09-deadline-evidence.md); #33 save endpoints; #34 report screens;
a Google OAuth client for second-device recovery; authorized device and
accessibility checks.

**Rejected alternatives and why:** Complete both in full: does not fit the
capacity above. Disclose AC10 export: export is cheap once the report screen
exists. Disclose all of AC08: guest checking and saving are core to both
journeys and mostly built. Silent cuts, retroactively marking criteria passed,
or presenting a fixture as a delivered capability remain rejected.

**What evidence would reverse this decision:** Second-device recovery verified
on two physical devices before the freeze removes its disclosure. An
accessibility check that fails at the freeze cannot be dropped; the failing
control is fixed or disclosed by name. A changed organizer or owner
requirement triggers another review.

## Disclosure wording

> This submission does not yet complete AC08 recovery of saved reports on a
> second Android device. It demonstrates guest checking and explicitly saved
> reports for one owner, with [verified evidence]. A report saved on one
> device cannot yet be restored on another device. This boundary was decided
> by natnael-solomon on 4 October 2026; the requirement is retained for after
> the submission.

Remove this wording if second-device recovery is verified before the freeze.

**Links:** Build contract sections 6 and 7; issues #5, #33, #34, #36, #39;
[BC-D07](BC-D07-account-link.md); RFC-D66.