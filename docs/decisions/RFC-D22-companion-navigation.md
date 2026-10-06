# RFC-D22: Companion navigation for real checks

**Question:** When the queue and reports become real (AN-08, #34), should the
companion keep Your space / Explore / Settings with Your space holding the
Inbox and Library, or adopt Inbox / Library / Settings as the three tabs?

**Status:** Accepted. Decided by natnael-solomon on 6 October 2026.

**Owner and participants:** natnael-solomon decided, on the options and
recommendation prepared for #34.

**Options considered:**

- A. Keep Your space / Explore / Settings. Your space shows the Inbox (checks in
  progress or needing attention) above the Library (finished reports), then
  the labeled saved samples. Explore stays the labeled sample gallery.
- B. Inbox / Library / Settings as three tabs, with the samples moved into
  Library under a labeled "Samples" section. The voice `open_tab` targets
  (`space`, `explore`) would be renamed, with their tests and documentation.

**Evidence and uncertainties:** Both options give the same user value: the
queue and reports are real and samples stay labeled. Option B changes the
client-local voice action while the BC-D04 allowlist is still conditional
(#35) and touches more screens three days before the 9 October 2026 deadline.
No user testing compared the two layouts.

## Chosen option and rationale

Option A. It delivers the Inbox and Library without changing the voice
`open_tab` targets, BC-D04 or the tab bar, and keeps the change small.

**User-visible consequences:** Your space opens on the Inbox, with stage and
age for each check and Cancel, Try again and Continue checking where they
apply, then the Library of finished reports. A real report opens from either
list and closes with Back or when the tab changes. Sample reports stay in
Explore and under "Saved samples", labeled as illustrative.

**Technical, privacy, cost and evaluation consequences:** No new data leaves
the device; the screens read the Room store and the existing `/v1` routes.
The voice manifest is unchanged.

**Dependencies / capability gates:** #18 (data layer) and #33 (report
routes), both merged. #35 fixes the voice allowlist independently.

**Rejected alternatives and why:** Option B: a larger change to navigation and
voice for no extra user value before the deadline.

**What evidence would reverse this decision:** Usability evidence that users
do not find their checks under Your space, or a voice allowlist decision in
#35 that needs Inbox or Library as named targets.

**Links:** AN-08 (#34); AC04, AC05; RFC section 06 screen inventory;
[BC-D04](BC-D04-voxide-route.md).
