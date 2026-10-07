"""SSRF guard for outbound fetches (REPO-06, #28): every test here is a negative case.

No test resolves a real name or opens a socket: the resolver is scripted and the network
backend is httpcore's in-memory mock, recording which address would have been dialled.
"""

import asyncio
import ipaddress
import logging
from types import SimpleNamespace

import httpcore
import httpx
import pytest

from services.evidence.stages import EvidenceStages
from services.providers import egress
from services.providers.egress import (
    GuardedBackend,
    GuardedTransport,
    UnsafeUrl,
    blocked_address,
    check_url,
    fetch,
    literal_address,
    pinned_address,
)
from services.providers.fulltext import FullTextClient
from services.providers.http import ProviderError, send
from services.settings import Settings

PUBLIC = "93.184.216.34"
OK = [
    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n"
    b"Connection: close\r\n\r\n",
    b"ok",
]

REJECTED = [
    # Non-http(s) schemes.
    ("file:///etc/passwd", "scheme_not_allowed"),
    ("ftp://arxiv.org/pub", "scheme_not_allowed"),
    ("gopher://arxiv.org:70/_GET", "scheme_not_allowed"),
    ("dict://arxiv.org:11211/stat", "scheme_not_allowed"),
    ("ws://arxiv.org/socket", "scheme_not_allowed"),
    ("data:text/html,hello", "scheme_not_allowed"),
    ("javascript:alert(1)", "scheme_not_allowed"),
    ("//arxiv.org/html/1", "scheme_not_allowed"),
    ("https://user:secret@arxiv.org/html/1", "credentials_in_url"),
    ("https://arxiv.org:8443/html/1", "port_not_allowed"),
    ("http://arxiv.org:22/", "port_not_allowed"),
    # Internal names.
    ("http://localhost/", "internal_name"),
    ("http://LOCALHOST./", "internal_name"),
    ("http://api.localhost/", "internal_name"),
    ("http://metadata/computeMetadata/v1/", "internal_name"),
    ("http://metadata.google.internal/computeMetadata/v1/", "internal_name"),
    ("http://db.internal/", "internal_name"),
    ("http://printer.local/", "internal_name"),
    ("http://intranet/", "internal_name"),
    # Loopback, private, shared, link-local and metadata IPv4.
    ("http://127.0.0.1/", "blocked_address"),
    ("http://127.255.255.254/", "blocked_address"),
    ("http://10.0.0.5/", "blocked_address"),
    ("http://172.16.0.1/", "blocked_address"),
    ("http://172.31.255.255/", "blocked_address"),
    ("http://192.168.1.1/", "blocked_address"),
    ("http://100.64.0.1/", "blocked_address"),
    ("http://169.254.169.254/latest/meta-data/", "blocked_address"),
    ("http://169.254.170.2/v2/credentials", "blocked_address"),
    ("http://0.0.0.0/", "blocked_address"),
    ("http://192.0.0.192/", "blocked_address"),
    ("http://198.18.0.1/", "blocked_address"),
    ("http://224.0.0.1/", "blocked_address"),
    ("http://255.255.255.255/", "blocked_address"),
    ("http://240.0.0.1/", "blocked_address"),
    # Legacy numeric IPv4 spellings resolvers accept.
    ("http://2130706433/", "blocked_address"),
    ("http://0x7f000001/", "blocked_address"),
    ("http://0x7f.1/", "blocked_address"),
    ("http://0177.0.0.1/", "invalid_url"),
    ("http://127.1/", "blocked_address"),
    ("http://169.254.43518/", "blocked_address"),
    # IPv6 loopback, unique-local, link-local, site-local and embedded IPv4.
    ("http://[::1]/", "blocked_address"),
    ("http://[::]/", "blocked_address"),
    ("http://[fc00::1]/", "blocked_address"),
    ("http://[fd00:ec2::254]/latest/meta-data/", "blocked_address"),
    ("http://[fe80::1]/", "blocked_address"),
    ("http://[fec0::1]/", "blocked_address"),
    ("http://[ff02::1]/", "blocked_address"),
    ("http://[::ffff:127.0.0.1]/", "blocked_address"),
    ("http://[::ffff:169.254.169.254]/", "blocked_address"),
    ("http://[::ffff:a9fe:a9fe]/", "blocked_address"),
    ("http://[::127.0.0.1]/", "blocked_address"),
    ("http://[64:ff9b::a9fe:a9fe]/", "blocked_address"),
    ("http://[64:ff9b:1::a00:1]/", "blocked_address"),
    ("http://[2002:7f00:1::]/", "blocked_address"),
    ("http://[2002:a9fe:a9fe::]/", "blocked_address"),
    ("http://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/", "blocked_address"),
    ("http://[2001:db8::1]/", "blocked_address"),
    ("http:///nohost", "invalid_url"),
]


