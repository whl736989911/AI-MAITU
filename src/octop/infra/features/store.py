"""Writing feature definitions authored in the settings UI.

The bundled library (``library/``) ships with the application and stays
read-only; everything the UI saves lands in an *overlay root* —
``~/.octop/features/`` — which :class:`~octop.infra.features.catalog.FeatureCatalog`
scans after the library root, so an overlay definition wins on an id collision.

Nothing invalid ever reaches the disk: :func:`~octop.infra.features.schema.validate_manifest`
runs on the manifest this module is about to write, and a definition that fails
is refused with every reason listed. Only user definitions can be written or
removed — a bundled one is refused rather than copied into the overlay, because
a silent copy would fork the shipped definition behind the operator's back.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from octop.infra.features.catalog import MANIFEST_FILENAME, FeatureCatalog
from octop.infra.features.schema import SUPPORTED_VERSIONS, validate_manifest

USER_PROMPT_FILENAME = "PROMPT.md"
"""System prompt file this store writes (and the only name the API can express)."""

_DEFINITION_KEYS = frozenset(
    {
        "id",
        "version",
        "label",
        "description",
        "icon_name",
        "color",
        "unit",
        "input_schema",
        "ui_schema",
        "prompt",
        "output",
        "permissions",
    }
)
_PROMPT_KEYS = frozenset({"user_template", "system_prompt"})

# The id becomes a directory name, so it never carries a separator, a drive
# letter, or a dot: ``..`` and ``a/b`` must not be expressible at all.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class FeatureStoreError(Exception):
    """Base class for feature-write failures."""


class FeatureDefinitionInvalid(FeatureStoreError):
    """A definition that must not be written; ``errors`` lists every problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class FeatureNotFound(FeatureStoreError):
    """No feature with that id is loaded."""


class FeatureAlreadyExists(FeatureStoreError):
    """The id is already served by the catalog, bundled or user-authored."""


class FeatureReadOnly(FeatureStoreError):
    """The feature is served from a root this store must not write to."""


