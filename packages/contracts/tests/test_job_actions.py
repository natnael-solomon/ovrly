import copy
import json
from pathlib import Path
from typing import Any

import pytest
from test_voice_actions import PACKAGE, validate

FIXTURES = PACKAGE / "fixtures/jobs"


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")))
def test_job_fixtures(path: Path) -> None:
    validate.check_job_fixture(validate.Validator(), path)


@pytest.mark.parametrize(
    "change",
    [
        {"status": 202},
        {"operation": "unknown"},
        {"synthetic": False},
        {"extra": "field"},
        {"response": {"job_id": "bad-id", "cancellation": "effective"}},
        {"response": {"job_id": "00000000-0000-4000-8000-000000000075"}},
    ],
)
def test_incompatible_job_fixture_fails(tmp_path: Path, change: dict[str, Any]) -> None:
    fixture = validate.read_json(FIXTURES / "cancel-effective.json")
    fixture.update(change)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))
    with pytest.raises(validate.Invalid):
        validate.check_job_fixture(validate.Validator(), path)


@pytest.mark.parametrize("name", ["cancel-effective", "delete-complete"])
def test_missing_or_extra_response_fields_fail(name: str) -> None:
    fixture = validate.read_json(FIXTURES / f"{name}.json")
    validator = validate.Validator()
    schema = f"job-{fixture['operation']}-response.schema.json"
    for field in fixture["response"]:
        broken = copy.deepcopy(fixture["response"])
        del broken[field]
        with pytest.raises(validate.Invalid):
            validator.validate(broken, schema)
    with pytest.raises(validate.Invalid):
        validator.validate({**fixture["response"], "owner_id": "private"}, schema)


def test_false_or_nonboolean_access_revocation_fails() -> None:
    response = validate.read_json(FIXTURES / "delete-complete.json")["response"]
    for value in (False, 1, "true"):
        with pytest.raises(validate.Invalid):
            validate.Validator().validate(
                {**response, "access_revoked": value}, "job-delete-response.schema.json"
            )