@pytest.mark.parametrize(("url", "reason"), REJECTED, ids=[url for url, _ in REJECTED])
def test_unsafe_urls_are_rejected_with_a_typed_reason(url, reason):
    with pytest.raises(UnsafeUrl) as raised:
        check_url(url)
    assert raised.value.reason == reason
    assert isinstance(raised.value, ProviderError)
    # The message is a fixed code: neither the URL nor an address leaks into logs or results.
    assert str(raised.value) == f"unsafe destination: {reason}"


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/html/2601.00001v2",
        "http://www.ebi.ac.uk/europepmc/webservices/rest/search",
        "https://ARXIV.org./html/1",
        f"https://{PUBLIC}/",
        "https://[2606:4700:4700::1111]/",
        "https://[64:ff9b::5db8:d822]/",
        "https://[2002:5db8:d822::]/",
        "https://[::ffff:93.184.216.34]/",
        "https://arxiv.org:443/html/1",
    ],
)
def test_public_http_urls_pass(url):
    assert check_url(url).host


def test_invalid_url_objects_and_hosts_are_rejected():
    with pytest.raises(UnsafeUrl, match="invalid_url"):
        check_url("http://[::1/")
    with pytest.raises(UnsafeUrl, match="invalid_url"):
        egress.check_host(".")
    assert literal_address("999.1.1.1") is None
    assert literal_address("1.2.3.4.5") is None
    assert literal_address("0x") == ipaddress.IPv4Address(0)
    # httpx refuses this spelling, but a resolver would read it as octal loopback.
    assert literal_address("0177.0.0.1") == ipaddress.IPv4Address("127.0.0.1")
    assert literal_address("1.2.3.4.example") is None
    assert literal_address("1.300.1.1") is None


def test_blocked_address_covers_every_internal_range():
    for public in ("8.8.8.8", PUBLIC, "2606:4700:4700::1111", "2a00:1450::1"):
        assert not blocked_address(ipaddress.ip_address(public))
    for internal in ("127.0.0.1", "169.254.169.254", "fd00:ec2::254", "::ffff:10.0.0.1"):
        assert blocked_address(ipaddress.ip_address(internal))


def resolver(*answers):
    """A scripted DNS: each call returns the next answer list (or raises it)."""
    calls = []
    queue = list(answers)

    async def resolve(host, port):
        calls.append((host, port))
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    resolve.calls = calls
    return resolve


async def test_resolution_refuses_any_internal_answer():
    assert await pinned_address("arxiv.org", 443, resolver([PUBLIC])) == PUBLIC
    assert await pinned_address("arxiv.org", 443, resolver(["2606:4700::1%0"])) == "2606:4700::1"
    for answers, reason in [
        ([PUBLIC, "10.0.0.1"], "blocked_address"),
        (["169.254.169.254"], "blocked_address"),
        (["::ffff:127.0.0.1"], "blocked_address"),
        (["fd12:3456::1"], "blocked_address"),
        ([], "unresolvable"),
        (["not-an-address"], "unresolvable"),
        (OSError("NXDOMAIN"), "unresolvable"),
    ]:
        with pytest.raises(UnsafeUrl) as raised:
            await pinned_address("arxiv.org", 443, resolver(answers))
        assert raised.value.reason == reason
    # Literal hosts are never sent to DNS.
    literal = resolver([PUBLIC])
    assert await pinned_address(PUBLIC, 80, literal) == PUBLIC
    assert literal.calls == []
    with pytest.raises(UnsafeUrl, match="internal_name"):
        await pinned_address("localhost", 80, literal)


