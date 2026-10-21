from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from octop.infra.skills.skill_packages import (
    SkillPackageError,
    build_skill_zip,
    read_skill_zip,
)


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, content in entries:
            archive.writestr(path, content)
    return stream.getvalue()


def test_skill_zip_roundtrip_and_hidden_secret_filter() -> None:
    files = [
        ("SKILL.md", b"---\nname: demo\n---\n"),
        ("docs/guide.md", b"guide"),
        (".env", b"token"),
    ]
    archive = build_skill_zip({"demo": files})
    parsed = read_skill_zip(archive)
    assert parsed == {"demo": files[:2]}


def test_skill_zip_rejects_traversal() -> None:
    with pytest.raises(SkillPackageError, match="unsafe"):
        read_skill_zip(_zip([("../escape/SKILL.md", b"bad")]))


def test_skill_zip_rejects_same_name_file_collision() -> None:
    with pytest.raises(SkillPackageError, match="duplicate"):
        read_skill_zip(_zip([("demo/SKILL.md", b"a"), ("demo/SKILL.md", b"b")]))


def test_skill_zip_rejects_no_manifest() -> None:
    with pytest.raises(SkillPackageError, match="SKILL.md"):
        read_skill_zip(_zip([("demo/readme.txt", b"x")]))


def test_skill_zip_rejects_symlink_members() -> None:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("demo/SKILL.md", b"---\nname: demo\n---\n")
        link = zipfile.ZipInfo("demo/link")
        link.create_system = 3
        link.external_attr = (0o120777 << 16) | 0xA000
        archive.writestr(link, b"../../outside")

    with pytest.raises(SkillPackageError, match="symlinks"):
        read_skill_zip(stream.getvalue())


def test_skill_zip_rejects_windows_absolute_paths() -> None:
    with pytest.raises(SkillPackageError, match="unsafe"):
        read_skill_zip(_zip([("C:/outside/SKILL.md", b"bad")]))
