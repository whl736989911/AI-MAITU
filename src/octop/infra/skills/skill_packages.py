"""Source-neutral skill package validation and workspace path normalization."""

from __future__ import annotations

import os
import stat
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath

MAX_SKILL_FILES = 2_000
MAX_SKILL_BYTES = 64 * 1024 * 1024


class SkillPackageError(ValueError):
    """A resolved skill package is invalid."""


class SkillPackageTooLarge(SkillPackageError):
    """A resolved skill package exceeded a safety limit."""


@dataclass(frozen=True)
class ResolvedSkillPackage:
    """Canonical package produced by any skill source adapter."""

    slug: str
    files: tuple[tuple[str, bytes], ...]
    source: str
    source_url: str = ""

    def workspace_uploads(self) -> list[tuple[str, bytes]]:
        return [
            (f"skills/{self.slug}/{relative_path}", content)
            for relative_path, content in self.files
        ]


def validate_skill_slug(slug: str) -> str:
    normalized = slug.strip()
    if (
        not normalized
        or normalized.startswith(".")
        or "/" in normalized
        or "\\" in normalized
        or "\x00" in normalized
    ):
        raise SkillPackageError("invalid skill name")
    return normalized


def _normalize_relative_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise SkillPackageError(f"invalid skill package path: {value}")
    is_dir_marker = value.endswith("/")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or (path.parts and (path.parts[0].endswith(":") or path.parts[0] == ""))
    ):
        raise SkillPackageError(f"unsafe skill package path: {value}")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise SkillPackageError(f"invalid skill package path: {value}")
    if is_dir_marker and not normalized.endswith("/"):
        normalized = f"{normalized}/"
    return normalized


def normalize_skill_files(
    files: list[tuple[str, bytes]],
    *,
    require_manifest: bool = True,
) -> tuple[tuple[str, bytes], ...]:
    """Validate and canonicalize source-relative skill files."""
    if len(files) > MAX_SKILL_FILES:
        raise SkillPackageTooLarge("skill package has too many files")

    normalized: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    total = 0
    for relative_path, content in files:
        path = _normalize_relative_path(relative_path)
        if path in seen:
            raise SkillPackageError(f"duplicate skill package path: {path}")
        if not isinstance(content, bytes):
            raise SkillPackageError(f"skill package content must be bytes: {path}")
        seen.add(path)
        total += len(content)
        if total > MAX_SKILL_BYTES:
            raise SkillPackageTooLarge("skill package size exceeds 64 MB")
        normalized.append((path, content))

    if require_manifest and "SKILL.md" not in seen:
        raise SkillPackageError("skill package does not contain a root SKILL.md")
    if require_manifest:
        manifest = next(content for path, content in normalized if path == "SKILL.md")
        try:
            manifest.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillPackageError("skill package SKILL.md must be UTF-8 text") from exc
    return tuple(normalized)


_SKILL_SECRET_NAMES = {".env", ".env.local", ".env.production", "credentials.json"}


def build_skill_zip(skills: dict[str, list[tuple[str, bytes]]]) -> bytes:
    """Create a ZIP with importer-compatible ``<slug>/SKILL.md`` roots."""
    output = BytesIO()
    total_files = 0
    total_bytes = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for slug, files in sorted(skills.items()):
            safe_slug = validate_skill_slug(slug)
            for relative_path, content in normalize_skill_files(files):
                parts = PurePosixPath(relative_path).parts
                if any(
                    part.startswith(".") or part.lower() in _SKILL_SECRET_NAMES for part in parts
                ):
                    continue
                total_files += 1
                total_bytes += len(content)
                if total_files > MAX_SKILL_FILES:
                    raise SkillPackageTooLarge("skill ZIP has too many files")
                if total_bytes > MAX_SKILL_BYTES:
                    raise SkillPackageTooLarge("skill ZIP contents exceed 64 MB")
                archive.writestr(f"{safe_slug}/{relative_path}", content)
    result = output.getvalue()
    if len(result) > MAX_SKILL_BYTES:
        raise SkillPackageTooLarge("skill ZIP exceeds 64 MB")
    return result