class RecordingBackend(httpcore.AsyncMockBackend):
    def __init__(self, buffer):
        super().__init__(buffer)
        self.dialled = []
        self.sni = []

    async def connect_tcp(
        self,
        host,
        port,
        timeout=None,  # noqa: ASYNC109 - httpcore backend signature
        local_address=None,
        socket_options=None,
    ):
        self.dialled.append((host, port))
        stream = await super().connect_tcp(host, port)
        backend = self

        class Tls(type(stream)):
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):  # noqa: ASYNC109
                backend.sni.append(server_hostname)
                return self

        stream.__class__ = Tls
        return stream


async def test_dns_rebinding_cannot_reach_an_internal_address():
    """The first lookup answers public, the next one 127.0.0.1 (a rebinding DNS server).

    The guard checks and dials one resolution per connection, so the first request goes to
    the checked public address and the rebound one is refused before any connection.
    """
    resolve = resolver([PUBLIC], ["127.0.0.1"])
    backend = RecordingBackend(OK)
    async with httpx.AsyncClient(transport=GuardedTransport(resolve, backend)) as client:
        first = await client.get("https://rebind.example.com/paper")
        assert first.status_code == 200 and first.text == "ok"
        with pytest.raises(UnsafeUrl) as raised:
            await client.get("https://rebind.example.com/paper")
    assert raised.value.reason == "blocked_address"
    assert resolve.calls == [("rebind.example.com", 443), ("rebind.example.com", 443)]
    # Only the checked address was ever dialled, and TLS still verified the host name.
    assert backend.dialled == [(PUBLIC, 443)]
    assert backend.sni == ["rebind.example.com"]


async def test_guarded_transport_refuses_internal_targets_without_dialling():
    backend = RecordingBackend(OK)
    transport = GuardedTransport(resolver(["10.1.2.3"]), backend)
    async with httpx.AsyncClient(transport=transport) as client:
        for url in ("http://intranet.example.com/", "http://169.254.169.254/", "http://localhost/"):
            with pytest.raises(UnsafeUrl):
                await client.get(url)
    assert backend.dialled == []


async def test_guarded_backend_delegates_and_refuses_unix_sockets():
    inner = RecordingBackend(OK)
    backend = GuardedBackend(resolver([PUBLIC]), inner)
    stream = await backend.connect_tcp("arxiv.org", 443, timeout=5)
    await stream.aclose()
    assert inner.dialled == [(PUBLIC, 443)]
    await backend.sleep(0)
    with pytest.raises(UnsafeUrl, match="unix_socket"):
        await backend.connect_unix_socket("/var/run/docker.sock")

    async def slow(host, port):
        await asyncio.sleep(10)
        return [PUBLIC]

    with pytest.raises(httpcore.ConnectTimeout):
        await GuardedBackend(slow, inner).connect_tcp("arxiv.org", 443, timeout=0.01)
    assert isinstance(GuardedBackend().inner, httpcore.AnyIOBackend)


async def test_slow_resolution_is_a_provider_error_not_a_stage_abort():
    async def slow(host, port):
        await asyncio.sleep(10)
        return [PUBLIC]

    transport = GuardedTransport(slow, RecordingBackend(OK))
    async with httpx.AsyncClient(transport=transport, timeout=0.01) as client:
        with pytest.raises(ProviderError, match="transport"):
            await send(client, "arxiv", "GET", "https://arxiv.org/html/1")
        assert await FullTextClient(client).arxiv("2601.00001v2") is None


