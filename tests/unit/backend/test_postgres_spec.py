"""Regression tests for storage rows and inline PostgreSQL backend specs."""

from __future__ import annotations

import json

from octop.infra.backend.adapter import row_to_backend_spec
from octop.infra.backend.postgres_spec import normalize_postgres_spec, split_host_port
from octop.infra.backend.resolver import resolve_agent_backend_spec
from octop.infra.db.repos.backends import BackendRow


def _row(**overrides: object) -> BackendRow:
    values: dict[str, object] = {
        "id": 1,
        "name": "db",
        "kind": "postgres",
        "endpoint": "db.example.test:5544",
        "access_key": "octop",
        "secret_key": "p@ss/#word",
        "bucket": "files",
        "region": "tenant_data",
        "config_json": None,
        "note": None,
        "enabled": 1,
        "created_at": 0,
        "updated_at": 0,
    }
    values.update(overrides)
    return BackendRow(**values)  # type: ignore[arg-type]


def test_postgres_row_maps_to_split_backend_config() -> None:
    spec = row_to_backend_spec(_row())
    assert spec == {
        "type": "postgres",
        "host": "db.example.test",
        "port": 5544,
        "user": "octop",
        "password": "p@ss/#word",
        "database": "files",
        "schema": "tenant_data",
    }
    assert "connection_string" not in spec


def test_postgres_row_accepts_uri_without_corrupting_encoded_credentials() -> None:
    row = _row(
        endpoint=None,
        access_key=None,
        secret_key=None,
        bucket=None,
        region=None,
        config_json=json.dumps(
            {
                "connection_string": "postgresql://u:p%40ss%2F%23@db.test:5433/catalog?sslmode=require"
            }
        ),
    )
    assert row_to_backend_spec(row) == {
        "type": "postgres",
        "host": "db.test",
        "port": 5433,
        "user": "u",
        "password": "p@ss/#",
        "database": "catalog",
        "sslmode": "require",
    }


def test_inline_postgres_spec_drops_uri_unknown_fields_and_preserves_explicit_fields() -> None:
    spec = resolve_agent_backend_spec(
        {
            "type": "postgres",
            "dsn": "postgresql://u:secret@db.test:5432/catalog",
            "password": "override",
            "pool_size": 8,
        },
        repo=None,
    )
    assert spec == {
        "type": "postgres",
        "host": "db.test",
        "port": 5432,
        "user": "u",
        "password": "override",
        "database": "catalog",
    }
    assert normalize_postgres_spec({"type": "postgres", "host": "db", "connection_string": ""}) == {
        "type": "postgres",
        "host": "db",
    }


def test_split_host_port_handles_ipv6_and_bare_ipv6() -> None:
    assert split_host_port("[2001:db8::1]:6432") == ("2001:db8::1", 6432)
    assert split_host_port("2001:db8::1") == ("2001:db8::1", None)
