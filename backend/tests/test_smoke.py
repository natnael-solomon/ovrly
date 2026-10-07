"""Post-deploy smoke against an in-memory fake of the deployed API (no network, no DB)."""

import hashlib
import json
import uuid

import httpx
import pytest

from services import smoke

ERROR_ACTION = {401: "authenticate", 404: "none", 400: "fix_request"}


def error(status, code):
    return httpx.Response(
        status,
        json={
            "code": code,
            "message": "safe message",
            "retryable": False,
            "action": ERROR_ACTION.get(status, "none"),
            "request_id": uuid.uuid4().hex,
        },
    )


class FakeApi:
    """Just enough of the deployed API to exercise every smoke step."""

    def __init__(self):
        self.sleeping = 0
        self.health_status = 200
        self.heartbeat = 2.0
        self.job_state_sequence = ["queued", "running", "published"]
        self.report = None
        self.tokens = {}
        self.uploads = {}
        self.investigations = {}
        self.keys = {}
        self.deleted = set()
        self.seen_bodies = []

    def owner(self, request):
        header = request.headers.get("Authorization", "")
        return self.tokens.get(header.removeprefix("Bearer "))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if request.content:
            self.seen_bodies.append(request.content)
        if path == "/healthz":
            if self.sleeping:
                self.sleeping -= 1
                return httpx.Response(503, text="<html>waking</html>")
            if self.health_status != 200:
                return httpx.Response(
                    self.health_status, json={"status": "unavailable", "reason": "migrations"}
                )
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "checks": {
                        "database": "ok",
                        "migrations": "ok",
                        "storage": "ok",
                        "worker": "ok",
                    },
                    "signals": {
                        "queue_depth": 0,
                        "oldest_queued_seconds": None,
                        "worker_heartbeat_seconds": self.heartbeat,
                        "scholarxiv": "configured",
                    },
                },
            )
        if path == "/v1/principals/guest" and method == "POST":
            token = "ovk_" + uuid.uuid4().hex
            principal = uuid.uuid4()
            self.tokens[token] = principal
            return httpx.Response(
                201,
                json={
                    "principal_id": str(principal),
                    "kind": "guest",
                    "credential": {"token": token, "token_type": "bearer"},
                },
            )
        owner = self.owner(request)
        if owner is None:
            if path.startswith("/v1/investigations"):
                response = error(401, "AUTHENTICATION_REQUIRED")
                if "X-Request-Id" in request.headers:
                    response.headers["X-Request-Id"] = request.headers["X-Request-Id"]
                return response
            return error(404, "NOT_FOUND")
        if path == "/v1/uploads" and method == "POST":
            body = json.loads(request.content)
            upload = str(uuid.uuid4())
            self.uploads[upload] = {"declared": body, "bytes": None}
            return httpx.Response(
                201, json={"id": upload, "target": f"/v1/uploads/{upload}/content"}
            )
        if path.startswith("/v1/uploads/") and path.endswith("/content") and method == "PUT":
            self.uploads[path.split("/")[3]]["bytes"] = request.content
            return httpx.Response(204)
        if path.startswith("/v1/uploads/") and path.endswith("/complete"):
            upload = self.uploads[path.split("/")[3]]
            ok = hashlib.sha256(upload["bytes"]).hexdigest() == upload["declared"]["sha256"]
            return httpx.Response(200, json={"state": "completed" if ok else "pending"})
        if path == "/v1/investigations" and method == "POST":
            key = request.headers.get("Idempotency-Key")
            if key is None:
                return error(400, "IDEMPOTENCY_KEY_REQUIRED")
            if (owner, key) not in self.keys:
                investigation = str(uuid.uuid4())
                self.investigations[investigation] = {
                    "owner": owner,
                    "job": str(uuid.uuid4()),
                    "polls": 0,
                }
                self.keys[(owner, key)] = investigation
            return httpx.Response(202, json={"id": self.keys[(owner, key)]})
        if path.startswith("/v1/investigations/"):
            record = self.investigations.get(path.split("/")[3])
            if record is None or record["owner"] != owner:
                return error(404, "NOT_FOUND")
            states = self.job_state_sequence
            state = states[min(record["polls"], len(states) - 1)]
            record["polls"] += 1
            return httpx.Response(
                200,
                json={
                    "id": path.split("/")[3],
                    "coverage": {"status": "not_started"},
                    "processing_status": "waiting",
                    "job": {"id": record["job"], "state": state, "stage": "intake"},
                    "report": self.report,
                },
            )
        if path.startswith("/v1/jobs/"):
            job = path.split("/")[3]
            if method == "DELETE":
                self.deleted.add(job)
                return httpx.Response(200, json={"job_id": job, "state": "deleted"})
            if job in self.deleted:
                return error(404, "NOT_FOUND")
            return httpx.Response(200, json={"cancellation": "effective"})
        return error(404, "NOT_FOUND")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run(api, **options):
    clock = Clock()
    lines = []
    with httpx.Client(transport=httpx.MockTransport(api), base_url="https://example.test") as c:
        runner = smoke.Smoke(c, clock=clock, sleep=clock.sleep, emit=lines.append, **options)
        report = runner.run()
    return report, lines, runner


def failed(report):
    return [step.name for step in report.steps if not step.ok]


