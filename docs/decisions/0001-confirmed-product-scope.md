# 0001: Confirmed product scope

**Question:** Which product promises govern implementation when the research
contains alternative recommendations?

**Status:** Accepted. Confirmed by the product owner on 19 September 2026 in
the main-ideas brief; recorded here on 4 October 2026. This record does not
claim the capabilities are implemented.

**Owner and participants:** natnael-solomon (product owner). Delivery
ownership follows the actual issue assignees on the board (BC-D01).

**Options considered:** Share-first or companion-only reduction; audio-only
checking; both journeys with speech and readable text; mandatory versus
optional accounts; voice in the overlay versus companion controls.

**Evidence and uncertainties:** The owner's brief supersedes conflicting
18 September research recommendations. The research and ideation material
motivates evaluation but does not prove product effectiveness, universal
provider access or a feasible schedule.

## Chosen option and rationale

Two required entry points:

| Entry point | Boundary |
| --- | --- |
| Live overlay | Explicitly started captured interval of up to 3 minutes, processed incrementally while capture runs. |
| Share to Ovrly | Supported link or permitted file for a complete accessible video of up to 10 minutes, analyzed across its full eligible duration. |

Both paths cover spoken claims and readable on-screen text. Android first,
English first. Free with clear usage limits. Advertise only demonstrated
provider, input and device combinations. If feasibility fails, narrow those
combinations before removing either entry point; a reduced submission needs an
explicit decision and disclosure (see [0003](0003-scope-review-cp2.md)).

Checking works without sign-in. An optional account recovers explicitly saved
reports on another Android device; raw recordings are never synced, and
temporary history and active jobs have no cross-device recovery promise.

Voxide controls the companion only: open checks, save reports, perform the
agreed queue actions. It does not activate overlay capture. Direct controls
stay available. Route and allowlist are in [BC-D04](BC-D04-voxide-route.md).

## Exclusions

Payments, non-Android clients, chart interpretation, visual-only
demonstrations, automatic swipe detection, creator credibility scores, inferred
political or bias profiles, critical-thinking games, fallacy scoring and
personalized reading recommendations. A public full-report website, voice
coaching and evidence conversation are not required.

**User-visible consequences:** Limits, unavailable inputs, coverage gaps,
provisional updates, failures, no claims and insufficient evidence must each be
understandable without implying a blanket truth verdict.

**Technical, privacy, cost and evaluation consequences:** Both paths need
timebases, modality coverage and one shared investigation model. Remote
processing needs rights, consent, region, retention and spending decisions.
Recognition, retrieval, assessment and user understanding are measured
separately. No latency or accuracy figure is accepted by citation alone.

**Dependencies / capability gates:** BC-D02 to BC-D08; AC01 to AC10.

**Rejected alternatives and why:** No silent companion-only or audio-only
substitute, no automatic swipe capture, no microphone as a silent fallback.
Each would quietly change what the product promises.

**What evidence would reverse this decision:** A documented owner decision
responding to measured feasibility or capacity, with changed acceptance
conditions and submission disclosure. Failure alone is not permission to cut.

**Links:** Build contract sections 1 to 3 and 6; owner's main-ideas brief,
19 September 2026; RFC section 22 (RFC-D02, RFC-D23, RFC-D55, RFC-D66).