def read_skill_zip(data: bytes) -> dict[str, list[tuple[str, bytes]]]:
    """Read one or more skills from a bounded ZIP without extracting to disk."""
    if len(data) > MAX_SKILL_BYTES:
        raise SkillPackageTooLarge("skill ZIP exceeds 64 MB")
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise SkillPackageError("invalid skill ZIP") from exc
    skills: dict[str, list[tuple[str, bytes]]] = {}
    seen: set[tuple[str, str]] = set()
    total = 0
    entries = archive.infolist()
    if len(entries) > MAX_SKILL_FILES:
        raise SkillPackageTooLarge("skill ZIP has too many files")
    for entry in entries:
        name = entry.filename
        mode = (entry.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise SkillPackageError(f"symlinks are not allowed in skill ZIP: {name}")
        if entry.is_dir():
            _normalize_relative_path(name)
            continue
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or "\\" in name
            or "\x00" in name
            or ".." in path.parts
            or (path.parts and path.parts[0].endswith(":"))
        ):
            raise SkillPackageError(f"unsafe skill ZIP path: {name}")
        parts = path.parts[1:] if path.parts and path.parts[0] == "skills" else path.parts
        if len(parts) < 2:
            raise SkillPackageError("skill ZIP must contain <skill>/SKILL.md files")
        slug = validate_skill_slug(parts[0])
        relative = _normalize_relative_path(PurePosixPath(*parts[1:]).as_posix())
        if any(part.startswith(".") or part.lower() in _SKILL_SECRET_NAMES for part in parts[1:]):
            continue
        key = (slug, relative)
        if key in seen:
            raise SkillPackageError(f"duplicate skill ZIP path: {name}")
        seen.add(key)
        total += entry.file_size
        if total > MAX_SKILL_BYTES:
            raise SkillPackageTooLarge("skill ZIP contents exceed 64 MB")
        if entry.file_size < 0 or entry.file_size > MAX_SKILL_BYTES:
            raise SkillPackageTooLarge("skill ZIP member exceeds 64 MB")
        content = archive.read(entry)
        if len(content) != entry.file_size:
            raise SkillPackageError(f"invalid skill ZIP member size: {name}")
        skills.setdefault(slug, []).append((relative, content))
    result = {}
    for slug, files in skills.items():
        result[slug] = list(normalize_skill_files(files))
    if not result:
        raise SkillPackageError("skill ZIP contains no importable skills")
    return result


def resolve_skill_package(
    *,
    slug: str,
    files: list[tuple[str, bytes]],
    source: str,
    source_url: str = "",
) -> ResolvedSkillPackage:
    return ResolvedSkillPackage(
        slug=validate_skill_slug(slug),
        files=normalize_skill_files(files),
        source=source.strip() or "unknown",
        source_url=source_url.strip(),
    )


def resolve_workspace_uploads(
    *,
    slug: str,
    uploads: list[tuple[str, bytes]],
    source: str,
    source_url: str = "",
) -> ResolvedSkillPackage:
    """Convert legacy workspace upload pairs into a canonical package."""
    safe_slug = validate_skill_slug(slug)
    prefix = f"skills/{safe_slug}/"
    files: list[tuple[str, bytes]] = []
    for path, content in uploads:
        if not path.startswith(prefix):
            raise SkillPackageError(f"skill upload is outside {prefix}: {path}")
        files.append((path[len(prefix) :], content))
    return resolve_skill_package(
        slug=safe_slug,
        files=files,
        source=source,
        source_url=source_url,
    )


def read_skill_directory(skill_dir: Path) -> list[tuple[str, bytes]]:
    """Read a CLI-produced package without following links."""
    files: list[tuple[str, bytes]] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        current = Path(dirpath)
        for dirname in dirnames:
            if (current / dirname).is_symlink():
                raise SkillPackageError(f"unsupported symlink in skill package: {dirname}")
        for filename in filenames:
            path = current / filename
            entry_stat = path.lstat()
            if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(entry_stat.st_mode):
                raise SkillPackageError(f"unsupported file type in skill package: {filename}")
            total += entry_stat.st_size
            if total > MAX_SKILL_BYTES:
                raise SkillPackageTooLarge("skill package size exceeds 64 MB")
            files.append((path.relative_to(skill_dir).as_posix(), path.read_bytes()))
            if len(files) > MAX_SKILL_FILES:
                raise SkillPackageTooLarge("skill package has too many files")
    return list(normalize_skill_files(files))


def read_cli_skill_install(install_root: Path) -> list[tuple[str, bytes]]:
    """Read one CLI-installed skill without deriving a path from its requested slug."""
    root_manifest = install_root / "SKILL.md"
    try:
        root_manifest_mode = root_manifest.lstat().st_mode
    except FileNotFoundError:
        root_manifest_mode = 0
    if stat.S_ISREG(root_manifest_mode):
        return read_skill_directory(install_root)

    candidates: list[Path] = []
    for entry in install_root.iterdir():
        try:
            entry_mode = entry.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(entry_mode):
            continue
        manifest = entry / "SKILL.md"
        try:
            manifest_mode = manifest.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISREG(manifest_mode):
            candidates.append(entry)

    if not candidates:
        raise SkillPackageError("CLI install did not produce a skill package")
    if len(candidates) > 1:
        raise SkillPackageError("CLI install produced multiple skill packages")
    return read_skill_directory(candidates[0])


__all__ = [
    "MAX_SKILL_BYTES",
    "MAX_SKILL_FILES",
    "ResolvedSkillPackage",
    "SkillPackageError",
    "SkillPackageTooLarge",
    "normalize_skill_files",
    "read_cli_skill_install",
    "read_skill_directory",
    "resolve_skill_package",
    "resolve_workspace_uploads",
    "validate_skill_slug",
]
