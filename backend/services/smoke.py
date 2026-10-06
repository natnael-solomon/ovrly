"""Post-deploy smoke test for a deployed ovrly backend (BE-11, #21).

Run from ``backend/``::

    uv run --frozen python -m services.smoke --base-url https://<service host> \
        --report reports/smoke.json

Checks, in order: the first ``/healthz`` wakes a sleeping free-tier host within
``--wake-seconds``; readiness reports every check ``ok`` and an embedded worker heartbeat
of at most ``--heartbeat-seconds``; typed errors use the shared error shape; a guest
principal uploads a tiny synthetic WAV (one second of silence plus a random canary chunk,
no consent or rights question), records an investigation with an ``Idempotency-Key``
and replays it; the intake job reaches a terminal state within ``--poll-seconds``; a
second guest cannot read the investigation; deleting the job revokes access. Any report
present must not be a development fixture and must cite only evidence it contains.

The smoke never opens a Voxide session, never sends a provider key and never prints
credentials, response bodies, the canary or identifiers. Printed lines carry step
names, HTTP status codes, contract error codes and timings only, and are checked for
the bearer token, the canary and the ``ovk_``/``sxv_`` key prefixes before exit.
Exit status: 0 passed, 1 a check failed, 2 invalid arguments.
"""

import argparse
import hashlib
import json
import re
import secrets
import struct
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

SECRET_PREFIXES = ("ovk_", "sxv_")
ERROR_KEYS = {"code", "message", "retryable", "action", "request_id"}
ERROR_ACTIONS = {"none", "retry", "authenticate", "fix_request", "upload_again"}
ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]*")
TERMINAL_JOB_STATES = {"published", "failed", "cancelled", "deleted"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Host responses while a sleeping service starts; anything else is a real failure.
WAKING_STATUSES = {502, 503, 504}


class SmokeFailure(Exception):
    """A check failed; the message is safe to print (no bodies, tokens or identifiers)."""


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str


@dataclass
class Report:
    base_url: str
    steps: list[Step] = field(default_factory=list)
    wake_seconds: float | None = None
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.steps) and all(step.ok for step in self.steps)

    def as_json(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "passed": self.passed,
            "wake_seconds": self.wake_seconds,
            "signals": self.signals,
            "steps": [step.__dict__ for step in self.steps],
        }


def validate_base_url(value: str) -> str:
    """HTTPS origin, or plain HTTP only on loopback for a local run."""
    parts = urlsplit(value.strip())
    host = parts.hostname or ""
    if parts.username or parts.password or parts.query or parts.fragment or not host:
        raise ValueError("Base URL must be a bare origin without credentials, query or fragment")
    if parts.scheme != "https" and not (parts.scheme == "http" and host in LOOPBACK_HOSTS):
        raise ValueError("Base URL must use https (http is allowed only on loopback)")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def synthetic_wav(canary: str, seconds: float = 1.0, rate: int = 8000) -> bytes:
    """Mono 8-bit silence with the canary in a private RIFF chunk that players ignore."""
    samples = b"\x80" * int(seconds * rate)
    marker = canary.encode("ascii")
    if len(marker) % 2:
        marker += b"\x00"
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate, 1, 8)
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"ovrl"
        + struct.pack("<I", len(marker))
        + marker
        + b"data"
        + struct.pack("<I", len(samples))
        + samples
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def error_code(response: httpx.Response) -> str:
    """Validate the shared error shape and return its code."""
    try:
        payload = response.json()
    except ValueError:
        raise SmokeFailure(f"HTTP {response.status_code} without a JSON error body") from None
    if not isinstance(payload, dict) or set(payload) != ERROR_KEYS:
        raise SmokeFailure(f"HTTP {response.status_code} error body does not match the schema")
    code = payload["code"]
    if (
        not isinstance(code, str)
        or not ERROR_CODE.fullmatch(code)
        or not isinstance(payload["retryable"], bool)
        or payload["action"] not in ERROR_ACTIONS
        or not isinstance(payload["message"], str)
        or not isinstance(payload["request_id"], str)
    ):
        raise SmokeFailure(f"HTTP {response.status_code} error fields are not typed")
    return code


