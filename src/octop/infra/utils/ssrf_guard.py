"""Outbound HTTPS URL validation — mitigates SSRF (CWE-918)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import typing
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream, NetworkStream
from httpcore._backends.sync import SyncBackend


class UnsafeOutboundUrl(ValueError):
    """Raised when a URL must not be fetched server-side."""


class OutboundFetchError(RuntimeError):
    """Raised when an allowed URL still could not be fetched.

    The URL itself passed the guard; the response did not arrive or was not
    usable — a non-2xx status, too many redirects, the size ceiling, or a
    transport failure.  Callers report it instead of storing what they got.
    """


@dataclass(frozen=True)
class GuardedResponse:
    """Body and metadata of a fully validated outbound GET."""

    content: bytes
    content_type: str
    final_url: str


def _parse_https_host(url: str) -> tuple[str, int | None]:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeOutboundUrl("only https URLs are allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeOutboundUrl("missing hostname")
    return host.lower().rstrip("."), parsed.port


def _check_ip_not_private(ip_str: str) -> None:
    addr = ipaddress.ip_address(ip_str)
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
    ):
        raise UnsafeOutboundUrl("private or reserved IP addresses are not allowed")


def _check_ip_literal(host: str) -> None:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    _check_ip_not_private(host)


def _check_resolved_ip(ip_str: str) -> None:
    _check_ip_not_private(ip_str)


def issuer_base_domain(issuer: str) -> str:
    host = (urlparse(issuer).hostname or "").lower()
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def host_allowed_for_issuer(host: str, issuer: str) -> bool:
    normalized = host.lower().rstrip(".")
    issuer_host = (urlparse(issuer).hostname or "").lower()
    base = issuer_base_domain(issuer)
    if normalized in {issuer_host, base}:
        return True
    return normalized.endswith(f".{base}")


def validate_https_url(url: str, *, field: str = "url") -> str:
    """Reject non-https URLs and literal private/reserved IPs."""
    host, _ = _parse_https_host(url)
    if host == "localhost":
        raise UnsafeOutboundUrl(f"{field}: localhost is not allowed")
    _check_ip_literal(host)
    return url


async def validate_https_url_resolved(url: str, *, field: str = "url") -> str:
    """Also resolve DNS and reject private/reserved addresses."""
    validate_https_url(url, field=field)
    await _resolve_validated_ip(url)
    return url


async def _resolve_validated_ip(url: str) -> str:
    """Resolve ``url`` and return one validated (public) IP.

    Raises :class:`UnsafeOutboundUrl` if the host cannot be resolved or any
    resolved address is private/reserved.  The caller should pin the returned
    IP for the actual connection to defeat DNS-rebinding (TOCTOU).
    """
    host, port = _parse_https_host(url)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host,
            port or 443,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise UnsafeOutboundUrl(f"cannot resolve hostname {host!r}") from exc
    return _validated_ip(host, infos)


def _resolve_validated_ip_sync(url: str) -> str:
    """The blocking twin of :func:`_resolve_validated_ip`."""
    host, port = _parse_https_host(url)
    try:
        infos = socket.getaddrinfo(
            host,
            port or 443,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise UnsafeOutboundUrl(f"cannot resolve hostname {host!r}") from exc
    return _validated_ip(host, infos)


def _validated_ip(host: str, infos: typing.Sequence[typing.Any]) -> str:
    """Validate every resolved address and return the first (both resolvers)."""
    if not infos:
        raise UnsafeOutboundUrl(f"cannot resolve hostname {host!r}")
    for info in infos:
        _check_resolved_ip(info[4][0])
    return str(infos[0][4][0])


class _PinnedNetworkBackend(AutoBackend):
    """Resolve the validated host to a fixed IP, while preserving SNI.

    Only requests whose host matches ``_target_host`` are pinned to
    ``_pin_ip``; everything else (e.g. redirects) resolves normally so the
    helper never breaks legitimate cross-host redirects.  The original
    hostname is passed to TLS via httpcore's SNI logic, so certificate
    validation is unaffected by the IP pinning.
    """

    def __init__(self, target_host: str, pin_ip: str) -> None:
        super().__init__()
        self._target_host = target_host
        self._pin_ip = pin_ip

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        if host == self._target_host:
            return await super().connect_tcp(
                self._pin_ip,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )
        return await super().connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PinnedIPTransport(httpx.AsyncHTTPTransport):
    """httpx transport that pins the validated IP for ``target_host``.

    Swaps the underlying httpcore network backend so the validated host always
    connects to the validated IP — closing the DNS-rebinding window between
    validation and the actual TCP connection (CWE-918).
    """

    def __init__(self, target_host: str, pin_ip: str) -> None:
        super().__init__()
        self._pool._network_backend = _PinnedNetworkBackend(target_host, pin_ip)


class _PinnedSyncNetworkBackend(SyncBackend):
    """The blocking twin of :class:`_PinnedNetworkBackend` (same contract)."""

    def __init__(self, target_host: str, pin_ip: str) -> None:
        super().__init__()
        self._target_host = target_host
        self._pin_ip = pin_ip

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> NetworkStream:
        if host == self._target_host:
            return super().connect_tcp(
                self._pin_ip,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )
        return super().connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PinnedIPSyncTransport(httpx.HTTPTransport):
    """Blocking twin of :class:`PinnedIPTransport` for worker-thread callers."""

    def __init__(self, target_host: str, pin_ip: str) -> None:
        super().__init__()
        self._pool._network_backend = _PinnedSyncNetworkBackend(target_host, pin_ip)


async def safe_request(
    method: str,
    url: str,
    *,
    json: typing.Any | None = None,
    data: typing.Any | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> httpx.Response:
    """Validate, resolve, pin the IP, and perform an outbound HTTPS request.

    URL scheme/host must be https and resolve to a public IP (see
    :func:`validate_https_url_resolved`).  The connection is then pinned to the
    validated IP so a malicious DNS change between validation and connection
    cannot redirect the request to an internal address.
    """
    host, _port = _parse_https_host(url)
    pin_ip = await _resolve_validated_ip(url)
    transport = PinnedIPTransport(host, pin_ip)
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        return await client.request(method, url, json=json, data=data, headers=headers)


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def safe_get(
    url: str,
    *,
    max_bytes: int,
    max_redirects: int = 5,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
) -> GuardedResponse:
    """GET *url* through the guard, capped at *max_bytes* and *max_redirects*.

    Every hop is re-validated and re-pinned — a public URL must not be able to
    bounce the fetch onto an internal address.  The body is streamed and the
    transfer is dropped as soon as it passes *max_bytes*, so an oversized or
    endless response cannot exhaust memory:

    - :class:`UnsafeOutboundUrl` — a hop is not a public https target.
    - :class:`OutboundFetchError` — the target was allowed and the fetch still
      failed (status, redirects, size, transport).
    """
    target = url
    for hop in range(max_redirects + 1):
        try:
            host, _port = _parse_https_host(target)
            pin_ip = _resolve_validated_ip_sync(target)
        except UnsafeOutboundUrl as exc:
            if hop == 0:
                raise
            raise UnsafeOutboundUrl(f"redirect to {target!r} refused: {exc}") from exc
        transport = PinnedIPSyncTransport(host, pin_ip)
        try:
            with (
                httpx.Client(
                    transport=transport, timeout=timeout, follow_redirects=False
                ) as client,
                client.stream("GET", target, headers=headers) as response,
            ):
                if response.status_code in _REDIRECT_STATUSES:
                    location = (response.headers.get("location") or "").strip()
                    if not location:
                        raise OutboundFetchError(
                            f"{target} answered HTTP {response.status_code} without a location"
                        )
                    target = urljoin(target, location)
                    continue
                if response.status_code != 200:
                    raise OutboundFetchError(
                        f"HTTP {response.status_code} {response.reason_phrase} from {target}"
                    )
                return GuardedResponse(
                    content=_read_capped(response, target=target, max_bytes=max_bytes),
                    content_type=response.headers.get("content-type", ""),
                    final_url=str(response.url),
                )
        except httpx.HTTPError as exc:
            raise OutboundFetchError(f"request to {target} failed: {exc}") from exc
    raise OutboundFetchError(
        f"{url} redirected more than {max_redirects} times (last hop {target})"
    )


def _read_capped(response: httpx.Response, *, target: str, max_bytes: int) -> bytes:
    """The body, abandoned as soon as it passes *max_bytes*.

    The declared content length is only a hint (it can lie or be absent), so
    the stream is capped as it arrives: an endless response cannot exhaust
    memory.
    """
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise OutboundFetchError(_too_large(target, max_bytes))
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise OutboundFetchError(_too_large(target, max_bytes))
    return bytes(body)


def _too_large(target: str, max_bytes: int) -> str:
    return f"{target} is larger than the {max_bytes}-byte ingest limit"
