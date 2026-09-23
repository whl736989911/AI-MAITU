"""What an extraction template may say, and which one applies to a file.

Two pure decisions live here, so both can be reasoned about (and tested) without
a database or a model:

* **What a template's fields may be** (design §7.1). A field is a name, a type, a
  required flag and an instruction for whatever extracts it; an enumeration also
  needs its options. Validation is strict about the things that would otherwise
  turn into a silent no-op later — a duplicate name, an unknown type, an
  enumeration with nothing to enumerate.

* **Which bound template applies to a file** (design §7.4). Bindings name a
  source, a folder inside it, or one file, and the most specific match wins.
  Two *different* templates matching at the same level are a conflict, and the
  design is explicit that this is reported rather than resolved: two rules
  claiming one file is a mistake to fix, not a coin to flip.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

FIELD_TYPES = ("text", "string[]", "date", "number", "amount", "boolean", "enum")
"""What a field may be extracted as (design §7.1's list, with its examples).

``amount`` is its own type rather than a number because a contract's 金额 is
money: whoever consumes the result should not have to re-read the instruction
to know whether ``1200`` means yuan or a count of something.
"""

MAX_FIELDS = 100
"""Bound on one template's field list; a wall of fields is not a template."""

MAX_FIELD_NAME = 64
MAX_INSTRUCTION = 2000

LEVEL_FILE = "file"
LEVEL_FOLDER = "folder"
LEVEL_SOURCE = "source"
_LEVEL_RANK = {LEVEL_FILE: 3, LEVEL_FOLDER: 2, LEVEL_SOURCE: 1}


@dataclass(frozen=True)
class TemplateField:
    """One field a template asks for (design §7.1)."""

    name: str
    type: str = "text"
    required: bool = False
    instruction: str = ""
    options: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "instruction": self.instruction,
        }
        if self.options:
            out["options"] = list(self.options)
        return out


@dataclass(frozen=True)
class BindingCandidate:
    """A binding joined with the template it points at, as the matcher sees it.

    ``applies_to`` is the template's own declaration of the file types it is for
    (design §7.1). It is carried here rather than looked up so the matcher stays
    a function of its arguments.
    """

    binding_id: str
    template_id: str
    data_source_id: str
    path: str = ""
    extension: str = ""
    mime_type: str = ""
    name_pattern: str = ""
    match_regex: str = ""
    applies_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateMatch:
    """What applies to one file, and what stopped it from being one answer."""

    template_id: str | None = None
    binding_id: str | None = None
    level: str = ""
    conflicts: tuple[str, ...] = ()

    @property
    def conflicted(self) -> bool:
        return bool(self.conflicts)


