import copy
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.api.text_schemas import TextBatch  # noqa: E402

import device_text  # noqa: E402
import validate  # noqa: E402


def test_upload_text_schema_and_synthetic_producer_fixture() -> None:
    committed = json.loads(device_text.SCHEMA.read_text())
    assert committed == device_text.schema()
    fixture = json.loads((ROOT / "fixtures/device-text/synthetic.json").read_text())
    assert fixture["synthetic"] is True
    body = fixture["batch"]
    validate.Validator().validate(body, "device-text.schema.json")
    parsed = TextBatch.model_validate_json(json.dumps(body))
    assert parsed.model_dump(mode="json") == body


@pytest.mark.parametrize("box", [[1, 2, 3], [1, 2, 3, 4, 5]])
def test_box_array_bounds_are_checked_by_both_contract_and_server(box: list[int]) -> None:
    fixture = json.loads((ROOT / "fixtures/device-text/synthetic.json").read_text())
    body = copy.deepcopy(fixture["batch"])
    body["frames"][0]["text_observations"][0]["box"] = box
    with pytest.raises(validate.Invalid):
        validate.Validator().validate(body, "device-text.schema.json")
    with pytest.raises(ValidationError):
        TextBatch.model_validate_json(json.dumps(body))
