from __future__ import annotations

import json
import sqlite3
from contextlib import suppress
from pathlib import Path

from click.testing import CliRunner

from octop.cli.main import cli
from octop.infra.storage import storage_report
from octop.infra.utils.paths import PathLayout


def test_storage_report_is_read_only_and_does_not_create_missing_home(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    report = storage_report(PathLayout(root))
    assert not root.exists()
    assert report["errors"]
    assert report["disk"] is not None


def test_storage_report_counts_nested_files_once_and_skips_symlink(tmp_path: Path) -> None:
    root = tmp_path / "home"
    (root / "knowledge" / "kb" / "previews").mkdir(parents=True)
    (root / "knowledge" / "kb" / "source.pdf").write_bytes(b"source")
    (root / "knowledge" / "kb" / "previews" / "cached.png").write_bytes(b"preview")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.bin").write_bytes(b"not counted")
    with suppress(OSError, NotImplementedError):
        (root / "external").symlink_to(outside, target_is_directory=True)
    report = storage_report(PathLayout(root))
    assert report["categories"]["knowledge"] == {"bytes": 6, "files": 1}
    assert report["categories"]["previews"] == {"bytes": 7, "files": 1}
    assert report["categories"]["other"]["files"] == 0


def test_storage_report_classifies_only_platform_cache_roots(tmp_path: Path) -> None:
    root = tmp_path / "home"
    fixtures = {
        "tmp/task-x/output.bin": b"tmp",
        "agents/.execute-tmp/task-x/output.bin": b"exec",
        "workspaces/user/tmp/notes.bin": b"workspace",
        "agents/a/tmp/notes.bin": b"agent",
        "agents/a/.execute-tmp/user-owned.bin": b"nested",
        "knowledge/kb/previews/cached.png": b"preview",
        "knowledge/kb/docs/previews/original.png": b"durable",
        "embedding_models/weights.onnx": b"weights",
        "knowledge/kb/index.sqlite": b"index",
        "agents/a/.maitu/memory.sqlite-wal": b"wal",
    }
    for relative, content in fixtures.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    report = storage_report(PathLayout(root))
    categories = report["categories"]
    assert categories["temp"] == {"bytes": 7, "files": 2}
    assert categories["previews"] == {"bytes": 7, "files": 1}
    assert categories["knowledge"] == {"bytes": 7, "files": 1}
    assert categories["workspaces"] == {"bytes": 20, "files": 3}
    assert categories["models"] == {"bytes": 7, "files": 1}
    assert categories["databases"] == {"bytes": 8, "files": 2}


def test_storage_report_does_not_open_live_sqlite_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OCTOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("OCTOP_DATABASE_DRIVER", raising=False)
    monkeypatch.delenv("OCTOP_DATABASE_SQLITE_PATH", raising=False)
    root = tmp_path / "home"
    root.mkdir()
    db = root / "maitu.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE records (value TEXT)")
    connection.execute("INSERT INTO records VALUES ('preserved')")
    connection.commit()
    connection.close()
    before = db.read_bytes()
    report = storage_report(PathLayout(root))
    assert report["categories"]["databases"]["files"] == 1
    assert report["sqlite_page_metrics"]["available"] is False
    assert db.read_bytes() == before
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "preserved"
    finally:
        connection.close()


def test_storage_report_marks_external_database_without_disclosing_url(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OCTOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("OCTOP_DATABASE_DRIVER", raising=False)
    monkeypatch.delenv("OCTOP_DATABASE_SQLITE_PATH", raising=False)
    root = tmp_path / "home"
    root.mkdir()
    (root / "config.json").write_text(
        json.dumps({"database": {"driver": "sqlite"}}),
        encoding="utf-8",
    )
    (root / "env").write_text(
        "OCTOP_DATABASE_URL=postgresql://private-user:secret-token@db.example/private\n",
        encoding="utf-8",
    )
    report = storage_report(PathLayout(root))
    assert report["database_scope"]["kind"] == "external"
    assert "private-user" not in json.dumps(report)
    assert "secret-token" not in json.dumps(report)


def test_storage_status_localized_and_json_stable(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "home"
    root.mkdir()
    runner = CliRunner()
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    human = runner.invoke(cli, ["storage", "status", "--home", str(root)])
    assert human.exit_code == 0
    assert "存储清单" in human.output
    first = runner.invoke(cli, ["storage", "status", "--home", str(root), "--json"])
    second = runner.invoke(cli, ["storage", "status", "--home", str(root), "--json"])
    assert first.exit_code == second.exit_code == 0
    first_report = json.loads(first.output)
    second_report = json.loads(second.output)
    assert first_report["root"] == str(root)
    assert first_report["categories"] == second_report["categories"]
    assert first_report["categories"]["other"] == {"bytes": 0, "files": 0}
    assert list(root.iterdir()) == []


def test_storage_cli_does_not_run_legacy_migration(tmp_path: Path, monkeypatch) -> None:
    from octop.infra.utils.legacy_names import LEGACY_LOG_FILENAME

    root = tmp_path / "home"
    root.mkdir()
    legacy_log = root / LEGACY_LOG_FILENAME
    legacy_log.write_text("legacy", encoding="utf-8")
    monkeypatch.setenv("MAITU_HOME", str(root))
    result = CliRunner().invoke(cli, ["storage", "status", "--json"])
    assert result.exit_code == 0
    assert legacy_log.read_text(encoding="utf-8") == "legacy"
    assert not (root / "logs").exists()
    assert not (root / "config.json").exists()


def test_storage_maintenance_prunes_only_rebuildable_preview_cache(
    tmp_path: Path, monkeypatch
) -> None:
    from octop.infra.knowledge import cad_preview
    from octop.infra.storage import maintain_storage

    root = tmp_path / "home"
    monkeypatch.setenv("MAITU_HOME", str(root))
    source = root / "knowledge" / "kb" / "source.dwg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original")
    manual_backup = root / "backups" / "manual.tar"
    manual_backup.parent.mkdir(parents=True)
    manual_backup.write_bytes(b"backup")
    cached_preview = cad_preview.preview_png_path("kb", "document", "a" * 64)
    cached_preview.write_bytes(b"cache")
    real_prune = cad_preview.prune_preview_cache
    monkeypatch.setattr(
        cad_preview,
        "prune_preview_cache",
        lambda *, home: real_prune(home=home, max_age_seconds=0),
    )
    maintain_storage(PathLayout(root))
    assert source.read_bytes() == b"original"
    assert manual_backup.read_bytes() == b"backup"
    assert not cached_preview.exists()
