"""Verify an unpacked MAITU source archive against its SOURCE-PACKAGE.json.

Only the Python standard library is used, so this runs before uv/Node exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default=".", help="Unpacked source root")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    root = Path(args.source).resolve()
    manifest_path = root / "SOURCE-PACKAGE.json"
    if not manifest_path.is_file():
        print(f"error: missing {manifest_path}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        hashes: dict[str, str] = manifest["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: cannot read SOURCE-PACKAGE.json: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    for name, expected in hashes.items():
        path = root / name
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing/non-file: {name}")
            continue
        actual = sha256(path)
        if actual != expected:
            errors.append(f"hash mismatch: {name}")
    if manifest.get("file_count") not in (None, len(hashes)):
        errors.append(
            f"file_count mismatch: manifest says {manifest.get('file_count')}, hashes={len(hashes)}"
        )
    if errors:
        for item in errors[:50]:
            print(f"error: {item}", file=sys.stderr)
        if len(errors) > 50:
            print(f"error: ... and {len(errors) - 50} more", file=sys.stderr)
        return 1
    if not args.quiet:
        print(
            f"source package OK: {len(hashes)} files, "
            f"{manifest.get('project_version', '?')} @ {manifest.get('base_commit', '?')[:12]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
