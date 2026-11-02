"""Build the complete source ZIP that ``deploy/ubuntu`` install/upgrade accept.

``package_release.py`` builds the updater/Docker artifact: it omits the built
dashboard and writes ``maitu-release.json``. The Ubuntu scripts need the other
shape — a source root that already contains ``src/octop/dashboard`` plus a
``SOURCE-PACKAGE.json`` hash manifest — and nothing in the repo produced it, so
an upgrade had no valid input.

The allowlist stays explicit: a checkout can contain local state, logs and
secrets, and a release archive must never carry them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tomllib
import uuid
import zipfile
from pathlib import Path

INCLUDE_DIRS = ("src", "dashboard", "deploy", "docker", "docs", "scripts")
INCLUDE_FILES = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "README_CN.md",
    "LICENSE",
    "deploy.sh",
    "brand.config.json",
    ".env.example",
    ".dockerignore",
    ".gitignore",
)
SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".vite",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    "downloads",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".zip", ".tar", ".gz", ".whl"}
# Local state, not source: never ship an operator's database, agents or backups.
SKIP_PREFIXES = ("data/", "backups/", "agents/", "knowledge/", "logs/", "output/")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def include(relative: Path) -> bool:
    text = relative.as_posix()
    if any(text.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    if any(part in SKIP_DIRS for part in relative.parts[:-1]):
        return False
    name = relative.name
    if name.startswith(".env") and name != ".env.example":
        return False
    if name in {"config.json", "maitu.db", "memory.sqlite"}:
        return False
    return relative.suffix not in SKIP_SUFFIXES


def iter_files(source: Path) -> list[Path]:
    files: list[Path] = []
    for name in INCLUDE_FILES:
        path = source / name
        if path.is_file():
            files.append(Path(name))
    for directory in INCLUDE_DIRS:
        root = source / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(source)
            if include(relative):
                files.append(relative)
    return sorted(set(files))


def base_commit(source: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build(source: Path, output: Path) -> Path:
    source = source.resolve()
    if not (source / "src" / "octop" / "dashboard" / "index.html").is_file():
        raise SystemExit("src/octop/dashboard is missing: run `make build-frontend` first")
    version = str(
        tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    )
    commit = base_commit(source)
    files = iter_files(source)
    manifest = {
        "product": "maitu",
        "package_type": "source",
        "project_version": version,
        "base_commit": commit,
        "file_count": len(files),
        "sha256": {relative.as_posix(): sha256(source / relative) for relative in files},
    }

    output.mkdir(parents=True, exist_ok=True)
    stamp = commit[:7] if commit else "nogit"
    destination = output / f"maitu-source-{version}-{stamp}.zip"
    temporary = output / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "SOURCE-PACKAGE.json",
                json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
            )
            for relative in files:
                archive.write(source / relative, relative.as_posix())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archive = build(args.source, args.output)
    print(archive)
    print(f"sha256 {sha256(archive)}")
    print(f"size   {archive.stat().st_size} bytes")


if __name__ == "__main__":
    sys.exit(main())
