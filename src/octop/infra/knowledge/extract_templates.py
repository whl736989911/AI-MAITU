"""Managing extraction templates and where they apply (design §7).

The service owns the three rules the repo cannot express:

* **Editing writes a version, never an overwrite** (§7.3), which is what keeps a
  recorded result able to name the version that produced it.
* **A template in use is not deleted** (§7.2). The visible use is a binding, and
  a binding can be cleared — so a refusal names what stands in the way instead of
  being a dead end.
* **A binding has to be able to work.** A binding names a path inside a *folder*
  source, and a pattern that does not compile is refused when it is written
  rather than silently never matching later.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from octop.infra.db.repos.data_sources import DataSourceRow
from octop.infra.db.repos.extract_templates import (
    STATUS_DISABLED,
    STATUSES,
    ExtractBindingRow,
    ExtractTemplateRepo,
    ExtractTemplateRow,
    TemplateVersionRow,
)
from octop.infra.knowledge.service import knowledge_content_type
from octop.infra.knowledge.sources import is_folder_kind
from octop.infra.knowledge.template_match import (
    parse_fields,
    resolve_template,
)

MAX_NAME = 120
MAX_DESCRIPTION = 500
MAX_NOTE = 500


class TemplateInUse(RuntimeError):
    """A template cannot be removed while something still points at it."""

    def __init__(self, bindings: int) -> None:
        self.bindings = bindings
        super().__init__(
            f"this template is bound to {bindings} place(s); clear the bindings before deleting it"
        )


class ExtractTemplateService:
    """Create, version, bind, and resolve extraction templates."""

    def __init__(self, services: Any) -> None:
        self._services = services
        self._repo: ExtractTemplateRepo = services.extract_templates_repo

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def list_templates(self) -> list[ExtractTemplateRow]:
        return self._repo.list_all()

    def get(self, template_id: str) -> ExtractTemplateRow:
        template = self._repo.get(template_id)
        if template is None:
            raise LookupError("extraction template not found")
        return template

    def current_version(self, template_id: str) -> TemplateVersionRow:
        version = self._repo.get_version(template_id)
        if version is None:
            # ``create`` writes version 1 in the same transaction, so a template
            # without a current version is a broken row, not an empty one.
            raise RuntimeError(f"extraction template {template_id!r} has no version")
        return version

    def create(
        self,
        *,
        actor_user_id: int,
        name: str,
        description: str = "",
        fields: object = None,
        instruction: str = "",
        applies_to: str = "",
        note: str = "",
    ) -> tuple[ExtractTemplateRow, TemplateVersionRow]:
        cleaned = _name(name)
        payload = _fields_json(fields)
        template = self._repo.create(
            name=cleaned,
            description=_bounded(description, MAX_DESCRIPTION, "description"),
            fields_json=payload,
            instruction=_bounded(instruction, MAX_NOTE * 4, "instruction"),
            applies_to=_applies_to(applies_to),
            note=_bounded(note, MAX_NOTE, "note"),
            created_by=actor_user_id,
        )
        return template, self.current_version(template.id)

    def update(
        self,
        template_id: str,
        *,
        actor_user_id: int,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> ExtractTemplateRow:
        """Rename, re-describe, enable, or disable a template.

        Separate from :meth:`add_version` on purpose: a rename is not a new
        version of the extraction, and bumping the version for one would mark
        every result built from it as stale for nothing.
        """
        template = self.get(template_id)
        cleaned_status = None
        if status is not None:
            cleaned_status = status.strip().lower()
            if cleaned_status not in STATUSES:
                raise ValueError(f"unknown template status {status!r}; expected one of {STATUSES}")
            if cleaned_status == template.status:
                cleaned_status = None
        updated = self._repo.update(
            template_id,
            name=_name(name) if name is not None else None,
            description=(
                _bounded(description, MAX_DESCRIPTION, "description")
                if description is not None
                else None
            ),
            status=cleaned_status,
        )
        if updated is None:
            raise LookupError("extraction template not found")
        return updated

    def add_version(
        self,
        template_id: str,
        *,
        actor_user_id: int,
        fields: object,
        instruction: str = "",
        applies_to: str = "",
        note: str = "",
    ) -> TemplateVersionRow:
        """Record an edit as a new version (design §7.3)."""
        self.get(template_id)
        version = self._repo.add_version(
            template_id,
            fields_json=_fields_json(fields),
            instruction=_bounded(instruction, MAX_NOTE * 4, "instruction"),
            applies_to=_applies_to(applies_to),
            note=_bounded(note, MAX_NOTE, "note"),
            created_by=actor_user_id,
        )
        if version is None:
            raise LookupError("extraction template not found")
        return version

    def list_versions(self, template_id: str) -> list[TemplateVersionRow]:
        self.get(template_id)
        return self._repo.list_versions(template_id)

    def delete(self, template_id: str) -> None:
        """Remove a template that nothing uses (design §7.2)."""
        template = self.get(template_id)
        bindings = self._repo.count_bindings(template.id)
        if bindings:
            raise TemplateInUse(bindings)
        self._repo.delete(template.id)

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def list_bindings(self, *, template_id: str | None = None) -> list[ExtractBindingRow]:
        if template_id is not None:
            self.get(template_id)
        return self._repo.list_bindings(template_id=template_id)

    def bind(
        self,
        template_id: str,
        *,
        actor_user_id: int,
        data_source_id: str,
        path: str = "",
        extension: str = "",
        mime_type: str = "",
        name_pattern: str = "",
        match_regex: str = "",
    ) -> ExtractBindingRow:
        template = self.get(template_id)
        if not template.enabled:
            raise ValueError("a disabled template cannot be bound; enable it first (design §7.2)")
        source = self._source(data_source_id)
        cleaned_path = _binding_path(path)
        pattern = _compilable(match_regex)
        return self._repo.create_binding(
            template_id=template.id,
            data_source_id=source.id,
            path=cleaned_path,
            extension=extension.strip(),
            mime_type=mime_type.strip(),
            name_pattern=name_pattern.strip(),
            match_regex=pattern,
            created_by=actor_user_id,
        )

    def unbind(self, binding_id: str) -> None:
        if not self._repo.delete_binding(binding_id):
            raise LookupError("extraction binding not found")

    def _source(self, data_source_id: str) -> DataSourceRow:
        """The folder source a binding names, or a refusal that says why not."""
        source = cast(
            DataSourceRow | None,
            self._services.data_sources_repo.get(data_source_id),
        )
        if source is None:
            raise LookupError("data source not found")
        if not is_folder_kind(source.kind):
            # §7.4's tree is source → folder → file, and only a folder source has
            # such a path. A url or upload source holds one document with no
            # tree to bind into.
            raise ValueError(
                f"a binding needs a folder source; {source.kind!r} has no folder to bind into"
            )
        return source

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, *, data_source_id: str, path: str, content_type: str = "") -> tuple[Any, str]:
        """Which template applies to one path inside a source (design §7.4).

        Returns the match and the content type it was decided with, because a
        caller that did not supply one should see the one that was used.
        """
        source = self._source(data_source_id)
        cleaned = _binding_path(path)
        if not cleaned:
            raise ValueError("a path inside the source is required")
        resolved_type = content_type.strip() or (knowledge_content_type(_suffix(cleaned)) or "")
        match = resolve_template(
            self._repo.candidates_for_matching(),
            data_source_id=source.id,
            path=cleaned,
            content_type=resolved_type,
        )
        return match, resolved_type


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def _name(raw: str) -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        raise ValueError("a template needs a name")
    if len(cleaned) > MAX_NAME:
        raise ValueError(f"a template name may be at most {MAX_NAME} characters")
    return cleaned


def _bounded(raw: str, limit: int, what: str) -> str:
    cleaned = (raw or "").strip()
    if len(cleaned) > limit:
        raise ValueError(f"the {what} may be at most {limit} characters")
    return cleaned


def _applies_to(raw: str) -> str:
    """The template's declared file types, stored as typed and validated by the matcher."""
    cleaned = _bounded(raw, MAX_DESCRIPTION, "applies_to")
    return cleaned


