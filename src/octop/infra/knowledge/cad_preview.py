"""Cached raster/PDF previews for CAD originals (DWG/DXF).

The platform does not render CAD itself: LibreDWG + ezdxf live in an external
converter script, and this module owns the contract around it — where the
converter is, where its output is cached, and when a cached preview is stale.

Three rules:

* **Optional, never fatal.** A host without the converter keeps the existing
  text preview and the original download; only the rendered preview is absent,
  and the caller learns that from :func:`converter` returning ``None``.
* **Cached by content.** The output file name carries the document's content
  hash, so an updated original produces a new preview instead of serving the
  old drawing under a new version.
* **Bounded.** The converter runs with a timeout and its own output directory;
  nothing it writes escapes the knowledge base's ``previews`` folder.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from octop.infra.knowledge.files import knowledge_base_dir

CAD_PREVIEW_SUFFIXES = frozenset({".dwg", ".dxf"})
"""Formats the external converter understands."""

_ENV_SCRIPT = "OCTOP_DWG_PREVIEW_SCRIPT"
_ENV_PYTHON = "OCTOP_DWG_PREVIEW_PYTHON"
_DEFAULT_SCRIPT = "/opt/maitu/tools/dwg_preview.py"
_TIMEOUT_SECONDS = 300


class CadPreviewUnavailable(RuntimeError):
    """No converter is configured on this host."""


class CadPreviewFailed(RuntimeError):
    """The converter ran but produced no usable image."""


def converter() -> tuple[str, str] | None:
    """``(python, script)`` for the external converter, or ``None`` if absent."""
    script = os.environ.get(_ENV_SCRIPT, _DEFAULT_SCRIPT).strip()
    if not script or not Path(script).is_file():
        return None
    python = os.environ.get(_ENV_PYTHON, "").strip() or sys.executable
    return python, script


def supports(filename: str) -> bool:
    return Path(filename).suffix.lower() in CAD_PREVIEW_SUFFIXES


def previews_dir(kb_id: str) -> Path:
    path = knowledge_base_dir(kb_id) / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def preview_png_path(kb_id: str, doc_id: str, content_hash: str) -> Path:
    digest = (content_hash or "current")[:16]
    return previews_dir(kb_id) / f"{doc_id}-{digest}.png"


def render_preview(
    *, kb_id: str, doc_id: str, original: Path, content_hash: str, filename: str
) -> Path:
    """Return a cached PNG for *original*, converting once when absent."""
    if not supports(filename):
        raise CadPreviewUnavailable(f"{filename} is not a CAD drawing")
    target = preview_png_path(kb_id, doc_id, content_hash)
    if target.is_file() and target.stat().st_size > 0:
        return target
    found = converter()
    if found is None:
        raise CadPreviewUnavailable(
            f"no CAD preview converter; set {_ENV_SCRIPT} to a dwg_preview.py path"
        )
    python, script = found
    with tempfile.TemporaryDirectory(
        prefix=f"work-{doc_id}-", dir=previews_dir(kb_id)
    ) as directory:
        workdir = Path(directory)
        try:
            result = subprocess.run(
                [python, script, str(original), str(workdir)],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CadPreviewFailed(f"converter could not run: {exc}") from exc
        produced = next(iter(sorted(workdir.glob("*.png"))), None)
        if produced is None or produced.stat().st_size == 0:
            detail = (result.stderr or result.stdout).strip()[-600:]
            raise CadPreviewFailed(f"converter produced no image: {detail}")
        target.write_bytes(produced.read_bytes())
    return target


__all__ = [
    "CAD_PREVIEW_SUFFIXES",
    "CadPreviewFailed",
    "CadPreviewUnavailable",
    "converter",
    "preview_png_path",
    "previews_dir",
    "render_preview",
    "supports",
]
