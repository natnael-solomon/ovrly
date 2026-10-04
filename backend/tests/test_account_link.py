"""Account linking per BC-D07: in-place upgrade, second-device merge and the owner invariant."""

import uuid

import httpx
import pytest
from google.auth.exceptions import GoogleAuthError
from sqlalchemy import select
from test_intake_api import URL_BODY, assert_error, completed_upload, guest

from services.api.auth.google import GoogleIdTokenVerifier, InvalidIdToken, VerifiedIdentity
from services.api.auth.linking import transfer_saved_reports
from services.api.main import create_app
from services.models import credentials, idempotency_keys, investigations, principals, uploads
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

    guest_id, second_device = await mint(client)
    guest_upload = await completed_upload(client, second_device)
    guest_investigation = await create_investigation(client, second_device, "second-device")

    merged = await client.post(LINK, json=link_body("token-alice"), headers=second_device)
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["principal_id"] == account_id and body["kind"] == "account"
    assert body["linked"] is True and body["merged_saved_reports"] == 0
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
    # Investigations, uploads and idempotency keys stay with the revoked guest.
    assert await owned_ids(app, investigations, guest_id) == [guest_investigation]
    assert await owned_ids(app, uploads, guest_id) == [guest_upload["id"]]
    assert await owned_ids(app, investigations, account_id) == [account_investigation]
    assert await owned_ids(app, uploads, account_id) == []
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


async def test_link_cannot_move_another_owners_rows(client, app):
    bystander_id, bystander = await mint(client)
    bystander_upload = await completed_upload(client, bystander)
    bystander_investigation = await create_investigation(client, bystander, "bystander")
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
        # The transfer is restricted to the given owner and, until #33, moves nothing.
        moved = await transfer_saved_reports(connection, uuid.UUID(guest_id), uuid.UUID(account_id))
    assert moved == 0
    assert await owned_ids(app, investigations, guest_id) == [guest_investigation]


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