def _fields_json(fields: object) -> str:
    """Validated fields as the JSON the version row stores."""
    parsed = parse_fields(fields)
    return json.dumps([field.payload() for field in parsed], ensure_ascii=False)


def _binding_path(raw: str) -> str:
    """A source-relative path, as §7.4's tree means it.

    The rules are the ones a path inside a share has to obey for matching to
    mean anything: ``/``-separated, relative, and free of ``..`` segments — a
    binding that could climb out of its source would match files the
    administrator never pointed at.
    """
    cleaned = (raw or "").strip().replace("\\", "/").strip("/")
    if not cleaned:
        return ""
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("a binding path may not contain '..'")
    return "/".join(parts)


def _compilable(pattern: str) -> str:
    """The regular expression as stored, refused when it cannot compile.

    Refused here rather than at match time: a pattern that never matches would
    leave a binding that looks configured and does nothing, which is the failure
    mode the whole feature is meant to avoid.
    """
    cleaned = (pattern or "").strip()
    if not cleaned:
        return ""
    try:
        re.compile(cleaned)
    except re.error as exc:
        raise ValueError(f"invalid match_regex: {exc}") from exc
    return cleaned


# ``STATUS_DISABLED`` is re-exported for the API layer, which must not have to
# reach into the repo module for a status name.
__all__ = [
    "ExtractTemplateService",
    "STATUS_DISABLED",
    "TemplateInUse",
]