class FeatureStore:
    """Create, update, and delete feature definitions under one writable root.

    The store owns ``root`` (``~/.octop/features/``) and reads through *catalog*
    to learn which id is served by which directory. Write permission is derived
    from that directory alone — :meth:`is_writable` is the single judgement
    behind both the refusals below and the ``_meta`` payload the dashboard greys
    its edit buttons with, so the two can never disagree.
    """

    def __init__(self, root: Path, catalog: FeatureCatalog) -> None:
        self._root = Path(root)
        self._catalog = catalog

    @property
    def root(self) -> Path:
        """Writable overlay root this store writes into."""
        return self._root

    def is_writable(self, feature_id: str) -> bool:
        """Whether a write to *feature_id* lands under this store's root."""
        directory = self._catalog.feature_dir(feature_id)
        return directory is not None and _is_within(self._root, directory)

    def read_only_ids(self) -> list[str]:
        """Loaded ids writes are refused for (the bundled, shipped definitions)."""
        return sorted(
            feature.id for feature in self._catalog.list() if not self.is_writable(feature.id)
        )

    def create(self, definition: dict[str, Any]) -> str:
        """Write a new user feature and return its id.

        The id decides the directory name, so it is checked before anything else;
        an id the catalog already serves is refused instead of shadowed.
        """
        feature_id = _checked_id(definition.get("id"))
        if self._catalog.get(feature_id) is not None:
            raise FeatureAlreadyExists(f"feature {feature_id!r} already exists")
        manifest, prompt_text = _manifest(definition, feature_id)
        self._write(feature_id, manifest, prompt_text)
        return feature_id

    def update(self, feature_id: str, definition: dict[str, Any]) -> str:
        """Overwrite one user feature with *definition* and return its id."""
        directory = self._writable_dir(feature_id)
        manifest, prompt_text = _manifest(definition, feature_id)
        self._write_to(directory, manifest, prompt_text)
        return feature_id

    def delete(self, feature_id: str) -> None:
        """Remove one user feature's directory; bundled ids are refused."""
        directory = self._writable_dir(feature_id)
        shutil.rmtree(directory)
        self._catalog.reload()

    def _writable_dir(self, feature_id: str) -> Path:
        """The overlay directory of *feature_id*, or the reason there is none."""
        directory = self._catalog.feature_dir(feature_id)
        if directory is None:
            raise FeatureNotFound(f"feature {feature_id!r} not found")
        if not self.is_writable(feature_id):
            raise FeatureReadOnly(
                f"feature {feature_id!r} is bundled with the application "
                "and cannot be modified or deleted"
            )
        return directory

    def _write(self, feature_id: str, manifest: dict[str, Any], prompt_text: str | None) -> None:
        self._write_to(self._root / feature_id, manifest, prompt_text)

    def _write_to(
        self,
        directory: Path,
        manifest: dict[str, Any],
        prompt_text: str | None,
    ) -> None:
        """Write one definition, then re-scan so the catalog serves it at once.

        Order matters: ``PROMPT.md`` first, the manifest last. A definition is
        discovered by its manifest, so the catalog never sees one pointing at a
        prompt file that is not there yet — while the reverse order would leave
        exactly that behind if the second write failed.
        """
        directory.mkdir(parents=True, exist_ok=True)
        if prompt_text:
            _atomic_write(directory / USER_PROMPT_FILENAME, prompt_text)
        _atomic_write(
            directory / MANIFEST_FILENAME, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        if not prompt_text:
            # Clearing the system prompt must drop the file too, otherwise a stale
            # PROMPT.md keeps living next to a manifest that no longer names it.
            (directory / USER_PROMPT_FILENAME).unlink(missing_ok=True)
        self._catalog.reload()


def _checked_id(value: Any) -> str:
    """The id of a new feature: a portable, lowercase directory name."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise FeatureDefinitionInvalid(
            [
                "id must be a lowercase directory name of letters, digits, '-' or '_' "
                "(at most 64 characters, starting with a letter or a digit)"
            ]
        )
    return value


def _manifest(definition: dict[str, Any], feature_id: str) -> tuple[dict[str, Any], str | None]:
    """Build the on-disk manifest and the ``PROMPT.md`` text of one definition.

    The request body is not the manifest: the format version is ours, and the
    system prompt arrives as text while the manifest references it by name. Every
    problem found — unknown keys here and whatever
    :func:`~octop.infra.features.schema.validate_manifest` reports about the
    result — is raised together, so one round trip tells the author everything.
    """
    errors: list[str] = []
    unknown = sorted(set(definition) - _DEFINITION_KEYS)
    if unknown:
        errors.append(f"unsupported keys: {_quoted(unknown)}")
    supplied_id = definition.get("id")
    if isinstance(supplied_id, str) and supplied_id != feature_id:
        errors.append(f"id {supplied_id!r} must match the addressed feature {feature_id!r}")

    prompt = definition.get("prompt")
    prompt = prompt if isinstance(prompt, dict) else {}
    unknown_prompt = sorted(set(prompt) - _PROMPT_KEYS)
    if unknown_prompt:
        errors.append(f"prompt uses unsupported keys: {_quoted(unknown_prompt)}")
    prompt_text = _prompt_text(prompt.get("system_prompt"), errors)

    manifest: dict[str, Any] = {
        "id": feature_id,
        "version": _version(definition.get("version"), errors),
        "label": definition.get("label"),
        "description": definition.get("description"),
        "icon_name": definition.get("icon_name"),
        "unit": definition.get("unit"),
        "input_schema": definition.get("input_schema"),
        "prompt": _prompt_manifest(prompt, prompt_text),
        "output": definition.get("output"),
    }
    # Optional keys are omitted rather than written as null: the format treats a
    # missing key as "not set", and a null colour or schema is not a value.
    for key in ("color", "ui_schema", "permissions"):
        if definition.get(key) is not None:
            manifest[key] = definition[key]

    errors.extend(validate_manifest(manifest, feature_id))
    if errors:
        raise FeatureDefinitionInvalid(errors)
    return manifest, prompt_text


def _prompt_text(value: Any, errors: list[str]) -> str | None:
    """The ``PROMPT.md`` body, or ``None`` when the definition declares none."""
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append("prompt.system_prompt must be a string or null")
        return None
    return value.strip() or None


def _prompt_manifest(prompt: dict[str, Any], prompt_text: str | None) -> dict[str, Any]:
    """Manifest ``prompt`` node — ``system_file`` only when a prompt is written."""
    user_template = prompt.get("user_template")
    if prompt_text is None:
        return {"user_template": user_template}
    return {"system_file": USER_PROMPT_FILENAME, "user_template": user_template}


def _version(value: Any, errors: list[str]) -> int:
    """Format version to write: the current one unless the body pins a known one."""
    if value is None:
        return SUPPORTED_VERSIONS[-1]
    if isinstance(value, bool) or value not in SUPPORTED_VERSIONS:
        errors.append(
            f"version {value!r} is not supported (expected {_quoted(SUPPORTED_VERSIONS)})"
        )
        return SUPPORTED_VERSIONS[-1]
    return int(value)


def _quoted(values: Any) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* so a reader sees the old file or the complete new one."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _is_within(root: Path, path: Path) -> bool:
    """Whether *path* sits under *root* (resolved, so symlinked roots agree)."""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False
