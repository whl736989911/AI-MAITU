from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.knowledge import cad_preview


def _configure(monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path) -> tuple[Path, Path]:
    monkeypatch.setenv("MAITU_HOME", str(home))
    monkeypatch.delenv("OCTOP_HOME", raising=False)
    script = tmp_path / "dwg_preview.py"
    script.write_text("fake converter", encoding="utf-8")
    monkeypatch.setenv("OCTOP_DWG_PREVIEW_SCRIPT", str(script))
    original = tmp_path / "drawing.dwg"
    original.write_bytes(b"drawing")
    return script, original


def test_preview_cache_reuses_valid_content_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, original = _configure(monkeypatch, tmp_path / "home", tmp_path)
    calls = 0

    def convert(args, **_kwargs):
        nonlocal calls
        calls += 1
        Path(args[3], "preview.png").write_bytes(b"rendered png")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cad_preview.subprocess, "run", convert)
    args = {
        "kb_id": "kb",
        "doc_id": "doc",
        "original": original,
        "content_hash": "a" * 64,
        "filename": "drawing.dwg",
    }
    one = cad_preview.render_preview(**args)
    two = cad_preview.render_preview(**args)

    assert one == two
    assert one.read_bytes() == b"rendered png"
    assert calls == 1


@pytest.mark.parametrize("max_bytes,max_files", [(8, 100), (100, 2)])
def test_preview_pruner_enforces_quotas_age_and_preserves_sources(
    tmp_path: Path,
    max_bytes: int,
    max_files: int,
) -> None:
    home = tmp_path / "home"
    previews = home / "knowledge" / "kb" / "previews"
    docs = home / "knowledge" / "kb" / "docs"
    previews.mkdir(parents=True)
    docs.mkdir(parents=True)
    now = time.time()
    cache_files = []
    for index in range(3):
        path = previews / f"doc{index}-{'%064x' % (index + 1)}.png"
        path.write_bytes(bytes([index]) * 4)
        os.utime(path, (now - index, now - index))
        cache_files.append(path)
    stale = previews / f"old-{'f' * 64}.png"
    stale.write_bytes(b"old")
    os.utime(stale, (now - 1000, now - 1000))
    unknown = previews / "unknown-output.png"
    unknown.write_bytes(b"keep unknown")
    source = docs / "drawing.dwg"
    source.write_bytes(b"source")

    result = cad_preview.prune_preview_cache(
        home=home, max_bytes=max_bytes, max_files=max_files, max_age_seconds=100
    )

    assert result == {"deleted_files": 2, "deleted_bytes": 7}
    assert source.read_bytes() == b"source"
    assert unknown.read_bytes() == b"keep unknown"
    assert stale.exists() is False
    assert len([path for path in cache_files if path.exists()]) == 2
    assert sum(path.stat().st_size for path in cache_files if path.exists()) <= 8


def test_preview_pruner_skips_symlinked_preview_tree(tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / f"outside-{'a' * 64}.png"
    target.write_bytes(b"preserve")
    kb = home / "knowledge" / "kb"
    kb.mkdir(parents=True)
    try:
        (kb / "previews").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    cad_preview.prune_preview_cache(home=home, max_bytes=0, max_files=0, max_age_seconds=0)

    assert target.read_bytes() == b"preserve"


def test_preview_concurrent_requests_generate_once_and_snapshot_survives_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, original = _configure(monkeypatch, tmp_path / "home", tmp_path)
    calls = 0
    call_lock = threading.Lock()

    def convert(args, **_kwargs):
        nonlocal calls
        with call_lock:
            calls += 1
        time.sleep(0.03)
        Path(args[3], "preview.png").write_bytes(b"complete png")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cad_preview.subprocess, "run", convert)
    results: list[Path] = []

    def render(index: int) -> None:
        results.append(
            cad_preview.snapshot_preview(
                kb_id="kb",
                doc_id="doc",
                original=original,
                content_hash="b" * 64,
                filename="drawing.dwg",
                snapshot=tmp_path / f"snapshot-{index}.png",
            )
        )

    threads = [threading.Thread(target=render, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert len(results) == 6
    assert all(path.read_bytes() == b"complete png" for path in results)
    cad_preview.prune_preview_cache(
        home=tmp_path / "home", max_bytes=0, max_files=0, max_age_seconds=0
    )
    assert all(path.read_bytes() == b"complete png" for path in results)


def test_unknown_content_hash_does_not_reuse_cached_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, original = _configure(monkeypatch, tmp_path / "home", tmp_path)
    calls = 0

    def convert(args, **_kwargs):
        nonlocal calls
        calls += 1
        Path(args[3], "preview.png").write_bytes(str(calls).encode())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cad_preview.subprocess, "run", convert)
    kwargs = {
        "kb_id": "kb",
        "doc_id": "doc",
        "original": original,
        "content_hash": "",
        "filename": "drawing.dwg",
    }
    first = cad_preview.render_preview(**kwargs)
    second = cad_preview.render_preview(**kwargs)

    assert first != second
    assert first.read_bytes() == b"1"
    assert second.read_bytes() == b"2"


def test_converter_failure_does_not_publish_partial_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, original = _configure(monkeypatch, tmp_path / "home", tmp_path)
    monkeypatch.setattr(
        cad_preview.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="failed"),
    )

    with pytest.raises(cad_preview.CadPreviewFailed):
        cad_preview.render_preview(
            kb_id="kb",
            doc_id="doc",
            original=original,
            content_hash="c" * 64,
            filename="drawing.dwg",
        )
    assert list((tmp_path / "home" / "knowledge" / "kb" / "previews").glob("*.png")) == []