def test_full_round_trip_passes_and_prints_nothing_secret():
    api = FakeApi()
    api.sleeping = 3
    report, lines, runner = run(api)
    assert report.passed, lines
    assert [step.name for step in report.steps] == [
        "wake",
        "health",
        "typed-errors",
        "guest",
        "upload",
        "intake",
        "poll",
        "isolation",
        "delete",
        "redaction",
    ]
    assert report.wake_seconds == 9
    assert report.signals["worker_heartbeat_seconds"] == 2.0
    printed = "\n".join(lines) + json.dumps(report.as_json())
    for token in api.tokens:
        assert token not in printed
    assert "ovk_" not in printed and runner.canary not in printed
    assert any(runner.canary.encode() in body for body in api.seen_bodies)


def test_cold_start_beyond_the_bound_fails():
    api = FakeApi()
    api.sleeping = 100
    report, _, _ = run(api, wake_seconds=30)
    assert failed(report) == ["wake"]
    assert report.steps[-1].name == "redaction" and report.steps[-1].ok


def test_application_503_is_not_mistaken_for_waking():
    api = FakeApi()
    api.health_status = 503
    report, lines, _ = run(api)
    assert failed(report) == ["health"]
    assert any("readiness HTTP 503 migrations" in line for line in lines)


def test_old_heartbeat_with_server_ok_passes_and_is_recorded():
    api = FakeApi()
    api.heartbeat = 590.0
    report, _, _ = run(api)
    assert report.passed
    assert report.signals["worker_heartbeat_seconds"] == 590.0


def test_explicit_heartbeat_bound_is_enforced():
    api = FakeApi()
    api.heartbeat = 121.0
    report, _, _ = run(api, heartbeat_seconds=120)
    assert failed(report) == ["health"]


def test_missing_heartbeat_age_fails():
    api = FakeApi()
    api.heartbeat = None
    report, _, _ = run(api)
    assert failed(report) == ["health"]


def test_intake_that_never_finishes_fails_within_the_bound():
    api = FakeApi()
    api.job_state_sequence = ["queued"]
    report, lines, _ = run(api, poll_seconds=10)
    assert failed(report) == ["poll"]
    assert any("no terminal state within 10s" in line for line in lines)


def test_failed_intake_fails():
    api = FakeApi()
    api.job_state_sequence = ["failed"]
    report, _, _ = run(api)
    assert failed(report) == ["poll"]


def test_fixture_report_is_rejected():
    api = FakeApi()
    api.report = {"version": 1, "fixture": True, "claims": [], "evidence": [], "assessments": []}
    report, lines, _ = run(api)
    assert failed(report) == ["poll"]
    assert any("development fixture" in line for line in lines)


def test_report_citations_are_checked():
    good = {
        "version": 2,
        "fixture": False,
        "claims": [{"id": "c1"}],
        "evidence": [{"id": "e1", "claim_id": "c1"}],
        "assessments": [{"claim_id": "c1", "relations": [{"evidence_id": "e1"}]}],
    }
    assert smoke.check_report(good) == "report v2 with 1 claims"
    with pytest.raises(smoke.SmokeFailure):
        smoke.check_report({**good, "evidence": [{"id": "e1", "claim_id": "c9"}]})
    with pytest.raises(smoke.SmokeFailure):
        smoke.check_report(
            {**good, "assessments": [{"claim_id": "c1", "relations": [{"evidence_id": "x"}]}]}
        )


def test_untyped_errors_fail():
    with pytest.raises(smoke.SmokeFailure, match="does not match the schema"):
        smoke.error_code(httpx.Response(404, json={"detail": "Not Found"}))
    with pytest.raises(smoke.SmokeFailure, match="without a JSON"):
        smoke.error_code(httpx.Response(500, text="oops"))
    with pytest.raises(smoke.SmokeFailure, match="not typed"):
        smoke.error_code(
            httpx.Response(
                404,
                json={
                    "code": "not_found",
                    "message": "m",
                    "retryable": False,
                    "action": "none",
                    "request_id": "r",
                },
            )
        )


def test_redaction_failure_is_reported():
    api = FakeApi()
    report, lines, runner = run(api)
    runner.emit("leak ovk_example")
    runner.check_redaction()
    assert runner.report.steps[-1].name == "redaction" and not runner.report.steps[-1].ok
    assert not runner.report.passed


def test_transport_error_mid_run_is_a_typed_failure():
    def broken(request):
        if request.url.path == "/healthz":
            return FakeApi()(request)
        raise httpx.ConnectError("refused")

    report, lines, _ = run(broken)
    assert failed(report) == ["typed-errors"]
    assert any("transport error (ConnectError)" in line for line in lines)


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https://user:pw@example.com",
        "https://example.com/?q=1",
        "https://example.com/#x",
        "ftp://example.com",
        "https://",
    ],
)
def test_invalid_base_urls(value):
    with pytest.raises(ValueError):
        smoke.validate_base_url(value)


def test_valid_base_urls():
    assert smoke.validate_base_url("https://api.example.com/") == "https://api.example.com"
    assert smoke.validate_base_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_synthetic_wav_is_valid_riff():
    data = smoke.synthetic_wav("canary-x")
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert int.from_bytes(data[4:8], "little") == len(data) - 8
    assert b"canary-x" in data


def test_main_rejects_bad_arguments(capsys):
    assert smoke.main(["--base-url", "http://example.com"]) == 2
    assert smoke.main([]) == 2
    assert "https" in capsys.readouterr().err


def test_main_writes_the_report(tmp_path, monkeypatch):
    api = FakeApi()
    real_client = httpx.Client

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(api), **kwargs)

    monkeypatch.setattr(smoke.httpx, "Client", client)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    path = tmp_path / "out" / "smoke.json"
    assert smoke.main(["--base-url", "https://example.test", "--report", str(path)]) == 0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["passed"] is True
    assert "ovk_" not in path.read_text(encoding="utf-8")
