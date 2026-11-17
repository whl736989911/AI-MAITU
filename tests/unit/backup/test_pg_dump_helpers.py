from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.infra.backup import pg_dump
from octop.infra.errors import ErrorCode, OctopError


def test_restore_postgres_reports_ignored_sql_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(pg_dump, "_require_tool", lambda _name: "pg_restore")
    monkeypatch.setattr(
        pg_dump.subprocess,
        "run",
        lambda _command, **_kwargs: SimpleNamespace(
            returncode=1,
            stderr="pg_restore: warning: errors ignored on restore: 1",
            stdout="",
        ),
    )
    with pytest.raises(OctopError) as error:
        pg_dump.restore_postgres("postgresql://example", tmp_path / "archive.dump")
    assert error.value.code is ErrorCode.INTERNAL_ERROR
