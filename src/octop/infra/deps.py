"""Runtime dependency audit: what this build needs, and what is actually usable.

Capability extras only declare Python distributions; the failures that actually
reach an operator are narrower and louder:

* a distribution installs but its import dies on a missing system library
  (rapidocr -> cv2 -> ``libGL.so.1``),
* a capability needs an external executable that no wheel can provide
  (``xdotool`` for desktop control),
* an extra simply was not synced on this host.

So the check probes imports and binaries in-process instead of reading installed
metadata, and carries the OS packages that would fix each failure. Everything
here is structured data: the CLI owns wording/localization, the installer owns
privileges.
"""

from __future__ import annotations

import importlib
import shutil
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Literal

ProbeKind = Literal["import", "binary"]


@dataclass(frozen=True)
class Component:
    """One capability and the concrete things that must work for it."""

    key: str
    extra: str
    imports: tuple[str, ...] = ()
    binaries: tuple[str, ...] = ()
    apt: tuple[str, ...] = ()


@dataclass(frozen=True)
class Missing:
    kind: ProbeKind
    name: str
    detail: str


@dataclass(frozen=True)
class CheckResult:
    component: Component
    required: bool
    missing: tuple[Missing, ...]

    @property
    def ok(self) -> bool:
        return not self.missing


# The base component is always required: every deployment parses/stores documents.
# Extra components are only required when the caller says that extra was selected,
# so a deliberately small install does not report permanent failures.
COMPONENTS: tuple[Component, ...] = (
    Component(key="core_files", extra="", imports=("pymupdf",)),
    Component(
        key="local_embedding", extra="local-embedding", imports=("fastembed", "huggingface_hub")
    ),
    Component(
        key="knowledge_ocr",
        extra="knowledge-ocr",
        imports=("rapidocr", "onnxruntime", "cv2"),
        apt=("libgl1", "libglib2.0-0"),
    ),
    Component(key="office", extra="office", imports=("pdfplumber", "pandas")),
    Component(key="browser", extra="browser", imports=("playwright",)),
    Component(
        key="desktop",
        extra="desktop",
        imports=("mss", "pynput"),
        binaries=("xdotool", "xdpyinfo"),
        apt=("xdotool", "x11-utils", "xauth", "xvfb"),
    ),
)

EXTRAS: tuple[str, ...] = tuple(dict.fromkeys(c.extra for c in COMPONENTS if c.extra))

_IMPORT_CACHE: dict[str, str | None] = {}


def _probe_import(module: str) -> str | None:
    """Return the failure text for *module*, or None when it imports cleanly."""
    if module not in _IMPORT_CACHE:
        try:
            importlib.import_module(module)
        except Exception as exc:  # ImportError, OSError from a missing .so, ...
            _IMPORT_CACHE[module] = f"{type(exc).__name__}: {exc}"
        else:
            _IMPORT_CACHE[module] = None
    return _IMPORT_CACHE[module]


def _probe_binary(binary: str) -> str | None:
    return None if shutil.which(binary) else "not found on PATH"


def check(extras: Collection[str]) -> list[CheckResult]:
    """Audit every component; ``extras`` decides which ones are required."""
    selected = {extra.strip() for extra in extras if extra and extra.strip()}
    results: list[CheckResult] = []
    for component in COMPONENTS:
        required = not component.extra or component.extra in selected
        missing = [
            Missing("import", module, detail)
            for module in component.imports
            if (detail := _probe_import(module)) is not None
        ]
        missing.extend(
            Missing("binary", binary, detail)
            for binary in component.binaries
            if (detail := _probe_binary(binary)) is not None
        )
        results.append(CheckResult(component, required, tuple(missing)))
    return results


def missing_apt_packages(results: Iterable[CheckResult]) -> list[str]:
    """OS packages that would fix the failures of *required* components."""
    packages: list[str] = []
    for result in results:
        if not result.required or result.ok:
            continue
        for package in result.component.apt:
            if package not in packages:
                packages.append(package)
    return packages


def healthy(results: Iterable[CheckResult]) -> bool:
    """True when every required component passed."""
    return all(result.ok for result in results if result.required)


def failed_components(results: Iterable[CheckResult]) -> list[CheckResult]:
    return [result for result in results if result.required and not result.ok]