def test_oversize_preview_is_not_published_to_bounded_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, original = _configure(monkeypatch, tmp_path / "home", tmp_path)
    monkeypatch.setattr(cad_preview, "_MAX_CACHE_BYTES", 4)
    monkeypatch.setattr(
        cad_preview,
        "subprocess",
        SimpleNamespace(
            run=lambda args, **_kwargs: (
                Path(args[3], "preview.png").write_bytes(b"12345")
                and SimpleNamespace(returncode=0, stdout="", stderr="")
            )
        ),
    )

    with pytest.raises(cad_preview.CadPreviewFailed):
        cad_preview.render_preview(
            kb_id="kb",
            doc_id="doc",
            original=original,
            content_hash="d" * 64,
            filename="drawing.dwg",
        )
    assert list((tmp_path / "home" / "knowledge" / "kb" / "previews").glob("*.png")) == []


def test_document_preview_deletion_uses_exact_document_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("MAITU_HOME", str(home))
    previews = home / "knowledge" / "kb" / "previews"
    previews.mkdir(parents=True)
    target = previews / f"doc-a-{'1' * 16}.png"
    legacy = previews / "doc-a-current.png"
    neighbor = previews / f"doc-a-child-{'2' * 64}.png"
    unknown = previews / "doc-a-not-a-cache.png"
    for path in (target, legacy, neighbor, unknown):
        path.write_bytes(path.name.encode())

    result = cad_preview.delete_document_previews("kb", "doc-a")

    assert result == {
        "deleted_files": 2,
        "deleted_bytes": len(target.name) + len(legacy.name),
    }
    assert not target.exists()
    assert not legacy.exists()
    assert neighbor.exists()
    assert unknown.exists()


def test_document_preview_deletion_does_not_create_missing_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "absent-home"
    monkeypatch.setenv("MAITU_HOME", str(home))

    assert cad_preview.delete_document_previews("kb", "doc") == {
        "deleted_files": 0,
        "deleted_bytes": 0,
    }
    assert not home.exists()


def test_pruner_recognizes_legacy_preview_versions(tmp_path: Path) -> None:
    home = tmp_path / "home"
    previews = home / "knowledge" / "kb" / "previews"
    previews.mkdir(parents=True)
    old_hash = previews / "doc-0123456789abcdef.png"
    old_current = previews / "doc-current.png"
    for path in (old_hash, old_current):
        path.write_bytes(b"legacy")

    result = cad_preview.prune_preview_cache(
        home=home, max_bytes=1024, max_files=10, max_age_seconds=0
    )

    assert result == {"deleted_files": 2, "deleted_bytes": 12}
    assert not old_hash.exists()
    assert not old_current.exists()


def test_abandoned_publication_stage_is_removed_before_valid_cache(tmp_path: Path) -> None:
    home = tmp_path / "home"
    previews = home / "knowledge" / "kb" / "previews"
    previews.mkdir(parents=True)
    cached = previews / f"doc-{'a' * 64}.png"
    cached.write_bytes(b"keep")
    abandoned = previews / f".doc-{'a' * 64}.png.{'b' * 32}.tmp"
    abandoned.write_bytes(b"unfinished")
    unknown = previews / ".user-document.tmp"
    unknown.write_bytes(b"user content")

    result = cad_preview.prune_preview_cache(
        home=home, max_bytes=4, max_files=1, max_age_seconds=7 * 86400
    )

    assert result == {"deleted_files": 1, "deleted_bytes": 10}
    assert not abandoned.exists()
    assert cached.read_bytes() == b"keep"
    assert unknown.read_bytes() == b"user content"
