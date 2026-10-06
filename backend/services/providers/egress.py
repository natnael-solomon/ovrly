"""Outbound request guard against server-side request forgery (REPO-06, #28).

Every evidence-provider request leaves through :class:`GuardedTransport`. Its network
backend resolves the host once, refuses the connection when **any** resolved address is
private, loopback, link-local (including the cloud metadata address 169.254.169.254),
shared, reserved, multicast, an IPv6 unique-local or site-local address, or an IPv6 form
that embeds such an IPv4 address (mapped, NAT64, 6to4, Teredo, compatible), and then
connects to exactly the address it checked. Checking and connecting therefore use one
resolution, so a DNS answer that changes between the two (rebinding) cannot reach an
internal host. TLS still verifies the certificate against the original host name.

:func:`fetch` is the only way to follow a redirect: it validates every URL (scheme
``http``/``https``, no credentials, default ports, no internal names or literal internal
addresses, including legacy numeric IPv4 forms), refuses an HTTPS to HTTP downgrade,
re-checks each hop and stops after :data:`MAX_REDIRECTS`. Bodies are bounded by
:func:`services.providers.http.send`. A refusal raises :class:`UnsafeUrl`, a
:class:`ProviderError` with a fixed ``reason`` code; neither the URL nor an address is
logged or put in the message.
"""

import asyncio
import ipaddress
import logging
import socket
import typing
from collections.abc import Awaitable, Callable, Iterable
from typing import Final

import httpcore
import httpx

from services.providers.http import MAX_BODY_BYTES, ProviderError, send

logger = logging.getLogger(__name__)

MAX_REDIRECTS: Final = 3
REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
ALLOWED_SCHEMES: Final = frozenset({"http", "https"})
ALLOWED_PORTS: Final = frozenset({None, 80, 443})
BLOCKED_NAMES: Final = frozenset({"localhost", "metadata", "metadata.google.internal"})
BLOCKED_SUFFIXES: Final = (".localhost", ".internal", ".local", ".home.arpa", ".lan")
# Ranges that ``ipaddress`` still reports as global but that must never be reached.
_EXTRA_BLOCKED: Final = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)
_NAT64: Final = ipaddress.ip_network("64:ff9b::/96")

Reason = typing.Literal[
    "invalid_url",
    "scheme_not_allowed",
    "credentials_in_url",
    "port_not_allowed",
    "internal_name",
    "blocked_address",
    "unresolvable",
    "scheme_downgrade",
    "too_many_redirects",
    "unix_socket",
]
REASONS: Final[frozenset[str]] = frozenset(typing.get_args(Reason))
Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]