def expect_error(response: httpx.Response, status: int, code: str) -> None:
    if response.status_code != status:
        raise SmokeFailure(f"expected HTTP {status} {code}, got HTTP {response.status_code}")
    actual = error_code(response)
    if actual != code:
        raise SmokeFailure(f"expected {code}, got {actual}")


def expect_status(response: httpx.Response, status: int, what: str) -> Any:
    if response.status_code != status:
        detail = ""
        if response.status_code >= 400:
            try:
                detail = f" {error_code(response)}"
            except SmokeFailure:
                detail = ""
        raise SmokeFailure(
            f"{what}: expected HTTP {status}, got HTTP {response.status_code}{detail}"
        )
    if status == 204:
        return None
    try:
        return response.json()
    except ValueError:
        raise SmokeFailure(f"{what}: response is not JSON") from None


def check_report(report: dict[str, Any]) -> str:
    """A published report must be real and every relation must cite its own evidence."""
    if report.get("fixture"):
        raise SmokeFailure("report is a development fixture; OVRLY_STUB_REPORTS is on")
    claims = {claim["id"] for claim in report.get("claims", [])}
    evidence = {item["id"]: item["claim_id"] for item in report.get("evidence", [])}
    if any(claim_id not in claims for claim_id in evidence.values()):
        raise SmokeFailure("report evidence names a claim the version does not contain")
    for assessment in report.get("assessments", []):
        for relation in assessment.get("relations", []):
            if evidence.get(relation.get("evidence_id")) != assessment.get("claim_id"):
                raise SmokeFailure("report assessment cites evidence it does not contain")
    return f"report v{report.get('version')} with {len(claims)} claims"


