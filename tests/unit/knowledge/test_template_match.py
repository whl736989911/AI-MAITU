"""Unit tests for extraction-template fields and binding resolution (design §7)."""

from __future__ import annotations

import pytest

from octop.infra.knowledge.template_match import (
    LEVEL_FILE,
    LEVEL_FOLDER,
    LEVEL_SOURCE,
    BindingCandidate,
    normalize_types,
    parse_fields,
    resolve_template,
)

_SOURCE = "src1"


def _binding(
    binding_id: str,
    template_id: str,
    *,
    path: str = "",
    data_source_id: str = _SOURCE,
    **conditions: str,
) -> BindingCandidate:
    return BindingCandidate(
        binding_id=binding_id,
        template_id=template_id,
        data_source_id=data_source_id,
        path=path,
        **conditions,
    )


def test_fields_must_be_carriable_out() -> None:
    """A field a later step cannot honour is refused while it is being written."""
    assert [field.name for field in parse_fields([{"name": "summary"}])] == ["summary"]
    assert parse_fields(None) == ()

    with pytest.raises(ValueError, match="needs a name"):
        parse_fields([{"name": "  "}])
    with pytest.raises(ValueError, match="duplicate field name"):
        parse_fields([{"name": "a"}, {"name": "a"}])
    with pytest.raises(ValueError, match="unknown type"):
        parse_fields([{"name": "a", "type": "money"}])
    with pytest.raises(ValueError, match="needs its options"):
        parse_fields([{"name": "a", "type": "enum"}])
    with pytest.raises(ValueError, match="at most"):
        parse_fields([{"name": f"f{index}"} for index in range(101)])


def test_a_field_keeps_its_instruction_and_options() -> None:
    fields = parse_fields(
        [
            {"name": "keywords", "type": "string[]", "required": True, "instruction": "5 to 15"},
            {"name": "kind", "type": "enum", "options": ["lease", "service"]},
        ]
    )

    assert fields[0].required is True
    assert fields[0].instruction == "5 to 15"
    assert fields[1].options == ("lease", "service")
    # Options only travel for an enumeration: a text field has nothing to pick.
    assert "options" not in fields[0].payload()


def test_normalize_types_accepts_both_spellings() -> None:
    assert normalize_types("pdf, .DOCX  xlsx") == (".pdf", ".docx", ".xlsx")
    assert normalize_types("") == ()


def test_the_most_specific_binding_wins() -> None:
    """design §7.4: file level beats folder level beats source level."""
    candidates = [
        _binding("b-source", "t-source"),
        _binding("b-folder", "t-folder", path="legal"),
        _binding("b-file", "t-file", path="legal/contract.pdf"),
    ]

    match = resolve_template(
        candidates,
        data_source_id=_SOURCE,
        path="legal/contract.pdf",
        content_type="application/pdf",
    )
    assert (match.template_id, match.level) == ("t-file", LEVEL_FILE)

    # The same file one level up: the folder rule is now the most specific one.
    other = resolve_template(
        candidates,
        data_source_id=_SOURCE,
        path="legal/notes.pdf",
        content_type="application/pdf",
    )
    assert (other.template_id, other.level) == ("t-folder", LEVEL_FOLDER)

    # And a file elsewhere in the source falls to the source-wide rule.
    elsewhere = resolve_template(
        candidates, data_source_id=_SOURCE, path="handbook.pdf", content_type="application/pdf"
    )
    assert (elsewhere.template_id, elsewhere.level) == ("t-source", LEVEL_SOURCE)


def test_two_templates_at_one_level_are_a_conflict_not_a_choice() -> None:
    """design §7.4: report the clash rather than pick one at random."""
    candidates = [
        _binding("b1", "t-one", path="legal/contract.pdf"),
        _binding("b2", "t-two", path="legal/contract.pdf"),
    ]

    match = resolve_template(
        candidates,
        data_source_id=_SOURCE,
        path="legal/contract.pdf",
        content_type="application/pdf",
    )

    assert match.template_id is None
    assert match.conflicted
    assert match.conflicts == ("t-one", "t-two")
    assert match.level == LEVEL_FILE


def test_one_template_reaching_a_file_twice_is_a_single_answer() -> None:
    """A folder rule and a file rule for the same template agree, so it applies."""
    candidates = [
        _binding("b-folder", "t-one", path="legal"),
        _binding("b-file", "t-one", path="legal/contract.pdf"),
        _binding("b-other", "t-two", path="legal"),
    ]

    match = resolve_template(
        candidates,
        data_source_id=_SOURCE,
        path="legal/contract.pdf",
        content_type="application/pdf",
    )

    assert (match.template_id, match.level) == ("t-one", LEVEL_FILE)
    assert not match.conflicted


def test_a_binding_only_reaches_its_own_source() -> None:
    candidates = [_binding("b1", "t-one", path="", data_source_id="other")]

    match = resolve_template(
        candidates, data_source_id=_SOURCE, path="a.pdf", content_type="application/pdf"
    )

    assert match.template_id is None


@pytest.mark.parametrize(
    ("conditions", "path", "content_type", "expected"),
    [
        ({"extension": "pdf"}, "a.pdf", "application/pdf", True),
        ({"extension": "pdf"}, "a.txt", "text/plain", False),
        ({"mime_type": "image/*"}, "a.png", "image/png", True),
        ({"mime_type": "image/*"}, "a.pdf", "application/pdf", False),
        ({"mime_type": "image/png"}, "a.png", "image/png", True),
        ({"name_pattern": "invoice-*.pdf"}, "invoice-2026.pdf", "", True),
        ({"name_pattern": "invoice-*.pdf"}, "contract.pdf", "", False),
        ({"match_regex": r"^legal/\d{4}"}, "legal/2026/x.pdf", "", True),
        ({"match_regex": r"^legal/\d{4}"}, "other/2026/x.pdf", "", False),
    ],
)
def test_conditions_filter_the_files_a_binding_covers(
    conditions: dict[str, str], path: str, content_type: str, expected: bool
) -> None:
    """Each condition narrows what a binding covers, on its own."""
    candidate = _binding("b1", "t-one", **conditions)

    match = resolve_template(
        [candidate], data_source_id=_SOURCE, path=path, content_type=content_type
    )

    assert (match.template_id is not None) is expected


def test_a_template_only_applies_to_the_types_it_declares() -> None:
    """design §7.1's ``applies_to``: a contract template does not read a spreadsheet."""
    contract = BindingCandidate(
        binding_id="b1",
        template_id="t-contract",
        data_source_id=_SOURCE,
        applies_to=normalize_types("doc, docx, pdf"),
    )
    candidates = [contract]

    assert (
        resolve_template(
            candidates, data_source_id=_SOURCE, path="a.docx", content_type=""
        ).template_id
        == "t-contract"
    )
    assert (
        resolve_template(
            candidates, data_source_id=_SOURCE, path="a.xlsx", content_type=""
        ).template_id
        is None
    )
