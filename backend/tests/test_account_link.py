"""Account linking per BC-D07: in-place upgrade, second-device merge and the owner invariant."""

import asyncio
import uuid

import httpx
import pytest
from google.auth.exceptions import GoogleAuthError
from sqlalchemy import func, select, update
from test_intake_api import URL_BODY, assert_error, completed_upload, guest
from test_reports_api import publish

from services.api.auth.dependency import Principal
from services.api.auth.google import GoogleIdTokenVerifier, InvalidIdToken, VerifiedIdentity
from services.api.auth.linking import link_account, transfer_saved_reports
from services.api.errors import ApiError
from services.api.main import create_app
from services.models import (
    credentials,
    idempotency_keys,
    investigations,
    principals,
    report_versions,
    saved_reports,
    uploads,
    voice_actions,
)
from services.reports import save_owned_report
from services.settings import Settings

LINK = "/v1/principals/link"


class FakeVerifier:
    """Maps synthetic tokens to subjects; anything else is invalid. Never contacts Google.

    Subjects are unique per verifier instance because the tests share one database.
    """

    def __init__(self):
        run = uuid.uuid4().hex[:8]
        self.tokens = {"token-alice": f"sub-alice-{run}", "token-bob": f"sub-bob-{run}"}
        self.seen = []

    async def verify(self, token):
        self.seen.append(token)
        if token not in self.tokens:
            raise InvalidIdToken
        return VerifiedIdentity("google", self.tokens[token])


@pytest.fixture
def verifier():
    return FakeVerifier()


@pytest.fixture
async def app(database_url, tmp_path, verifier):
    application = create_app(
        Settings(database_url=database_url, storage_dir=tmp_path / "uploads", _env_file=None),
        id_token_verifier=verifier,
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


def link_body(token):
    return {"provider": "google", "id_token": token}


async def principal_row(app, principal_id):
    async with app.state.database.engine.connect() as connection:
        return (
            await connection.execute(
                select(principals).where(principals.c.id == uuid.UUID(principal_id))
            )
        ).one()


async def owned_ids(app, table, owner_id):
    async with app.state.database.engine.connect() as connection:
        rows = await connection.execute(
            select(table.c.id).where(table.c.owner_id == uuid.UUID(owner_id))
        )
        return sorted(str(row.id) for row in rows)


async def create_investigation(client, headers, key):
    response = await client.post(
        "/v1/investigations", json=URL_BODY, headers={**headers, "Idempotency-Key": key}
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def test_link_unavailable_without_a_configured_client_id(database_url, tmp_path):
    app = create_app(Settings(database_url=database_url, storage_dir=tmp_path, _env_file=None))
    assert app.state.id_token_verifier is None
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            headers = await guest(client)
            response = await client.post(LINK, json=link_body("token-alice"), headers=headers)
    body = assert_error(response, 503, "ACCOUNT_LINK_UNAVAILABLE", "none")
    assert body["retryable"] is False


def test_configured_client_id_selects_the_google_verifier(database_url, tmp_path):
    app = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path,
            google_client_id="synthetic-client.apps.googleusercontent.com",
            _env_file=None,
        )
    )
    assert isinstance(app.state.id_token_verifier, GoogleIdTokenVerifier)
    assert app.state.id_token_verifier.client_id == "synthetic-client.apps.googleusercontent.com"
    with pytest.raises(ValueError):
        GoogleIdTokenVerifier("")


async def test_link_requires_bearer_and_valid_body(client, verifier):
    assert_error(
        await client.post(LINK, json=link_body("token-alice")), 401, "AUTHENTICATION_REQUIRED"
    )
    headers = await guest(client)
    for body in (
        {"provider": "apple", "id_token": "x"},
        {"provider": "google"},
        {"provider": "google", "id_token": ""},
        {"provider": "google", "id_token": "x", "extra": True},
    ):
        assert_error(await client.post(LINK, json=body, headers=headers), 422, "VALIDATION_FAILED")
    assert verifier.seen == [], "Validation failures must not reach the verifier"
    invalid = await client.post(LINK, json=link_body("forged"), headers=headers)
    assert_error(invalid, 401, "INVALID_ID_TOKEN", "authenticate")
    assert "forged" not in invalid.text


