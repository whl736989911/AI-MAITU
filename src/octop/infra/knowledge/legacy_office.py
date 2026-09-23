"""LibreOffice headless conversion for the binary Office formats (design §6.1).

``.doc`` and ``.ppt`` predate the XML formats, so there is no text to read out
of them directly. The design's answer is a headless LibreOffice pass that writes
``.docx`` / ``.pptx`` into a temporary directory for the ordinary parsers to
read.

Three rules shape this module:

* **The source is only read.** A scan reads a share; it does not edit one, and
  §6.1 says so. The conversion writes into a temporary directory and the
  original path is passed to the converter as-is.
* **The profile is temporary too.** LibreOffice locks its user profile while it
  runs, so the profile is redirected into the same temporary directory. Without
  that, two conversions of one share fight over the real profile — and the
  platform would be writing into the server user's home, which a scan has no
  business doing.
* **Every failure names itself.** A host without LibreOffice raises
  :class:`LegacyConversionUnavailable` carrying the install hint, and a
  converter that writes nothing raises :class:`LegacyConversionFailed` carrying
  the converter's own last words, so a per-file failure reason is never guessed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LEGACY_OFFICE_SUFFIXES: dict[str, str] = {".doc": ".docx", ".ppt": ".pptx"}
"""The binary formats, and the XML format each converts to before parsing."""

_ENV_OVERRIDE = "OCTOP_LIBREOFFICE_PATH"
_TIMEOUT_SECONDS = 120
_PROGRAM_NAMES = ("soffice", "soffice.exe", "libreoffice")
_PROGRAM_PATHS = (
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/opt/libreoffice/program/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)
# A windowed parent would flash a console window on Windows for every
# conversion; 0 is the documented "no flags" value on the other platforms.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LegacyConversionUnavailable(RuntimeError):
    """This host has no LibreOffice, so the format cannot be converted."""


class LegacyConversionFailed(RuntimeError):
    """LibreOffice ran and produced nothing usable."""


def find_libreoffice() -> str | None:
    """The LibreOffice binary to convert with, or ``None`` when there is none.

    ``OCTOP_LIBREOFFICE_PATH`` wins when set, because a host may keep the binary
    somewhere ``PATH`` does not name. A set-but-wrong override raises rather than
    falling through to the search: silently converting with a different binary
    than the one an administrator named is exactly the surprise this setting
    exists to avoid. After that the usual names on ``PATH`` and the usual install
    locations are tried, so the common case needs no configuration at all.
    """
    override = os.environ.get(_ENV_OVERRIDE, "").strip()
    if override:
        if Path(override).is_file():
            return override
        raise LegacyConversionUnavailable(
            f"{_ENV_OVERRIDE} points at {override}, which is not a file"
        )
    for name in _PROGRAM_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for candidate in _PROGRAM_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


@contextmanager
def converted_copy(path: Path) -> Iterator[Path]:
    """Yield the converted ``.docx`` / ``.pptx`` for *path*, then clean up.

    The temporary directory holds both the output and the LibreOffice profile,
    so nothing this needs outlives the call and nothing lands outside it.
    """
    target = LEGACY_OFFICE_SUFFIXES.get(path.suffix.lower())
    if target is None:
        raise LegacyConversionFailed(
            f"{path.suffix or '(no extension)'} is not a format LibreOffice converts"
        )
    binary = find_libreoffice()
    if binary is None:
        raise LegacyConversionUnavailable(
            f"reading {path.suffix.lower()} files needs LibreOffice: install it, "
            f"or point {_ENV_OVERRIDE} at the soffice binary"
        )
    with tempfile.TemporaryDirectory(prefix="octop-legacy-") as tmp:
        workdir = Path(tmp)
        outdir = workdir / "out"
        outdir.mkdir()
        result = _run_converter(binary, path, outdir, target, workdir)
        produced = next(iter(sorted(outdir.glob(f"*{target}"))), None)
        if produced is None:
            raise LegacyConversionFailed(_failure_reason(result, target))
        yield produced


def _run_converter(
    binary: str, path: Path, outdir: Path, target: str, workdir: Path
) -> subprocess.CompletedProcess[str]:
    command = [
        binary,
        f"-env:UserInstallation={(workdir / 'profile').as_uri()}",
        "--headless",
        "--norestore",
        "--nodefault",
        "--nologo",
        "--convert-to",
        target.lstrip("."),
        "--outdir",
        str(outdir),
        str(path),
    ]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise LegacyConversionFailed(
            f"LibreOffice did not finish within {_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise LegacyConversionFailed(f"could not run {binary}: {exc}") from exc


def _failure_reason(result: subprocess.CompletedProcess[str], target: str) -> str:
    """Why a conversion produced nothing, in the converter's own words.

    The reason is shown to an administrator on the file's row (§8.4), so it
    reports what LibreOffice said, and only falls back to the exit code when it
    said nothing at all.
    """
    said = [
        line.strip()
        for line in f"{result.stdout or ''}\n{result.stderr or ''}".splitlines()
        if line.strip()
    ]
    detail = said[-1] if said else f"exit code {result.returncode}"
    return f"LibreOffice wrote no {target} file: {detail}"
