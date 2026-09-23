"""Applying and undoing one improvement to a feature's workflow, item by item.

An improvement is a *diff*, not a new document: a list of items that each name one
place in the definition (or in the caller's overlay) and what that place said
before and says after. That shape is what makes the two promises of this feature
keepable:

* **Nothing is applied blindly.** An item carries the value it expected to find.
  If the document moved — somebody edited the same step in the editor while the
  assistant was summarising — the batch is refused whole rather than overwriting
  an edit nobody meant to lose. The same check runs on the way back.
* **Every applied change can be undone.** Reverting is the same list in reverse:
  put back what was there, and drop what was added. There is no second code path
  that "knows" how to undo a particular kind of change.

Paths are the ones an editor and a model can both be told about: ``/rules/2``,
``/steps/1/gate``, ``/inputs/properties/customer_name/title/zh``. ``-`` in place of
a list index means "append", and is resolved against the document when the batch is
applied, so what is recorded is always the index it actually landed on.

The module is pure: it neither reads nor writes anything, which is what lets the
API validate the result and the tests pin the semantics exactly.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

MISSING = object()
"""The path does not exist — distinct from a path whose value is ``null``."""

_APPEND = "-"
"""A list index that means "one past the end"."""


class ChangeConflictError(Exception):
    """The document no longer says what the change expected.

    Carries the paths that mismatched, in the order they were examined, so the
    refusal can name them instead of saying "the change could not be applied".
    """

    def __init__(self, paths: Sequence[str]) -> None:
        self.paths: tuple[str, ...] = tuple(paths)
        super().__init__(", ".join(self.paths))


def parse_path(path: str) -> list[str]:
    """``/steps/1/gate`` → ``["steps", "1", "gate"]``; ``""`` is the document itself."""
    if not isinstance(path, str):
        raise ValueError("a change path must be a string")
    trimmed = path.strip()
    if not trimmed:
        return []
    if not trimmed.startswith("/"):
        raise ValueError(f"a change path must start with '/': {path!r}")
    return list(trimmed.split("/")[1:])


def _resolve(document: Any, parts: Sequence[str], *, append_lists: bool) -> tuple[Any, str]:
    """Walk *parts* from *document*, returning ``(container, key)`` for the last hop.

    *append_lists* lets a ``-`` in the final position resolve to the current length:
    the batch is applied against the document it was built from, so "append to
    rules" lands on the index that append means *now* and is recorded as that index.
    """
    node = document
    for index, part in enumerate(parts[:-1]):
        node = _step(node, part, parts=parts, index=index)
    return node, parts[-1]


def _step(node: Any, part: str, *, parts: Sequence[str], index: int) -> Any:
    if isinstance(node, list):
        position = _list_index(node, part, parts=parts, index=index)
        return node[position]
    if isinstance(node, Mapping):
        if part not in node:
            raise ValueError(f"no such field: {'/'.join(parts[: index + 1])}")
        return node[part]
    raise ValueError(f"{'/' + '/'.join(parts[:index]) or 'document'} is not an object or array")


def _list_index(
    node: list[Any],
    part: str,
    *,
    parts: Sequence[str],
    index: int,
    allow_end: bool = False,
) -> int:
    if not part.isdigit():
        raise ValueError(f"{'/' + '/'.join(parts[:index])} takes a numeric index, not {part!r}")
    position = int(part)
    limit = len(node) if allow_end else len(node) - 1
    if position > limit:
        raise ValueError(f"index {position} is past the end of {'/' + '/'.join(parts[:index])}")
    return position


def get_at(document: Any, path: str) -> Any:
    """The value at *path*, or :data:`MISSING` when nothing is there."""
    parts = parse_path(path)
    if not parts:
        return document
    node: Any = document
    for index, part in enumerate(parts):
        if isinstance(node, list):
            if part == _APPEND and index == len(parts) - 1:
                return MISSING
            if not part.isdigit():
                return MISSING
            position = int(part)
            if position >= len(node):
                return MISSING
            node = node[position]
            continue
        if not isinstance(node, Mapping) or part not in node:
            return MISSING
        node = node[part]
    return node


def set_at(document: Any, path: str, value: Any, *, allow_new: bool = False) -> None:
    """Write *value* at *path* in *document* (mutating it; callers pass a copy).

    Two allowances, both of them about replaying a recorded change rather than about
    being lenient:

    * a list index one past the end appends, so a change recorded under the index an
      append landed on still applies after the list was reverted to its shorter self;
    * *allow_new* lets a missing mapping key be written. It is set only for an item
      whose ``before`` is ``null`` — the item itself says "nothing was there" — so a
      typo in a path is still refused (or caught as a conflict) rather than silently
      creating a field nobody meant to add.
    """
    parts = parse_path(path)
    if not parts:
        raise ValueError("cannot assign the whole document through a change item")
    container, key = _resolve(document, parts, append_lists=True)
    if isinstance(container, list):
        if key == _APPEND:
            container.append(value)
            return
        position = _list_index(container, key, parts=parts, index=len(parts) - 1, allow_end=True)
        if position == len(container):
            container.append(value)
            return
        container[position] = value
        return
    if not isinstance(container, MutableMapping):
        raise ValueError(f"{'/' + '/'.join(parts[:-1]) or 'document'} is not an object or array")
    if key not in container and not allow_new:
        raise ValueError(f"no such field: {path}")
    container[key] = value


def delete_at(document: Any, path: str) -> None:
    """Remove what *path* points at — the inverse of appending to a list."""
    parts = parse_path(path)
    if not parts:
        raise ValueError("cannot delete the whole document")
    container, key = _resolve(document, parts, append_lists=False)
    if isinstance(container, list):
        position = _list_index(container, key, parts=parts, index=len(parts) - 1)
        del container[position]
        return
    if isinstance(container, MutableMapping) and key in container:
        del container[key]
        return
    raise ValueError(f"no such field: {path}")


def _matches(current: Any, expected: Any) -> bool:
    """Whether the document still says *expected* at a path.

    ``MISSING`` matches an expected ``null``: a batch that adds a field records
    ``before = null``, and "nothing was there" is what that means.
    """
    if current is MISSING:
        return expected is None
    return bool(current == expected)


def resolve_items(
    document: Any,
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The same batch with every ``-`` replaced by the index that append lands on.

    Two appends to one list in a single batch must not be recorded under the same
    path, or undo could not tell them apart and would delete the wrong element.
    Resolution walks the batch in order, so the second append sees the list the
    first one already grew.
    """
    working: dict[str, Any] = copy.deepcopy(document)
    resolved: list[dict[str, Any]] = []
    for item in items:
        path = str(item.get("path") or "")
        parts = parse_path(path)
        final = path
        if parts and parts[-1] == _APPEND:
            container, _key = _resolve(working, parts, append_lists=False)
            length = len(container) if isinstance(container, list) else 0
            final = "/" + "/".join([*parts[:-1], str(length)])
        set_at(
            working,
            final,
            copy.deepcopy(item.get("after")),
            allow_new=item.get("before") is None,
        )
        resolved.append({**item, "path": final})
    return resolved


