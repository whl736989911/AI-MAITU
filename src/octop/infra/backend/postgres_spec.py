"""Translate storage rows and inline specs to deepagents-backends fields."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from octop.infra.db.repos.backends import BackendRow

POSTGRES_SPEC_FIELDS = frozenset(
    {
        "host",
        "port",
        "database",
        "user",
        "password",
        "table",
        "schema",
        "min_pool_size",
        "max_pool_size",
        "max_idle_seconds",
        "connection_timeout",
        "sslmode",
    }
)
_URI_KEYS = ("connection_string", "dsn")


def row_to_postgres_spec(row: BackendRow, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Build a split-field PostgresConfig spec from a storage backend row."""
    spec: dict[str, Any] = {"type": "postgres"}
    uri = _first_uri(cfg)
    if uri:
        spec.update(parse_postgres_conninfo(uri))
    spec.update({key: value for key, value in cfg.items() if key in POSTGRES_SPEC_FIELDS})

    if row.endpoint:
        host, port = split_host_port(row.endpoint)
        if host:
            spec["host"] = host
        if port is not None:
            spec["port"] = port
    if row.access_key:
        spec["user"] = row.access_key
    if row.secret_key:
        spec["password"] = row.secret_key
    if row.bucket:
        spec["database"] = row.bucket
    if row.region:
        spec["schema"] = row.region

    if not spec.get("host"):
        return None
    if not all(spec.get(key) for key in ("user", "password", "database")) and not uri:
        return None
    return spec


def normalize_postgres_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalize an inline postgres URI or config to supported split fields."""
    out: dict[str, Any] = {"type": "postgres"}
    uri = _first_uri(spec)
    if uri:
        out.update(parse_postgres_conninfo(uri))
    out.update({key: value for key, value in spec.items() if key in POSTGRES_SPEC_FIELDS})
    return out


def _first_uri(spec: dict[str, Any]) -> str | None:
    for key in _URI_KEYS:
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def split_host_port(endpoint: str) -> tuple[str, int | None]:
    """Split host:port and bracketed IPv6 endpoints without mangling bare IPv6."""
    text = endpoint.strip()
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return text, None
        host, suffix = text[1:end], text[end + 1 :]
        return (
            (host, int(suffix[1:]))
            if suffix.startswith(":") and suffix[1:].isdigit()
            else (host, None)
        )
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            return host, int(port)
    return text, None


def parse_postgres_conninfo(value: str) -> dict[str, Any]:
    """Parse PostgreSQL URI or libpq keyword conninfo without exposing secrets."""
    text = value.strip()
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        return _parse_uri(text)
    try:
        return _map_libpq_dict(conninfo_to_dict(text))
    except Exception:
        raise ValueError("invalid postgres connection_string") from None


def _parse_uri(text: str) -> dict[str, Any]:
    parsed = urlparse(text)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("invalid postgres connection_string")
    raw: dict[str, Any] = {}
    if parsed.hostname:
        raw["host"] = parsed.hostname
    if parsed.port is not None:
        raw["port"] = parsed.port
    if parsed.username is not None:
        raw["user"] = unquote(parsed.username)
    if parsed.password is not None:
        raw["password"] = unquote(parsed.password)
    if path := parsed.path.lstrip("/"):
        raw["dbname"] = unquote(path)
    if sslmodes := parse_qs(parsed.query).get("sslmode"):
        raw["sslmode"] = sslmodes[0]
    return _map_libpq_dict(raw)


def _map_libpq_dict(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if raw.get("host"):
        out["host"] = str(raw["host"])
    if (port := raw.get("port")) is not None and port != "":
        try:
            out["port"] = int(port)
        except (TypeError, ValueError):
            raise ValueError("invalid postgres connection_string") from None
    if database := raw.get("dbname") or raw.get("database"):
        out["database"] = str(database)
    if raw.get("user"):
        out["user"] = str(raw["user"])
    if raw.get("password") is not None:
        out["password"] = str(raw["password"])
    if raw.get("sslmode"):
        out["sslmode"] = str(raw["sslmode"])
    return out