async def test_system_resolver_reads_getaddrinfo(monkeypatch):
    async def fake(host, port, type):  # noqa: A002 - mirrors the loop signature
        return [(2, 1, 6, "", (PUBLIC, port)), (10, 1, 6, "", ("2606:4700::1", port, 0, 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake)
    assert await egress.system_resolve("arxiv.org", 443) == [PUBLIC, "2606:4700::1"]


def redirector(chain, final=b"<p>ok</p>"):
    """MockTransport answering each path in ``chain`` with a redirect to its target."""
    seen = []

    def handle(request):
        seen.append(str(request.url))
        target = chain.get(request.url.path)
        if target is not None:
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, content=final)

    return httpx.MockTransport(handle), seen


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("http://169.254.169.254/latest/meta-data/iam/", "blocked_address"),
        ("http://[fd00:ec2::254]/latest/", "blocked_address"),
        ("http://127.0.0.1:8000/v1/principals/guest", "port_not_allowed"),
        ("http://localhost/admin", "internal_name"),
        ("http://10.0.0.7/", "blocked_address"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("gopher://arxiv.org/", "scheme_not_allowed"),
        ("http://arxiv.org/html/downgraded", "scheme_downgrade"),
        ("https://user:pw@arxiv.org/", "credentials_in_url"),
    ],
)
async def test_redirects_to_internal_or_unsafe_targets_are_refused(location, reason, caplog):
    transport, seen = redirector({"/html/1": location})
    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UnsafeUrl) as raised:
            await fetch(client, "arxiv", "https://arxiv.org/html/1")
    assert raised.value.reason == reason
    # The unsafe hop was never requested, and nothing about it was logged.
    assert seen == ["https://arxiv.org/html/1"]
    assert location not in caplog.text and "169.254" not in caplog.text


async def test_safe_redirects_are_followed_and_capped():
    malformed, seen = redirector({"/m": "https://[::1"})
    async with httpx.AsyncClient(transport=malformed) as client:
        with pytest.raises(ProviderError):
            await fetch(client, "arxiv", "https://arxiv.org/m")
    assert seen == ["https://arxiv.org/m"]
    chain = {"/a": "/b", "/b": "https://export.arxiv.org/c", "/c": "/d"}
    transport, seen = redirector(chain)
    async with httpx.AsyncClient(transport=transport) as client:
        response, body = await fetch(client, "arxiv", "https://arxiv.org/a")
        assert (response.status_code, body) == (200, b"<p>ok</p>")
        assert seen[-1] == "https://export.arxiv.org/d"
        loop, _ = redirector({"/x": "/x"})
    async with httpx.AsyncClient(transport=loop) as client:
        with pytest.raises(UnsafeUrl, match="too_many_redirects"):
            await fetch(client, "arxiv", "https://arxiv.org/x")
        with pytest.raises(UnsafeUrl, match="too_many_redirects"):
            await fetch(client, "arxiv", "https://arxiv.org/x", max_redirects=0)
    # A redirect without a Location is a plain response; params are merged into the URL.
    bare = httpx.MockTransport(lambda request: httpx.Response(302))
    async with httpx.AsyncClient(transport=bare) as client:
        response, _ = await fetch(client, "arxiv", "https://arxiv.org/", params={"q": "x y"})
        assert response.status_code == 302
        assert response.request.url.params["q"] == "x y"


async def test_response_size_is_capped_by_declaration_and_by_bytes():
    declared = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-length": "999999999"})
    )
    async with httpx.AsyncClient(transport=declared) as client:
        with pytest.raises(ProviderError, match="too large"):
            await fetch(client, "arxiv", "https://arxiv.org/html/1")

    async def endless():
        while True:
            yield b"x" * 1024

    streamed = httpx.MockTransport(lambda request: httpx.Response(200, content=endless()))
    async with httpx.AsyncClient(transport=streamed) as client:
        with pytest.raises(ProviderError, match="too large"):
            await send(client, "arxiv", "GET", "https://arxiv.org/html/1", max_bytes=4096)


async def test_full_text_never_follows_a_redirect_into_the_network():
    transport, seen = redirector(
        {
            "/html/2601.00001v2": "http://169.254.169.254/latest/meta-data/",
            "/europepmc/webservices/rest/search": "http://10.0.0.1/",
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        full_text = FullTextClient(client)
        assert await full_text.arxiv("2601.00001v2") is None
        assert await full_text.europepmc("10.5555/x") is None
    assert all("169.254" not in url and "10.0.0.1" not in url for url in seen)


def test_production_evidence_client_uses_the_guard():
    settings = Settings(database_url="postgresql+psycopg://u:p@127.0.0.1/x", _env_file=None)
    client = EvidenceStages(SimpleNamespace(), settings)._client()
    assert isinstance(client._transport, GuardedTransport)
    assert client.follow_redirects is False and client._trust_env is False
