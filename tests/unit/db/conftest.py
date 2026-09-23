"""Shared fixtures for the migration tests in this directory."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from octop.infra.db import migrate as migrate_module
from octop.infra.db.pool import SqlitePool


@pytest.fixture
def upgrade_through(monkeypatch: pytest.MonkeyPatch) -> Callable[[SqlitePool, int], None]:
    """Run the migration chain up to a version, and no further.

    A test about one migration asserts the state that migration left, so the
    chain stops there — later versions are free to fold away what an earlier one
    wrote, and v35 does: it merges every legacy knowledge base into the
    enterprise space (design §13). A test that wants the whole chain runs
    ``run_migrations`` instead, which is what
    ``tests/unit/db/test_legacy_knowledge_base_merge.py`` does.

    Only the discovery is capped, so everything ``run_migrations`` does around
    the versions — the every-boot ensure helpers included — still runs.
    """

    def _run(pool: SqlitePool, version: int) -> None:
        discovered = migrate_module._discover
        monkeypatch.setattr(
            migrate_module,
            "_discover",
            lambda dialect: [item for item in discovered(dialect) if item[0] <= version],
        )
        migrate_module.run_migrations(pool)

    return _run