async def test_guest_is_upgraded_in_place_and_keeps_its_objects(client, app, verifier):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "mine")
    me = (await client.get(f"/v1/investigations/{investigation_id}", headers=headers)).json()
    linked = await client.post(LINK, json=link_body("token-alice"), headers=headers)
    assert linked.status_code == 200, linked.text
    body = linked.json()
    guest_principal = await principal_row(app, body["principal_id"])
    assert body == {
        "principal_id": str(guest_principal.id),
        "kind": "account",
        "linked": True,
        "merged_saved_reports": 0,
        "credential": None,
    }
    assert guest_principal.kind == "account"
    assert guest_principal.google_sub == verifier.tokens["token-alice"]
    assert guest_principal.merged_into is None
    # The same credential keeps working and every object keeps its owner.
    again = await client.get(f"/v1/investigations/{investigation_id}", headers=headers)
    assert again.status_code == 200 and again.json() == me
    assert await owned_ids(app, investigations, body["principal_id"]) == [investigation_id]
    repeat = await client.post(LINK, json=link_body("token-alice"), headers=headers)
    assert repeat.status_code == 200 and repeat.json() == body
    other = await client.post(LINK, json=link_body("token-bob"), headers=headers)
    assert_error(other, 409, "ACCOUNT_ALREADY_LINKED", "none")
    unchanged = await principal_row(app, body["principal_id"])
    assert unchanged.google_sub == verifier.tokens["token-alice"]


async def mint(client):
    response = await client.post("/v1/principals/guest")
    assert response.status_code == 201, response.text
    body = response.json()
    return body["principal_id"], {"Authorization": f"Bearer {body['credential']['token']}"}