def validate_items(items: Any) -> list[str]:
    """Every problem with a batch of items, in the order they appear. Pure."""
    problems: list[str] = []
    if not isinstance(items, list) or not items:
        return ["items must be a non-empty array"]
    for index, item in enumerate(items):
        where = f"items[{index}]"
        if not isinstance(item, Mapping):
            problems.append(f"{where} must be an object")
            continue
        unknown = sorted(set(item) - {"path", "before", "after"})
        if unknown:
            problems.append(f"{where} has unsupported keys: {', '.join(unknown)}")
        if "after" not in item:
            problems.append(f"{where}.after is required")
        try:
            parse_path(str(item.get("path") or ""))
        except ValueError as exc:
            problems.append(f"{where}.path {exc}")
    return problems


def apply_items(document: Any, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """*document* with every item applied, or :class:`ChangeConflictError`.

    Applied in order, on a copy: a batch that conflicts anywhere leaves the caller's
    document exactly as it was, and the refusal names every path that moved.
    """
    working: dict[str, Any] = copy.deepcopy(document)
    stale: list[str] = []
    for item in items:
        path = str(item.get("path") or "")
        expected = item.get("before")
        if not _matches(get_at(working, path), expected):
            stale.append(path)
    if stale:
        raise ChangeConflictError(stale)
    for item in items:
        path = str(item.get("path") or "")
        set_at(
            working,
            path,
            copy.deepcopy(item.get("after")),
            allow_new=item.get("before") is None,
        )
    return working


def revert_items(
    document: Any,
    items: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Undo every item, newest first, and report the ones that cannot be undone.

    Reverting is not forced. An item whose ``after`` value has since been edited
    somewhere else is reported back instead of overwritten — the caller decides
    whether to drop the newer edit, because only they know which one they meant.

    An item that *added* something (``before`` is ``null``) removes it rather than
    writing a null back: "nothing was there" is what the change recorded, and undo
    is the statement of the document as it was. For a list that means dropping the
    element the append created; for a field it means dropping the field.
    """
    working: dict[str, Any] = copy.deepcopy(document)
    conflicts: list[str] = []
    for item in reversed(list(items)):
        path = str(item.get("path") or "")
        if not _matches(get_at(working, path), item.get("after")):
            conflicts.append(path)
            continue
        before = item.get("before")
        if before is None and get_at(working, path) is not MISSING:
            delete_at(working, path)
            continue
        set_at(working, path, copy.deepcopy(before))
    return working, tuple(conflicts)


__all__ = [
    "ChangeConflictError",
    "apply_items",
    "delete_at",
    "MISSING",
    "get_at",
    "parse_path",
    "resolve_items",
    "revert_items",
    "set_at",
    "validate_items",
]
