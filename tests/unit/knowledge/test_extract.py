"""Unit tests for the extraction prompt and reply validation (design §7.1)."""

from __future__ import annotations

import pytest

from octop.infra.knowledge.extract import build_messages, parse_reply, validate_payload
from octop.infra.knowledge.template_match import parse_fields

_FIELDS = parse_fields(
    [
        {
            "name": "summary",
            "type": "text",
            "required": True,
            "instruction": "用不超过300字总结合同核心内容",
        },
        {"name": "keywords", "type": "string[]", "required": True, "instruction": "5 到 15 个"},
        {"name": "kind", "type": "enum", "options": ["lease", "service"]},
        {"name": "amount", "type": "amount"},
    ]
)


def test_the_prompt_carries_every_field_and_its_instruction() -> None:
    """The template is the question; the prompt has to ask all of it."""
    messages = build_messages(
        fields=_FIELDS,
        instruction="重点关注合同主体、期限、金额、付款和违约责任。",
        title="采购合同",
        text="合同正文……",
    )

    body = str(messages[1].content)
    assert "- summary (text, required): 用不超过300字总结合同核心内容" in body
    assert "- keywords (array of strings, required)" in body
    assert "One of: lease, service." in body
    assert "重点关注合同主体、期限、金额、付款和违约责任。" in body
    assert "采购合同" in body
    assert "合同正文……" in body


def test_a_long_document_is_truncated_with_a_note() -> None:
    messages = build_messages(fields=_FIELDS, instruction="", title="", text="x" * 70_000)

    body = str(messages[1].content)
    assert "[document truncated]" in body
    assert len(body) < 70_000


def test_parse_reply_keeps_the_declared_fields_in_order() -> None:
    reply = '{"amount": 1200, "summary": "一份合同", "kind": "lease", "keywords": ["甲", "乙"]}'

    parsed = parse_reply(reply, _FIELDS)

    assert list(parsed) == ["summary", "keywords", "kind", "amount"]


def test_parse_reply_accepts_a_fenced_block() -> None:
    """Models add a code fence however the prompt asks them not to."""
    reply = '```json\n{"summary": "一份合同", "keywords": ["甲"]}\n```'

    assert parse_reply(reply, _FIELDS) == {"summary": "一份合同", "keywords": ["甲"]}


def test_parse_reply_turns_a_written_list_into_a_list() -> None:
    """A model answering a list question with a line of text is still an answer."""
    reply = '{"summary": "s", "keywords": "采购、付款, 期限"}'

    assert parse_reply(reply, _FIELDS)["keywords"] == ["采购", "付款", "期限"]


def test_a_missing_required_field_is_an_error() -> None:
    with pytest.raises(ValueError, match="summary"):
        parse_reply('{"keywords": ["甲"]}', _FIELDS)


def test_an_off_enumeration_value_is_an_error() -> None:
    """A value outside the enum is a wrong answer, not a new option."""
    with pytest.raises(ValueError, match="kind"):
        parse_reply('{"summary": "s", "keywords": ["甲"], "kind": "purchase"}', _FIELDS)


def test_undeclared_keys_are_dropped() -> None:
    """The template defines the shape, so a volunteered key is not part of it."""
    reply = '{"summary": "s", "keywords": ["甲"], "extra": "surprise"}'

    assert "extra" not in parse_reply(reply, _FIELDS)


def test_a_reply_that_is_not_json_is_an_error() -> None:
    with pytest.raises(ValueError, match="not JSON"):
        parse_reply("I could not find the fields.", _FIELDS)
    with pytest.raises(ValueError, match="nothing"):
        parse_reply("   ", _FIELDS)
    with pytest.raises(ValueError, match="JSON object"):
        validate_payload(["not", "an", "object"], _FIELDS)


def test_an_optional_field_may_be_absent() -> None:
    """``amount`` is not required, so leaving it out is a valid answer."""
    parsed = parse_reply('{"summary": "s", "keywords": ["甲"]}', _FIELDS)

    assert "amount" not in parsed
