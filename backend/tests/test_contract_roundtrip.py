"""The committed result fixtures in packages/contracts are what the read models serialise.

``packages/contracts/roundtrip.py`` builds the six scenarios from ``services.api.schemas``
with ``model_dump(mode="json")`` and compares the rendered JSON byte for byte with the
committed files. This module runs that check in the backend suite (no database needed)
and proves the check itself notices drift.
"""

import json
import sys
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parents[2] / "packages" / "contracts"
sys.path.insert(0, str(CONTRACTS))

import roundtrip  # noqa: E402
import validate  # noqa: E402


def test_committed_result_fixtures_match_the_pydantic_models() -> None:
    problems = list(roundtrip.drift())
    assert problems == [], "\n".join(problems)


def test_cli_check_mode_passes_on_the_committed_fixtures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert roundtrip.main(["--check"]) == 0
    assert "6 result fixtures match" in capsys.readouterr().out


def test_every_scenario_is_a_result_fixture_the_validator_accepts(tmp_path: Path) -> None:
    validator = validate.Validator()
    assert set(roundtrip.SCENARIOS) == set(validate.RESULT_FIXTURES)
    for name in roundtrip.SCENARIOS:
        path = tmp_path / f"{name}.json"
        path.write_text(roundtrip.render(roundtrip.build(name)), encoding="ascii")
        validate.check_result_fixture(validator, path)


def test_update_then_check_is_stable(tmp_path: Path) -> None:
    written = roundtrip.update(tmp_path)
    assert {path.name for path in written} == {f"{n}.json" for n in roundtrip.SCENARIOS}
    assert list(roundtrip.drift(tmp_path)) == []
    for path in written:
        raw = path.read_bytes()
        raw.decode("ascii")
        assert b"\r" not in raw and raw.endswith(b"}\n")


def test_check_mode_reports_drift_and_missing_files(tmp_path: Path) -> None:
    roundtrip.update(tmp_path)
    drifted = tmp_path / "complete.json"
    fixture = json.loads(drifted.read_text(encoding="ascii"))
    fixture["investigation"]["report"]["assessments"][0]["overall"] = "challenged"
    drifted.write_bytes((json.dumps(fixture, indent=2) + "\n").encode("ascii"))
    (tmp_path / "no-claims.json").unlink()
    problems = list(roundtrip.drift(tmp_path))
    assert len(problems) == 2
    assert "complete.json" in problems[0]
    assert '"overall": "challenged"' in problems[0] and '"overall": "supported"' in problems[0]
    assert "no-claims.json" in problems[1] and "missing" in problems[1]


def test_cli_check_mode_fails_on_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roundtrip.update(tmp_path)
    (tmp_path / "failed.json").write_text("{}\n", encoding="ascii")
    monkeypatch.setattr(roundtrip, "RESULTS", tmp_path)
    assert roundtrip.main(["--check"]) == 1
    assert "failed.json" in capsys.readouterr().err


def test_cli_update_writes_into_the_results_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "fixtures" / "results"
    monkeypatch.setattr(roundtrip, "RESULTS", target)
    monkeypatch.setattr(roundtrip, "ROOT", tmp_path)
    assert roundtrip.main(["--update"]) == 0
    assert "wrote fixtures/results/complete.json" in capsys.readouterr().out
    assert sorted(p.name for p in target.iterdir()) == sorted(
        f"{n}.json" for n in roundtrip.SCENARIOS
    )


def test_cli_requires_exactly_one_mode() -> None:
    with pytest.raises(SystemExit):
        roundtrip.main([])
    with pytest.raises(SystemExit):
        roundtrip.main(["--check", "--update"])
