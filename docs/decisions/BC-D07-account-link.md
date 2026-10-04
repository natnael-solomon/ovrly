# BC-D07: Account link flow

**Question:** How does a guest optionally link an account so explicitly saved
reports can be recovered on another Android device, without making sign-in a
requirement for checking?

**Status:** Accepted. Decided by the product owner on 4 October 2026 and
implemented in BE-05 part 2 (#19). The Android client (AN-10, #36) and the
saved-reports table (BE-10, #33) are separate deliveries; this record does not
claim either is complete.

**Owner and participants:** natnael-solomon (product owner, BE-05 owner).
Nattyy-1 (backend, contracts) and the AN-10 owner consume the endpoint.

## Options considered

1. Google sign-in only, verified server-side from the Google ID token; the
   guest principal is upgraded in place, and a second device merges into the
   existing account.
2. Several providers (Google, Apple, email magic link) behind one link endpoint.
3. Backend-owned username/password accounts.
4. Create a new account principal on link and re-parent every guest object to it.

## Evidence and uncertainties

- Checking must work without sign-in and an optional account recovers only
  explicitly saved reports ([0001](0001-confirmed-product-scope.md); RFC
  section 15 "Authentication options"; RFC-D55).
- Every Android 10+ target device ships Google Play services; Credential
  Manager (`androidx.credentials` with `googleid`) returns a Google ID token
  without a browser round trip or a client secret on the device.
- Google ID tokens are signed JWTs with a stable `sub`; the `google-auth`
  library verifies signature, expiry, issuer and audience against published
  keys. The backend needs only the Web client ID, which is configuration, not
  a secret.
- Saved reports have no table yet (#33), so the transfer step is a function
  that currently moves nothing and returns zero. Its contract is fixed here so
  #33 adds the table without changing the endpoint.
- Not verified: Credential Manager behaviour on devices without Play services,
  and Google Cloud project quotas. Both are AN-10 concerns.

## Chosen option and rationale

Option 1. One provider keeps the hackathon scope small, needs no password
storage and matches the devices the product targets.

`POST /v1/principals/link` with body `{"provider": "google", "id_token": ...}`,
bearer-authenticated as the calling guest. The backend verifies the token
against `OVRLY_GOOGLE_CLIENT_ID` through a small `IdTokenVerifier` protocol so
tests inject a fake and never call Google.

| Case | Behaviour |
| --- | --- |
| Subject unknown | Upgrade in place: the calling principal gains `google_sub`, its `kind` becomes `account`, its credential stays valid and every object keeps its owner. `200 {principal_id, kind: "account", linked: true, merged_saved_reports: 0, credential: null}`. |
| Same subject, same principal | Idempotent: the same `200`. |
| Principal already linked to a different subject | `409 ACCOUNT_ALREADY_LINKED`. |
| Subject already belongs to principal A (second device) | In one transaction: transfer the calling guest's explicitly saved reports to A, revoke the guest's credentials, record `merged_into` on the guest row, mint a new credential for A. `200` with `principal_id = A` and the new `credential` so the device continues as A. |
| Server has no client ID | `503 ACCOUNT_LINK_UNAVAILABLE`, action `none`. |
| Bad signature, audience, issuer or expiry | `401 INVALID_ID_TOKEN`, action `authenticate`. |

Investigations, uploads, idempotency keys and temporary history are never
transferred; they stay with the revoked guest and expire by retention. All
writes are restricted to the calling principal's id taken from its credential;
client-supplied principal identifiers are rejected (`CLIENT_IDENTITY_REJECTED`).
Links for one subject are serialised with a transaction-scoped advisory lock so
two devices cannot both become the account.

## User-visible consequences

Checking never requires sign-in. Linking is one tap through Credential Manager.
On a second device the user sees their saved reports and the device silently
continues as the account; the previous guest history on that device is not
merged, and the client copy must say so (#36 checklist). A device that is
already an account cannot switch to a different Google account in place.

## Technical, privacy, cost and evaluation consequences

The backend stores the Google `sub` and nothing else from the token (no email,
name or picture). Token verification fetches Google's public keys over HTTPS;
no other provider is contacted and no secret is held. Revoked guest
credentials fail with `401 INVALID_CREDENTIAL`. Migration `0005_account_link`
adds `google_sub` (unique), `merged_into` and `merged_at` to `principals` with
a downgrade. The `services/api/auth` package stays under the 90 percent
coverage floor check. No cost beyond Google's free sign-in quota.

## Dependencies / capability gates

AC08. BE-10 (#33) adds the saved-reports table and the `UPDATE ... WHERE
owner_id = guest` inside `transfer_saved_reports`. AN-10 (#36) stores the
guest credential securely, calls the endpoint and swaps to the returned
credential. #15 exports the request and response models to
`packages/contracts`.

## Rejected alternatives and why

Multiple providers: more client surfaces and review time for no demonstrated
user need before the deadline. Passwords: storage, reset flows and breach
exposure for a hackathon product. Re-parenting every guest object to a new
account principal: more rows touched, larger transactions and a worse story for
the second-device case, where the product promise is that temporary history
does not follow the account.

## What evidence would reverse this decision

A target device population without Play services, a Google policy change that
blocks Credential Manager for this app, or an owner decision to support a
second provider. Each would add a provider behind the same endpoint rather than
change the merge semantics.

## Links

Build contract section 5 (BC-D07) and section 6 (AC08); RFC section 15 and
RFC-D55; [0001](0001-confirmed-product-scope.md); issues #19, #33, #36; PR #74
(BE-05 part 1).