async def test_second_device_continues_as_the_existing_account(client, app, verifier):
    account_id, first_device = await mint(client)
    linked = await client.post(LINK, json=link_body("token-alice"), headers=first_device)
    assert linked.status_code == 200 and linked.json()["principal_id"] == account_id
    account_investigation = await create_investigation(client, first_device, "first-device")
    account_report = await publish(app, account_investigation)
    saved = await client.post(f"/v1/reports/{account_report.id}/save", headers=first_device)
    assert saved.status_code == 200, saved.text

    guest_id, second_device = await mint(client)
    guest_upload = await completed_upload(client, second_device)
    guest_investigation = await create_investigation(client, second_device, "second-device")
    guest_report = await publish(app, guest_investigation)
    guest_saved = await client.post(f"/v1/reports/{guest_report.id}/save", headers=second_device)
    assert guest_saved.status_code == 200, guest_saved.text

    merged = await client.post(LINK, json=link_body("token-alice"), headers=second_device)
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["principal_id"] == account_id and body["kind"] == "account"
    assert body["linked"] is True and body["merged_saved_reports"] == 1
    token = body["credential"]["token"]
    assert set(body["credential"]) == {"token", "token_type"}
    assert "bearer" in body["credential"].values() and token.startswith("ovk_")
    continued = {"Authorization": f"Bearer {token}"}

    # The device now acts as the account; the guest credential is revoked.
    listing = await client.get("/v1/investigations", headers=continued)
    assert [item["id"] for item in listing.json()["items"]] == [account_investigation]
    assert_error(
        await client.get("/v1/investigations", headers=second_device), 401, "INVALID_CREDENTIAL"
    )
    assert_error(
        await client.get(f"/v1/investigations/{guest_investigation}", headers=continued),
        404,
        "NOT_FOUND",
    )
    # The explicitly saved report moved with its snapshot; the account's own save is kept.
    recovered = await client.get("/v1/reports/saved", headers=continued)
    assert [item["report_id"] for item in recovered.json()["items"]] == [
        guest_report.id,
        account_report.id,
    ]
    assert recovered.json()["items"][0] == guest_saved.json()
    resaved = await client.post(f"/v1/reports/{guest_report.id}/save", headers=continued)
    assert resaved.status_code == 200 and resaved.json() == guest_saved.json()
    assert await saved_owners(app, [guest_report.id, account_report.id]) == {
        guest_report.id: account_id,
        account_report.id: account_id,
    }
    # The report version itself, like investigations, uploads and idempotency keys, stays
    # with the revoked guest.
    assert await owned_ids(app, report_versions, guest_id) == [guest_report.id]
    assert await owned_ids(app, investigations, guest_id) == [guest_investigation]
    assert await owned_ids(app, uploads, guest_id) == [guest_upload["id"]]
    assert await owned_ids(app, investigations, account_id) == [account_investigation]
    assert await owned_ids(app, uploads, account_id) == []
    # The account can remove the moved save; afterwards the guest's version is not its own.
    removed = await client.delete(f"/v1/reports/{guest_report.id}/save", headers=continued)
    assert removed.status_code == 204, removed.text
    assert_error(
        await client.delete(f"/v1/reports/{guest_report.id}/save", headers=continued),
        404,
        "NOT_FOUND",
    )
    assert await saved_owners(app, [guest_report.id]) == {}
    async with app.state.database.engine.connect() as connection:
        keys = (
            await connection.execute(
                select(idempotency_keys.c.owner_id, idempotency_keys.c.key)
                .where(
                    idempotency_keys.c.owner_id.in_([uuid.UUID(account_id), uuid.UUID(guest_id)])
                )
                .order_by(idempotency_keys.c.key)
            )
        ).all()
        guest_credentials = (
            await connection.execute(
                select(credentials.c.revoked_at).where(
                    credentials.c.principal_id == uuid.UUID(guest_id)
                )
            )
        ).all()
        account_credentials = (
            await connection.execute(
                select(credentials.c.revoked_at).where(
                    credentials.c.principal_id == uuid.UUID(account_id)
                )
            )
        ).all()
    assert [(str(owner), key) for owner, key in keys] == [
        (account_id, "first-device"),
        (guest_id, "second-device"),
    ]
    assert len(guest_credentials) == 1 and guest_credentials[0].revoked_at is not None
    assert len(account_credentials) == 2 and all(c.revoked_at is None for c in account_credentials)
    guest_row = await principal_row(app, guest_id)
    assert guest_row.kind == "guest" and guest_row.google_sub is None
    assert str(guest_row.merged_into) == account_id and guest_row.merged_at is not None
    account_row = await principal_row(app, account_id)
    assert account_row.google_sub == verifier.tokens["token-alice"]
    assert account_row.merged_into is None


async def saved_owners(app, report_ids):
    async with app.state.database.engine.connect() as connection:
        rows = await connection.execute(
            select(saved_reports.c.report_id, saved_reports.c.owner_id).where(
                saved_reports.c.report_id.in_([uuid.UUID(value) for value in report_ids])
            )
        )
        return {str(row.report_id): str(row.owner_id) for row in rows}


