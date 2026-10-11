"""An interrupted first enrollment must not regenerate an existing center identity."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from scripts.deployment.center import _needs_owner
from scripts.deployment.common import DeploymentError


def test_interrupted_enrollment_can_resume_without_reinitializing_existing_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "center.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE admin_users(id INTEGER); CREATE TABLE signing_keys(kid TEXT);"
        )
    assert _needs_owner(tmp_path) is True
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO admin_users VALUES(1)")
        connection.execute("INSERT INTO signing_keys VALUES('original-signing-identity')")
    assert _needs_owner(tmp_path) is False


def test_lost_administrator_with_existing_signing_identity_requires_recovery(
    tmp_path: Path,
) -> None:
    with sqlite3.connect(tmp_path / "center.sqlite3") as connection:
        connection.executescript(
            "CREATE TABLE admin_users(id INTEGER); CREATE TABLE signing_keys(kid TEXT);"
        )
        connection.execute("INSERT INTO signing_keys VALUES('original-signing-identity')")
    with pytest.raises(DeploymentError, match="已有签发身份"):
        _needs_owner(tmp_path)


def test_orphaned_keys_cannot_create_a_second_identity(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    keys.mkdir()
    original = keys / "original.pem"
    original.write_text("preserve original key material", encoding="utf-8")
    with pytest.raises(DeploymentError, match="数据库缺失"):
        _needs_owner(tmp_path)
    assert not (tmp_path / "center.sqlite3").exists()
    assert original.read_text(encoding="utf-8") == "preserve original key material"


def test_corrupt_database_is_not_replaced_with_a_new_center(tmp_path: Path) -> None:
    database = tmp_path / "center.sqlite3"
    original = b"invalid existing database"
    database.write_bytes(original)
    with pytest.raises(DeploymentError, match="损坏数据"):
        _needs_owner(tmp_path)
    assert database.read_bytes() == original
