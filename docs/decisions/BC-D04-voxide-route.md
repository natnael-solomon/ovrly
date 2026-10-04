# BC-D04: Voxide Android route, session budget and action allowlist

Also records RFC-D24 (native Voxide / WebView / browser companion). One
decision, two identifiers.

**Question:** How does the Android app integrate Voxide, how is the sponsored
session allowance controlled, and which voice actions are allowed?

**Status:** Accepted for the route, key handling and budget policy on
3 October 2026. The final action allowlist is Conditional until AN-09 (#35)
wires voice to the backend.

**Owner and participants:** natnael-solomon (product owner, AN-02 owner).
The sponsor confirmed the route in chat; the exact wording is held privately.

## Options considered

1. Native Kotlin client calling Voxide's live endpoint over HTTPS and WebSocket
   with the publishable key. The endpoint is documented for the browser SDK.
2. WebView hosting the Voxide browser SDK inside the app.
3. Browser companion page outside the app.

## Evidence and uncertainties

- Browser lab, 1 October 2026: two sessions proved open, cancel, repeated-cancel
  rejection and save against synthetic data.
- Native device runs, 2 October 2026, Samsung SM-A217F (Android 12): speech in,
  audible replies, real tab navigation. One supervised run lasted past five
  minutes; the final run lasted 2 min 54 s with 4 tool calls and 4 results, 15
  completed turns, two provider interruptions and zero cleanup failures.
- Fixed along the way: a stop-time crash from socket cancellation on the UI
  thread, an eight-slot playback queue that overflowed on small chunks, and a
  16-message inbox cutoff. Each has a regression test.
- Sponsor confirmation of the native route was given in chat on or before
  3 October 2026. No written organizer statement is on file.
- Voxide's definition of a billable session (connect, first audio, per minute)
  and refund behaviour for disconnects are not documented. Working assumption:
  one session = one successful connect, no refund.
- Dashboard balance on 3 October 2026: 84 of 100 sessions remaining. Local
  logs account for 10 native and 2 browser attempts; the 4-session gap is
  unexplained and the dashboard figure is authoritative.

## Chosen option and rationale

Option 1, the native client. It works on the target device, needs no WebView
or bridge, and the sponsor accepted it. The code states that it calls a
browser-documented endpoint natively and does not impersonate a browser Origin.

Key handling: only a publishable key (`vox_pub_` prefix) is accepted, enforced
by pattern at runtime; a secret key is rejected. The key lives in the ignored
`android/voxide.local.properties` and is compiled in only when the build sets
`VOXIDE_LIVE=1`. CI rejects the live opt-in. Every other build, debug or
release, runs the offline simulation labelled "Demo".

Session budget: 100 sessions, allocated 50 development, 30 rehearsal and device
checks, 20 demo reserve. The Voxide dashboard is the only ledger. The app has
no local attempt cap; the earlier persistent attempt counter was removed because
a per-device counter cannot see the team's shared usage. Live builds are the
control: no live key, no session.

Action allowlist (conditional): `open_check`, `save_report`, `queue_cancel`,
`queue_retry`, `queue_continue`, each with a required typed target. A sixth
client-local action, `switch_tab` (Your space / Explore), is provisional; it is
the only action live today and never reaches the backend. No delete, publish or
settings actions by voice. The final list is fixed at AN-09 (#35) and mirrored
in the voice-actions contract (BE-13, #67).

## User-visible consequences

The orb shows "Demo" and simulates a session in any build without the live
key. Live voice needs microphone permission, stops when capture starts or the
app leaves the foreground, never auto-starts and never reconnects. Denied
microphone, disconnects and unsupported commands are visible errors with a
typed alternative.

## Technical, privacy, cost and evaluation consequences

Audio leaves the device only in live builds. No secret key exists in the app.
Each live connect spends one session from a shared allowance, so device checks
are planned, supervised and recorded against the dashboard. Unit and CI tests
use the mock transport; zero real sessions from CI.

## Dependencies / capability gates

AC09. BE-10 (#33) provides `POST /v1/voice/actions` with server-enforced
allowlist and ownership checks; BE-13 (#67) fixes the schema; AN-09 (#35) wires
the client. #10 closed on 3 October 2026 via PR #69.

## Rejected alternatives and why

WebView SDK: adds a web runtime and bridge for no demonstrated benefit once the
native route was accepted. Browser companion: not an Android app experience.
Persistent local attempt counter: false sense of control over a shared budget.

## What evidence would reverse this decision

A sponsor or organizer statement withdrawing acceptance of the native route;
Voxide rejecting non-browser clients; a documented session definition that
makes the one-connect assumption wrong by more than the demo reserve.

## Links

Build contract section 5 (BC-D04) and section 6 (AC09); RFC section 09,
section 14 Voxide table, RFC-D23 and RFC-D24; issues #10, #33, #35, #67;
PRs #68 and #69.
