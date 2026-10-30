"""Resolve the externally visible HTTP origin for an incoming request."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from starlette.requests import Request


def _first_header_value(value: str | None) -> str | None:
    if value is None:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def _with_forwarded_port(host: str, scheme: str, forwarded_port: str | None) -> str:
    if not forwarded_port or not forwarded_port.isascii() or not forwarded_port.isdecimal():
        return host
    try:
        port = int(forwarded_port)
        if urlsplit(f"//{host}").port is not None or host.endswith(":"):
            return host
    except ValueError:
        return host
    if not 1 <= port <= 65535:
        return host
    if (scheme, port) in {("http", 80), ("https", 443)}:
        return host
    return f"{host}:{port}"


def _safe_prefix(value: str | None) -> str:
    """Accept only a simple absolute URL path supplied by the trusted proxy."""
    if not value:
        return ""
    raw = value.split(",", 1)[0].strip()
    if raw in {"", "/"}:
        return ""
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or "%" in raw:
        return ""
    parts = raw.strip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    if not re.fullmatch(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*/?", raw):
        return ""
    return "/" + "/".join(parts)


def resolve_public_prefix(request: Request) -> str:
    """Return the validated external mount prefix from a trusted proxy or ASGI."""
    forwarded = _safe_prefix(request.headers.get("x-forwarded-prefix"))
    root_path = _safe_prefix(str(request.scope.get("root_path") or ""))
    return forwarded or root_path


def prefix_public_path(prefix: str, path: str) -> str:
    """Add a mount prefix once to an app-owned absolute path."""
    if not prefix or not path.startswith("/") or path.startswith("//"):
        return path
    if path == prefix or path.startswith(f"{prefix}/"):
        return path
    return f"{prefix}/{path.lstrip('/')}"


def resolve_public_base(request: Request) -> str:
    """Resolve the externally visible origin and optional stripped mount prefix.

    Trusts forwarded scheme, host, port and prefix headers from a trusted reverse
    proxy; otherwise uses the ASGI root_path for the mount prefix. An explicit
    port in the host wins, and a default HTTP/HTTPS port is omitted. The root
    deployment remains origin-only.
    """
    prefix = resolve_public_prefix(request)
    forwarded_proto = _first_header_value(request.headers.get("x-forwarded-proto"))
    forwarded_host = _first_header_value(request.headers.get("x-forwarded-host"))
    if forwarded_proto:
        host = forwarded_host or request.headers.get("host")
        if host:
            forwarded_port = _first_header_value(request.headers.get("x-forwarded-port"))
            host = _with_forwarded_port(host, forwarded_proto, forwarded_port)
            return f"{forwarded_proto}://{host}{prefix}".rstrip("/")

    origin = str(request.url.replace(path="", query="", fragment="")).rstrip("/")
    return f"{origin}{prefix}".rstrip("/")
