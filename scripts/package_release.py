"""Build a MAITU source release ZIP, without local state or embedded secrets."""

from __future__ import annotations

import argparse
import json
import os
import platform
import stat
import tomllib
import uuid
import zipfile
from pathlib import Path

from packaging.version import Version

# Deliberately allowlist inputs: a Git checkout can contain customer data and secrets.
DIRECTORIES = ("src", "dashboard", "docker", "scripts/deployment", "scripts/security")
FILES = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "README_CN.md",
    "deploy.sh",
    "scripts/deploy.py",
    "scripts/README.md",
    "scripts/setup-host-sandbox.sh",
    ".env.example",
    ".dockerignore",
    "brand.config.json",
    "scripts/maitu_updater.py",
    "scripts/maitu-updater-requirements.txt",
)
EXCLUDED = {
    "node_modules",
    "__pycache__",
    ".vite",
    ".venv",
    "dist",
    "build",
    ".git",
    "dashboard",  # Only excluded inside src/octop; runtime UI is built in Docker.
}


def package(source: Path, output: Path, architecture: str, release_notes: str) -> Path:
    source = source.resolve()
    version = str(
        Version(tomllib.loads((source / "pyproject.toml").read_text("utf-8"))["project"]["version"])
    )
    if architecture not in {"amd64", "arm64"}:
        raise ValueError("architecture must be amd64 or arm64")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"maitu-{version}.zip"
    metadata = {
        "product": "maitu",
        "version": version,
        "package_type": "source",
        "platform": "linux",
        "architecture": architecture,
        "release_notes": release_notes,
    }
    temporary = output / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("maitu-release.json", json.dumps(metadata, ensure_ascii=False))
            for relative in FILES:
                path = source / relative
                if not path.is_file() or path.is_symlink():
                    raise ValueError(f"missing or linked release input: {relative}")
                archive.write(path, relative)
            for directory in DIRECTORIES:
                for path in sorted((source / directory).rglob("*")):
                    relative = path.relative_to(source)
                    parts = relative.parts[1:]
                    excluded = EXCLUDED - {"dashboard"} if directory != "src" else EXCLUDED
                    if any(part in excluded for part in parts):
                        continue
                    if path.is_symlink():
                        raise ValueError(f"linked release input: {relative.as_posix()}")
                    if not path.is_file():
                        continue
                    if path.name.startswith(".env") and path.name != ".env.example":
                        continue
                    if path.suffix in {".pyc", ".log"}:
                        continue
                    mode = path.stat().st_mode
                    if not stat.S_ISREG(mode):
                        raise ValueError(f"non-regular release input: {relative.as_posix()}")
                    archive.write(path, relative.as_posix())
        # Atomic publication without overwriting an existing release of this version.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--architecture",
        choices=("amd64", "arm64"),
        default={"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64"}.get(
            platform.machine(), "amd64"
        ),
    )
    parser.add_argument("--notes-file", type=Path)
    args = parser.parse_args()
    notes = args.notes_file.read_text("utf-8") if args.notes_file else ""
    print(package(args.source, args.output, args.architecture, notes))


if __name__ == "__main__":
    main()