class Smoke:
    def __init__(
        self,
        client: httpx.Client,
        *,
        wake_seconds: float = 180,
        poll_seconds: float = 120,
        heartbeat_seconds: float = 120,
        interval: float = 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        emit: Callable[[str], None] = print,
    ):
        self.client = client
        self.wake_seconds = wake_seconds
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.interval = interval
        self.clock = clock
        self.sleep = sleep
        self._emit = emit
        self.lines: list[str] = []
        self.canary = "ovrly-smoke-canary-" + secrets.token_hex(8)
        self.secrets: set[str] = {self.canary}
        self.report = Report(base_url=str(client.base_url))

    def emit(self, line: str) -> None:
        self.lines.append(line)
        self._emit(line)

    @staticmethod
    def _waking(response: httpx.Response) -> bool:
        """A host gateway answer while the service starts, not the API's own readiness."""
        if response.status_code not in WAKING_STATUSES:
            return False
        try:
            body = response.json()
        except ValueError:
            return True
        return not (isinstance(body, dict) and body.get("status") == "unavailable")

    @contextmanager
    def step(self, name: str) -> Iterator[list[str]]:
        started = self.clock()
        detail: list[str] = []
        try:
            yield detail
        except SmokeFailure as failure:
            self._record(name, False, started, str(failure))
            raise
        except httpx.HTTPError as error:
            self._record(name, False, started, f"transport error ({type(error).__name__})")
            raise SmokeFailure(name) from None
        except (KeyError, TypeError, AttributeError, ValueError):
            self._record(name, False, started, "unexpected response shape")
            raise SmokeFailure(name) from None
        self._record(name, True, started, "; ".join(detail))

    def _record(self, name: str, ok: bool, started: float, detail: str) -> None:
        seconds = round(self.clock() - started, 2)
        self.report.steps.append(Step(name, ok, seconds, detail))
        self.emit(f"smoke: {name} {'ok' if ok else 'FAILED'} {seconds:.2f}s {detail}".rstrip())

    def guest(self) -> dict[str, str]:
        body = expect_status(self.client.post("/v1/principals/guest", json={}), 201, "guest")
        token = body["credential"]["token"]
        if body.get("kind") != "guest" or not token.startswith("ovk_"):
            raise SmokeFailure("guest response is not a guest bearer credential")
        self.secrets.add(token)
        return {"Authorization": f"Bearer {token}"}

    def run(self) -> Report:
        try:
            self._run()
        except SmokeFailure:
            pass
        self.check_redaction()
        return self.report

    def _run(self) -> None:
        with self.step("wake") as detail:
            started = self.clock()
            while True:
                try:
                    response: httpx.Response | None = self.client.get("/healthz")
                except httpx.TransportError:
                    response = None
                if response is not None and not self._waking(response):
                    ready = response
                    break
                if self.clock() - started > self.wake_seconds:
                    raise SmokeFailure(f"no readiness answer within {self.wake_seconds:.0f}s")
                self.sleep(self.interval)
            self.report.wake_seconds = round(self.clock() - started, 2)
            detail.append(f"first answer after {self.report.wake_seconds:.2f}s")

        with self.step("health") as detail:
            if ready.status_code != 200:
                reason = ""
                try:
                    reason = str(ready.json().get("reason", ""))
                except (ValueError, AttributeError):
                    reason = ""
                raise SmokeFailure(f"readiness HTTP {ready.status_code} {reason}".rstrip())
            body = ready.json()
            checks = body.get("checks", {})
            if body.get("status") != "ok" or any(value != "ok" for value in checks.values()):
                raise SmokeFailure("readiness checks are not all ok (embedded worker required)")
            if set(checks) != {"database", "migrations", "storage", "worker"}:
                raise SmokeFailure(
                    "readiness does not report database, migrations, storage, worker"
                )
            signals = body.get("signals", {})
            self.report.signals = signals
            heartbeat = signals.get("worker_heartbeat_seconds")
            if not isinstance(heartbeat, int | float) or heartbeat > self.heartbeat_seconds:
                raise SmokeFailure(f"worker heartbeat older than {self.heartbeat_seconds:.0f}s")
            detail.append(
                f"heartbeat {heartbeat}s, queue {signals.get('queue_depth')}, "
                f"oldest {signals.get('oldest_queued_seconds')}s"
            )

        with self.step("typed-errors") as detail:
            request_id = "smoke-" + secrets.token_hex(6)
            missing = self.client.get("/v1/investigations", headers={"X-Request-Id": request_id})
            expect_error(missing, 401, "AUTHENTICATION_REQUIRED")
            if missing.headers.get("X-Request-Id") != request_id:
                raise SmokeFailure("X-Request-Id was not echoed")
            expect_error(self.client.get("/v1/smoke-unknown-route"), 404, "NOT_FOUND")
            detail.append("401 AUTHENTICATION_REQUIRED, 404 NOT_FOUND")

        with self.step("guest") as detail:
            owner = self.guest()
            detail.append("guest credential minted")

        content = synthetic_wav(self.canary)
        with self.step("upload") as detail:
            declared = expect_status(
                self.client.post(
                    "/v1/uploads",
                    json={
                        "size_bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "content_type": "audio/wav",
                    },
                    headers=owner,
                ),
                201,
                "declare upload",
            )
            target = declared["target"]
            if not target.startswith("/v1/uploads/"):
                raise SmokeFailure("upload target is not an API path")
            expect_status(
                self.client.put(target, content=content, headers=owner), 204, "upload bytes"
            )
            completed = expect_status(
                self.client.post(target.removesuffix("/content") + "/complete", headers=owner),
                200,
                "complete upload",
            )
            if completed.get("state") != "completed":
                raise SmokeFailure("upload did not complete")
            detail.append(f"{len(content)} synthetic bytes verified")

        with self.step("intake") as detail:
            body = {"source": {"kind": "upload", "upload_id": declared["id"], "duration_ms": 1000}}
            expect_error(
                self.client.post("/v1/investigations", json=body, headers=owner),
                400,
                "IDEMPOTENCY_KEY_REQUIRED",
            )
            keyed = {**owner, "Idempotency-Key": "smoke-" + uuid.uuid4().hex}
            first = expect_status(
                self.client.post("/v1/investigations", json=body, headers=keyed), 202, "intake"
            )
            replay = expect_status(
                self.client.post("/v1/investigations", json=body, headers=keyed), 202, "replay"
            )
            if replay.get("id") != first.get("id"):
                raise SmokeFailure("idempotent replay returned a different investigation")
            investigation = f"/v1/investigations/{first['id']}"
            detail.append("202 accepted, replay identical, missing key 400")

        with self.step("poll") as detail:
            deadline = self.clock() + self.poll_seconds
            while True:
                current = expect_status(self.client.get(investigation, headers=owner), 200, "poll")
                job = current.get("job") or {}
                status = current.get("processing_status")
                if job.get("state") in TERMINAL_JOB_STATES or status in {"partial", "complete"}:
                    break
                if self.clock() > deadline:
                    raise SmokeFailure(
                        f"no terminal state within {self.poll_seconds:.0f}s "
                        f"(job {job.get('state')}, {status})"
                    )
                self.sleep(self.interval)
            if job.get("state") != "published" and status not in {"partial", "complete"}:
                raise SmokeFailure(f"intake ended {job.get('state')} ({status})")
            if not isinstance(current.get("coverage"), dict):
                raise SmokeFailure("investigation has no coverage object")
            detail.append(f"job {job.get('state')} at stage {job.get('stage')}, {status}")
            if current.get("report") is not None:
                detail.append(check_report(current["report"]))
            job_id = job.get("id")

        with self.step("isolation") as detail:
            other = self.guest()
            expect_error(self.client.get(investigation, headers=other), 404, "NOT_FOUND")
            detail.append("another guest gets 404")

        with self.step("delete") as detail:
            if not job_id:
                raise SmokeFailure("investigation has no job to delete")
            deleted = expect_status(
                self.client.delete(f"/v1/jobs/{job_id}", headers=owner), 200, "delete"
            )
            if deleted.get("state") != "deleted":
                raise SmokeFailure("delete did not tombstone the job")
            expect_error(
                self.client.post(f"/v1/jobs/{job_id}/cancel", headers=owner), 404, "NOT_FOUND"
            )
            detail.append("job deleted, then 404")

    def check_redaction(self) -> None:
        """Fail if anything printed so far contains a credential, key prefix or the canary."""
        leaked = [
            line
            for line in self.lines
            if any(secret in line for secret in self.secrets)
            or any(prefix in line for prefix in SECRET_PREFIXES)
        ]
        started = self.clock()
        if leaked:
            self.report.steps.append(Step("redaction", False, 0.0, "secret text in output"))
            self._emit("smoke: redaction FAILED")
        else:
            self._record("redaction", True, started, f"{len(self.lines)} lines clean")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m services.smoke", description=__doc__)
    parser.add_argument("--base-url", required=True, help="Deployed origin, e.g. https://host")
    parser.add_argument("--wake-seconds", type=float, default=180, help="cold-start bound")
    parser.add_argument("--poll-seconds", type=float, default=120, help="terminal-state bound")
    parser.add_argument("--heartbeat-seconds", type=float, default=120)
    parser.add_argument("--report", type=Path, help="write the JSON result here")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        base_url = validate_base_url(args.base_url)
    except ValueError as error:
        print(f"smoke: {error}", file=sys.stderr)
        return 2
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    timeout = httpx.Timeout(30.0, connect=10.0)
    with httpx.Client(
        base_url=base_url,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": "ovrly-smoke/1"},
    ) as client:
        report = Smoke(
            client,
            wake_seconds=args.wake_seconds,
            poll_seconds=args.poll_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
        ).run()
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report.as_json(), indent=2) + "\n", encoding="utf-8")
    print(f"smoke: {'PASSED' if report.passed else 'FAILED'}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