async def test_link_cannot_move_another_owners_rows(client, app):
    bystander_id, bystander = await mint(client)
    bystander_upload = await completed_upload(client, bystander)
    bystander_investigation = await create_investigation(client, bystander, "bystander")
    bystander_report = await publish(app, bystander_investigation)
    kept = await client.post(f"/v1/reports/{bystander_report.id}/save", headers=bystander)
    assert kept.status_code == 200
    account_id, first_device = await mint(client)
    linked = await client.post(LINK, json=link_body("token-bob"), headers=first_device)
    assert linked.status_code == 200

    guest_id, second_device = await mint(client)
    guest_investigation = await create_investigation(client, second_device, "guest")
    crafted = {**link_body("token-bob"), "principal_id": bystander_id}
    assert_error(
        await client.post(LINK, json=crafted, headers=second_device),
        422,
        "CLIENT_IDENTITY_REJECTED",
    )
    assert_error(
        await client.post(
            LINK,
            json=link_body("token-bob"),
            headers={**second_device, "X-Owner-Id": bystander_id},
        ),
        400,
        "CLIENT_IDENTITY_REJECTED",
    )
    assert_error(
        await client.post(
            LINK,
            json=link_body("token-bob"),
            params={"user_id": bystander_id},
            headers=second_device,
        ),
        400,
        "CLIENT_IDENTITY_REJECTED",
    )
    merged = await client.post(LINK, json=link_body("token-bob"), headers=second_device)
    assert merged.status_code == 200 and merged.json()["principal_id"] == account_id
    assert merged.json()["merged_saved_reports"] == 0

    # Only the calling guest was touched; the bystander's rows and credential are intact.
    assert await owned_ids(app, investigations, bystander_id) == [bystander_investigation]
    assert await owned_ids(app, uploads, bystander_id) == [bystander_upload["id"]]
    assert await owned_ids(app, investigations, guest_id) == [guest_investigation]
    assert await owned_ids(app, investigations, account_id) == []
    mine = await client.get("/v1/investigations", headers=bystander)
    assert [item["id"] for item in mine.json()["items"]] == [bystander_investigation]
    bystander_row = await principal_row(app, bystander_id)
    assert bystander_row.kind == "guest" and bystander_row.merged_into is None
    async with app.state.database.engine.begin() as connection:
        # The transfer is restricted to the given owner; the guest has no saves left.
        moved = await transfer_saved_reports(connection, uuid.UUID(guest_id), uuid.UUID(account_id))
    assert moved == 0
    assert await owned_ids(app, investigations, guest_id) == [guest_investigation]
    assert await saved_owners(app, [bystander_report.id]) == {bystander_report.id: bystander_id}
    listing = await client.get("/v1/reports/saved", headers=bystander)
    assert [item["report_id"] for item in listing.json()["items"]] == [bystander_report.id]


async def test_transfer_keeps_the_accounts_save_when_both_saved_one_report(client, app):
    first_id, first = await mint(client)
    second_id, second = await mint(client)
    report = await publish(app, await create_investigation(client, first, "shared"))
    other = await publish(app, await create_investigation(client, first, "other"))
    for report_id in (report.id, other.id):
        assert (
            await client.post(f"/v1/reports/{report_id}/save", headers=first)
        ).status_code == 200
    account_save = (await client.get("/v1/reports/saved", headers=first)).json()["items"]
    async with app.state.database.engine.begin() as connection:
        # Give the second principal a later save of one of the same reports.
        rows = (
            await connection.execute(
                select(saved_reports).where(saved_reports.c.owner_id == uuid.UUID(first_id))
            )
        ).all()
        for row in rows:
            await connection.execute(
                saved_reports.insert().values({**row._mapping, "owner_id": uuid.UUID(second_id)})
            )
        await connection.execute(
            saved_reports.delete().where(
                saved_reports.c.owner_id == uuid.UUID(first_id),
                saved_reports.c.report_id == uuid.UUID(other.id),
            )
        )
        moved = await transfer_saved_reports(connection, uuid.UUID(second_id), uuid.UUID(first_id))
    assert moved == 1
    after = (await client.get("/v1/reports/saved", headers=first)).json()["items"]
    assert sorted(item["report_id"] for item in after) == sorted([report.id, other.id])
    kept = next(item for item in after if item["report_id"] == report.id)
    assert kept == next(item for item in account_save if item["report_id"] == report.id)
    assert (await client.get("/v1/reports/saved", headers=second)).json() == {"items": []}


