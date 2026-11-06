from __future__ import annotations

import tarfile
import traceback
from pathlib import Path
from threading import Barrier, Thread

import pytest
from click.testing import CliRunner

from octop.cli.commands.backup import backup
from octop.config import load_config
from octop.infra.backup import system_archive
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.utils.paths import PathLayout


@pytest.mark.parametrize("parent_traversal", [False, True])
def test_repeated_export_inside_knowledge_does_not_archive_prior_output(
    tmp_path: Path, monkeypatch, parent_traversal: bool
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("OCTOP_HOME", str(home))
    monkeypatch.setenv("MAITU_HOME", str(home))
    for key in ("OCTOP_DATABASE_URL", "OCTOP_DATABASE_DRIVER", "OCTOP_DATABASE_SQLITE_PATH"):
        monkeypatch.delenv(key, raising=False)
    paths = PathLayout(home)
    source = paths.knowledge_dir / "kb" / "notes.txt"
    source.parent.mkdir(parents=True)
    payload = b"preserve the original document"
    source.write_bytes(payload)
    destination = source.parent / "export.tar.gz"
    output = (
        source.parent / ".." / source.parent.name / destination.name
        if parent_traversal
        else destination
    )
    runner = CliRunner()

    for _ in range(2):
        result = runner.invoke(backup, ["create", "--home", str(home), "--output", str(output)])
        assert result.exit_code == 0, result.output
        with tarfile.open(destination, "r:gz") as archive:
            names = archive.getnames()
            assert "knowledge/kb/export.tar.gz" not in names
            member = archive.extractfile("knowledge/kb/notes.txt")
            assert member is not None
            assert member.read() == payload

    assert source.read_bytes() == payload
    assert not any((home / "tmp").iterdir())


def test_overlapping_exports_to_same_destination_both_finish_with_valid_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("OCTOP_HOME", str(home))
    monkeypatch.setenv("MAITU_HOME", str(home))
    for key in ("OCTOP_DATABASE_URL", "OCTOP_DATABASE_DRIVER", "OCTOP_DATABASE_SQLITE_PATH"):
        monkeypatch.delenv(key, raising=False)
    paths = PathLayout(home)
    load_config(paths.config)
    pool = SqlitePool(paths.db)
    try:
        run_migrations(pool)
    finally:
        pool.close()
    original = paths.knowledge_dir / "kb" / "notes.txt"
    original.parent.mkdir(parents=True)
    payload = b"original bytes survive concurrent exports"
    original.write_bytes(payload)
    destination = original.parent / "parallel.tar.gz"
    barrier = Barrier(2)
    add_directory = system_archive._add_dir

    def overlapping_add(archive, source, archive_root, **kwargs):
        if archive_root == "knowledge":
            barrier.wait(timeout=10)
        return add_directory(archive, source, archive_root, **kwargs)

    monkeypatch.setattr(system_archive, "_add_dir", overlapping_add)
    failures: list[BaseException] = []

    def export() -> None:
        try:
            backup.main(
                args=["create", "--home", str(home), "--output", str(destination)],
                standalone_mode=False,
            )
        except BaseException as exc:
            failures.append(exc)

    workers = [Thread(target=export) for _ in range(2)]
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join(30)
    finally:
        barrier.abort()
        for worker in workers:
            worker.join(5)

    assert all(not worker.is_alive() for worker in workers)
    assert failures == [], "\n".join(
        "".join(traceback.format_exception(error)) for error in failures
    )
    with tarfile.open(destination, "r:gz") as archive:
        member = archive.extractfile("knowledge/kb/notes.txt")
        assert member is not None
        assert member.read() == payload
        assert "knowledge/kb/parallel.tar.gz" not in archive.getnames()
    assert original.read_bytes() == payload
    assert not any((home / "tmp").iterdir())