def parse_fields(raw: object) -> tuple[TemplateField, ...]:
    """Validate a template's field list (design §7.1).

    Raises ``ValueError`` naming the field at fault: a template that cannot be
    carried out is worse than one that is refused, because its failures would
    only appear later, per file, in an extraction nobody is watching.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("fields must be a list")
    if len(raw) > MAX_FIELDS:
        raise ValueError(f"a template may define at most {MAX_FIELDS} fields")
    fields: list[TemplateField] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each field must be an object")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ValueError("a field needs a name")
        if len(name) > MAX_FIELD_NAME or any(char.isspace() for char in name):
            raise ValueError(f"invalid field name {name!r}")
        if name in seen:
            raise ValueError(f"duplicate field name {name!r}")
        seen.add(name)
        kind = str(entry.get("type") or "text").strip().lower()
        if kind not in FIELD_TYPES:
            raise ValueError(
                f"field {name!r} has unknown type {kind!r}; expected one of {FIELD_TYPES}"
            )
        options = tuple(
            str(option).strip() for option in (entry.get("options") or []) if str(option).strip()
        )
        if kind == "enum" and not options:
            raise ValueError(f"field {name!r} is an enum and needs its options")
        instruction = str(entry.get("instruction") or "").strip()
        if len(instruction) > MAX_INSTRUCTION:
            raise ValueError(f"field {name!r} has an instruction that is too long")
        fields.append(
            TemplateField(
                name=name,
                type=kind,
                required=bool(entry.get("required")),
                instruction=instruction,
                options=options,
            )
        )
    return tuple(fields)


def normalize_types(raw: str | Iterable[str]) -> tuple[str, ...]:
    """Declared file types as lower-case extensions (``pdf``, ``.docx`` → ``.pdf``).

    A template that declares the types it is for (design §7.1) does so the way a
    person writes them, so both spellings land in one form here.
    """
    parts = re.split(r"[,\s]+", raw) if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for part in parts:
        cleaned = str(part).strip().lower()
        if not cleaned:
            continue
        if not cleaned.startswith("."):
            cleaned = f".{cleaned}"
        if cleaned not in out:
            out.append(cleaned)
    return tuple(out)


def resolve_template(
    candidates: Iterable[BindingCandidate],
    *,
    data_source_id: str,
    path: str,
    content_type: str = "",
) -> TemplateMatch:
    """Which template applies to one file inside a source (design §7.4).

    Candidates for other sources are ignored here rather than by the caller, so
    the rule "a binding only ever reaches its own source" cannot be forgotten at
    one call site.
    """
    filename = path.rsplit("/", 1)[-1]
    matched: list[tuple[int, BindingCandidate]] = []
    for candidate in candidates:
        if candidate.data_source_id != data_source_id:
            continue
        level = _level_for(candidate.path, path)
        if level is None:
            continue
        if not _applies_to(candidate.applies_to, filename):
            continue
        if not _conditions_match(
            candidate,
            path=path,
            filename=filename,
            content_type=content_type,
        ):
            continue
        matched.append((_LEVEL_RANK[level], candidate))
    if not matched:
        return TemplateMatch()
    best = max(rank for rank, _ in matched)
    winners = [candidate for rank, candidate in matched if rank == best]
    # One template claiming a file twice — a folder rule and a file rule for the
    # same template, say — is one answer, not a conflict.
    templates = sorted({candidate.template_id for candidate in winners})
    if len(templates) > 1:
        return TemplateMatch(
            level=_level_name(best),
            conflicts=tuple(templates),
        )
    chosen = winners[0]
    return TemplateMatch(
        template_id=chosen.template_id,
        binding_id=chosen.binding_id,
        level=_level_name(best),
    )


def _level_name(rank: int) -> str:
    for level, value in _LEVEL_RANK.items():
        if value == rank:
            return level
    raise ValueError(f"unknown level rank {rank}")


def _level_for(binding_path: str, path: str) -> str | None:
    """How specifically a binding's path covers *path*, or ``None`` if at all not.

    An empty binding path is the whole source; a prefix of the file's path is a
    folder it sits in; the file's own path is that file.
    """
    bound = binding_path.strip("/")
    if not bound:
        return LEVEL_SOURCE
    if bound == path:
        return LEVEL_FILE
    if path.startswith(f"{bound}/"):
        return LEVEL_FOLDER
    return None


def _applies_to(declared: tuple[str, ...], filename: str) -> bool:
    if not declared:
        return True
    return filename.lower().endswith(declared)


def _conditions_match(
    candidate: BindingCandidate, *, path: str, filename: str, content_type: str
) -> bool:
    if candidate.extension:
        wanted = normalize_types(candidate.extension)
        if not filename.lower().endswith(wanted):
            return False
    if candidate.mime_type and not _mime_matches(candidate.mime_type, content_type):
        return False
    if candidate.name_pattern and not fnmatchcase(filename, candidate.name_pattern):
        return False
    # Matched against the file's path inside the source, so a rule can aim at a
    # folder's naming as well as one file's. Patterns are validated when they are
    # stored, which is why nothing here guards against a bad one.
    return not (candidate.match_regex and re.search(candidate.match_regex, path) is None)


def _mime_matches(pattern: str, content_type: str) -> bool:
    wanted = content_type.split(";")[0].strip().lower()
    rule = pattern.strip().lower()
    if rule.endswith("/*"):
        return wanted.startswith(rule[:-1])
    return wanted == rule