class UnsafeUrl(ProviderError):
    """The destination is not a public http(s) address; ``reason`` is a fixed code."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(f"unsafe destination: {reason}")
        self.reason: Reason = reason


def blocked_address(address: Address) -> bool:
    """True for every address that is not a public unicast address."""
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped
        if embedded is None and address in _NAT64:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if embedded is None and address.sixtofour is not None:
            embedded = address.sixtofour
        if address.teredo is not None or int(address) >> 32 == 0:
            # Teredo tunnels and IPv4-compatible (::a.b.c.d, ::1, ::) are never public.
            return True
        if embedded is not None:
            return blocked_address(embedded)
        if address.is_site_local:
            return True
    return (
        not address.is_global
        or address.is_multicast
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or any(address in network for network in _EXTRA_BLOCKED)
    )


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Numeric hosts that resolvers read as IPv4 (``2130706433``, ``0x7f.1``, ``0177.0.0.1``)."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values: list[int] = []
    for part in parts:
        try:
            if part.lower().startswith("0x"):
                values.append(int(part[2:] or "0", 16))
            elif len(part) > 1 and part.startswith("0"):
                values.append(int(part, 8))
            else:
                values.append(int(part, 10))
        except ValueError:
            return None
    *head, last = values
    if any(value > 255 for value in head) or last >= 256 ** (5 - len(values)):
        return None
    number = 0
    for value in head:
        number = number << 8 | value
    number = number << 8 * (5 - len(values)) | last
    return ipaddress.IPv4Address(number)


def literal_address(host: str) -> Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return _legacy_ipv4(host)


def check_host(host: str) -> None:
    name = host.rstrip(".").lower()
    if not name:
        raise UnsafeUrl("invalid_url")
    literal = literal_address(name)
    if literal is not None:
        if blocked_address(literal):
            raise UnsafeUrl("blocked_address")
        return
    if name in BLOCKED_NAMES or name.endswith(BLOCKED_SUFFIXES) or "." not in name:
        raise UnsafeUrl("internal_name")


def check_url(url: str | httpx.URL) -> httpx.URL:
    """Validate one URL before any request; raise :class:`UnsafeUrl` otherwise."""
    try:
        parsed = url if isinstance(url, httpx.URL) else httpx.URL(url)
    except (httpx.InvalidURL, TypeError):
        raise UnsafeUrl("invalid_url") from None
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrl("scheme_not_allowed")
    if parsed.userinfo:
        raise UnsafeUrl("credentials_in_url")
    if parsed.port not in ALLOWED_PORTS:
        raise UnsafeUrl("port_not_allowed")
    check_host(parsed.host)
    return parsed


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def pinned_address(host: str, port: int, resolve: Resolver) -> str:
    """Resolve once, refuse if any answer is internal, and return the address to dial."""
    check_host(host)
    literal = literal_address(host.rstrip("."))
    if literal is not None:
        return str(literal)
    try:
        answers = await resolve(host, port)
    except OSError:
        raise UnsafeUrl("unresolvable") from None
    addresses: list[Address] = []
    for answer in answers:
        try:
            addresses.append(ipaddress.ip_address(answer.split("%", 1)[0]))
        except ValueError:
            raise UnsafeUrl("unresolvable") from None
    if not addresses:
        raise UnsafeUrl("unresolvable")
    if any(blocked_address(address) for address in addresses):
        raise UnsafeUrl("blocked_address")
    return str(addresses[0])


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Dial only the address that passed the check; unix sockets are refused."""

    def __init__(
        self,
        resolve: Resolver = system_resolve,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self.resolve = resolve
        self.inner = inner if inner is not None else httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend signature
        local_address: str | None = None,
        socket_options: Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        lookup = pinned_address(host, port, self.resolve)
        address = await (asyncio.wait_for(lookup, timeout) if timeout else lookup)
        return await self.inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend signature
        socket_options: Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise UnsafeUrl("unix_socket")

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """``httpx`` transport whose every connection goes through :class:`GuardedBackend`.

    It never uses an environment proxy, which would dial the target on our behalf
    without the address check.
    """

    def __init__(
        self,
        resolve: Resolver = system_resolve,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=GuardedBackend(resolve, inner),
        )


async def fetch(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    *,
    params: dict[str, str | int] | None = None,
    max_redirects: int = MAX_REDIRECTS,
    max_bytes: int = MAX_BODY_BYTES,
) -> tuple[httpx.Response, bytes]:
    """GET ``url`` following at most ``max_redirects`` checked redirects."""
    try:
        current = check_url(url)
        if params:
            current = current.copy_merge_params(params)
        for hop in range(max_redirects + 1):
            response, body = await send(client, provider, "GET", str(current), max_bytes=max_bytes)
            location = response.headers.get("location")
            if response.status_code not in REDIRECT_STATUSES or not location:
                return response, body
            if hop == max_redirects:
                raise UnsafeUrl("too_many_redirects")
            try:
                target = current.join(location)
            except httpx.InvalidURL:  # pragma: no cover - httpx rejects such a Location first
                raise UnsafeUrl("invalid_url") from None
            current_scheme = current.scheme
            current = check_url(target)
            if current_scheme == "https" and current.scheme == "http":
                raise UnsafeUrl("scheme_downgrade")
    except UnsafeUrl as exc:
        logger.warning("Provider %s request blocked (%s)", provider, exc.reason)
        raise
    raise AssertionError("unreachable")  # pragma: no cover