async def test_google_verifier_maps_outcomes_without_network(monkeypatch):
    verifier = GoogleIdTokenVerifier("client-id.apps.googleusercontent.com")
    calls = []

    outcomes = {
        "bad-signature": ValueError("Token has wrong audience"),
        "transport": GoogleAuthError("certificate fetch failed"),
        "wrong-issuer": {"iss": "https://evil.example", "sub": "123"},
        "no-subject": {"iss": "accounts.google.com"},
        "good": {"iss": "https://accounts.google.com", "sub": "1234567890", "email": "x@y"},
    }

    def fake_verify(raw, request, audience):
        calls.append((raw, audience))
        outcome = outcomes[raw]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("services.api.auth.google.id_token.verify_oauth2_token", fake_verify)
    identity = await verifier.verify("good")
    assert identity == VerifiedIdentity("google", "1234567890")
    for token in ("bad-signature", "transport", "wrong-issuer", "no-subject"):
        with pytest.raises(InvalidIdToken):
            await verifier.verify(token)
    assert all(audience == "client-id.apps.googleusercontent.com" for _, audience in calls)
    assert len(calls) == 5


async def test_save_by_a_merged_guest_is_refused_and_stores_nothing(client, app):
    """A save authenticated before a second-device link commits must not land under the
    merged guest: the save transaction share-locks the principal and sees merged_into."""
    account_id, first_device = await mint(client)
    assert (
        await client.post(LINK, json=link_body("token-alice"), headers=first_device)
    ).status_code == 200
    guest_id, second_device = await mint(client)
    report = await publish(app, await create_investigation(client, second_device, "late"))
    async with app.state.database.engine.begin() as connection:
        # The state a racing save observes once the link transaction has committed.
        await connection.execute(
            update(principals)
            .where(principals.c.id == uuid.UUID(guest_id))
            .values(merged_into=uuid.UUID(account_id), merged_at=func.now())
        )
    async with app.state.database.engine.begin() as connection:
        with pytest.raises(ApiError) as refused:
            await save_owned_report(
                connection, Principal(uuid.UUID(guest_id), "guest"), uuid.UUID(report.id)
            )
    assert (refused.value.status_code, refused.value.code) == (401, "INVALID_CREDENTIAL")
    voice = {
        "request_id": "req_synthetic_merged_save",
        "action": "save_report",
        "target": {"kind": "report", "id": report.id},
    }
    # Both HTTP save paths give the revoked-credential answer while the credential is valid.
    assert_error(
        await client.post(f"/v1/reports/{report.id}/save", headers=second_device),
        401,
        "INVALID_CREDENTIAL",
    )
    assert_error(
        await client.post("/v1/voice/actions", json=voice, headers=second_device),
        401,
        "INVALID_CREDENTIAL",
    )
    assert await saved_owners(app, [report.id]) == {}
    async with app.state.database.engine.connect() as connection:
        audited = await connection.scalar(
            select(func.count())
            .select_from(voice_actions)
            .where(voice_actions.c.request_id == "req_synthetic_merged_save")
        )
    assert audited == 0


async def test_save_waits_for_a_concurrent_link_and_is_refused(client, app, verifier):
    """True race: the link holds the guest row lock; the save waits, then is refused."""
    account_id, first_device = await mint(client)
    assert (
        await client.post(LINK, json=link_body("token-bob"), headers=first_device)
    ).status_code == 200
    guest_id, second_device = await mint(client)
    report = await publish(app, await create_investigation(client, second_device, "race"))
    guest = Principal(uuid.UUID(guest_id), "guest")
    engine = app.state.database.engine
    async with engine.connect() as link_connection:
        link_transaction = await link_connection.begin()
        await link_account(
            link_connection, guest, VerifiedIdentity("google", verifier.tokens["token-bob"])
        )

        async def save():
            async with engine.begin() as connection:
                await save_owned_report(connection, guest, uuid.UUID(report.id))

        saving = asyncio.create_task(save())
        await asyncio.sleep(0.3)
        assert not saving.done(), "The save must wait for the link's row lock"
        await link_transaction.commit()
        with pytest.raises(ApiError) as refused:
            await asyncio.wait_for(saving, 5)
    assert refused.value.code == "INVALID_CREDENTIAL"
    assert await saved_owners(app, [report.id]) == {}
    assert (await principal_row(app, guest_id)).merged_into == uuid.UUID(account_id)
