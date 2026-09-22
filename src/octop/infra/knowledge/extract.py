"""Turning a document into the fields a template asks for (design §7.1, §12.6).

Two halves again, and for the same reason as search: what the model is *told*
and what its reply is *allowed to mean* are pure functions, so they can be
tested and argued with without a model in the loop. Only :func:`run_extraction`
calls anything.

The reply is validated against the template rather than trusted: a field the
template marked required and the model left empty is a failed extraction, not a
result with a hole in it, and an enumeration's value that is not one of its
options is a wrong answer rather than a new option.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from octop.infra.knowledge.template_match import TemplateField
from octop.infra.utils.llm_text import ainvoke_text

DEFAULT_EXTRACT_TIMEOUT = 120.0
MAX_DOCUMENT_CHARS = 60_000
"""How much of a document is sent. A long file is truncated with a note rather
than refused: an answer from most of a contract beats no answer, and the note
tells the model what it is looking at."""

_SYSTEM = (
    "You extract structured fields from one document. Reply with a single JSON "
    "object and nothing else — no prose, no code fence. Use exactly the field "
    "names given. When a field cannot be determined from the document, leave it "
    "out rather than guessing."
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def build_messages(
    *,
    fields: Sequence[TemplateField],
    instruction: str,
    title: str,
    text: str,
) -> list[Any]:
    """The messages one extraction sends (design §7.1's template, as a prompt).

    Every field carries its own instruction, because "extract the date" and
    "extract the effective date" are different questions, and the template is
    where a person decided which one they meant.
    """
    lines = ["Fields to extract (JSON object with exactly these keys):"]
    for field in fields:
        kind = "array of strings" if field.type == "string[]" else field.type
        detail = f"- {field.name} ({kind}{', required' if field.required else ''})"
        if field.instruction:
            detail += f": {field.instruction}"
        if field.type == "enum" and field.options:
            detail += f" One of: {', '.join(field.options)}."
        lines.append(detail)
    if instruction.strip():
        lines.append("")
        lines.append(f"Overall instruction: {instruction.strip()}")
    body = text.strip()
    if len(body) > MAX_DOCUMENT_CHARS:
        body = body[:MAX_DOCUMENT_CHARS] + "\n\n[document truncated]"
    return [
        SystemMessage(content=_SYSTEM),
        HumanMessage(
            content=("\n".join(lines) + f"\n\nDocument: {title or 'untitled'}\n---\n{body}\n---")
        ),
    ]


def parse_reply(reply: str, fields: Sequence[TemplateField]) -> dict[str, Any]:
    """The model's reply as the fields the template declared.

    Accepts a fenced block because models add one no matter what the prompt says,
    and validates the result — see the module docstring for why a missing
    required field and an off-enumeration value are errors rather than data.
    """
    return validate_payload(_decode(reply), fields)


def validate_payload(payload: Any, fields: Sequence[TemplateField]) -> dict[str, Any]:
    """Keep the declared fields, in their declared order, and check them.

    Keys the template did not declare are dropped: the template defines the
    shape of the answer, so anything else the model volunteered is not part of
    it and must not appear to be.
    """
    if not isinstance(payload, dict):
        raise ValueError("the model did not answer with a JSON object")
    out: dict[str, Any] = {}
    for field in fields:
        value = payload.get(field.name)
        if field.type == "string[]":
            value = _as_list(value)
        if _is_empty(value):
            if field.required:
                raise ValueError(f"field {field.name!r} is required and came back empty")
            continue
        if (
            field.type == "enum"
            and isinstance(value, str)
            and field.options
            and value not in field.options
        ):
            raise ValueError(f"field {field.name!r} is {value!r}, which is not one of its options")
        out[field.name] = value
    return out


def _decode(reply: str) -> Any:
    text = (reply or "").strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text:
        raise ValueError("the model returned nothing")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"the model's reply is not JSON: {exc}") from exc


def _as_list(value: Any) -> list[str]:
    """A ``string[]`` field from either a list or the text a model wrote instead.

    Models answer a list question with a comma- or newline-separated line often
    enough that refusing would make the feature brittle; the split applies only
    to a string, so a real list is never reshaped.
    """
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n;、]+", value) if part.strip()]
    return []


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


async def run_extraction(
    *,
    llm: Any,
    fields: Sequence[TemplateField],
    instruction: str,
    title: str,
    text: str,
    timeout: float | None = DEFAULT_EXTRACT_TIMEOUT,
) -> dict[str, Any]:
    """Ask the model for the fields, and return them validated.

    ``llm`` is a chat model the caller built; this module never decides which
    model a deployment extracts with — design §7.3 puts that in the result's
    provenance, not in the extractor.
    """
    reply = await ainvoke_text(
        llm,
        build_messages(fields=fields, instruction=instruction, title=title, text=text),
        timeout=timeout,
    )
    return parse_reply(reply, fields)
