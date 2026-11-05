"""Metadata schema/value validation shared by any knowledge-base field set."""

from __future__ import annotations

import pytest

from octop.infra.knowledge.metadata import (
    MetadataError,
    metadata_matches,
    normalize_schema,
    normalize_values,
    validate_suggested_values,
)

SCHEMA = normalize_schema(
    [
        {"key": "drawing_no", "label": "Drawing no.", "type": "text", "required": True},
        {"key": "material", "label": "Material", "type": "text"},
        {"key": "sheet", "label": "Sheet", "type": "enum", "options": ["A0", "A1"]},
        {"key": "issue_date", "label": "Issued", "type": "date"},
        {"key": "weight", "label": "Weight", "type": "number"},
    ]
)


def test_schema_normalizes_defaults_and_enum_options() -> None:
    assert SCHEMA[0]["searchable"] is True
    assert SCHEMA[0]["required"] is True
    assert SCHEMA[2]["options"] == ["A0", "A1"]
    assert [field["key"] for field in SCHEMA] == [
        "drawing_no",
        "material",
        "sheet",
        "issue_date",
        "weight",
    ]


@pytest.mark.parametrize(
    "field",
    [
        {"key": "1bad", "type": "text"},
        {"key": "ok", "type": "unsupported"},
        {"key": "ok", "type": "enum"},
        {"key": "ok", "type": "enum", "options": []},
    ],
)
def test_schema_rejects_invalid_fields(field: dict) -> None:
    with pytest.raises(MetadataError):
        normalize_schema([field])


def test_values_merge_validate_and_clear() -> None:
    values = normalize_values(
        SCHEMA,
        {
            "drawing_no": " A-102 ",
            "sheet": "A0",
            "issue_date": "2026-01-31",
            "weight": 12.5,
        },
    )
    assert values == {
        "drawing_no": "A-102",
        "sheet": "A0",
        "issue_date": "2026-01-31",
        "weight": 12.5,
    }

    merged = normalize_values(SCHEMA, {"material": "Q235", "weight": None}, existing=values)
    assert merged["material"] == "Q235"
    assert "weight" not in merged
    assert merged["drawing_no"] == "A-102"


@pytest.mark.parametrize(
    "payload",
    [
        {"material": "Q235"},
        {"drawing_no": "A-1", "sheet": "A9"},
        {"drawing_no": "A-1", "issue_date": "2026/01/01"},
        {"drawing_no": "A-1", "weight": "heavy"},
        {"drawing_no": "A-1", "missing": "x"},
    ],
)
def test_values_reject_invalid_payloads(payload: dict) -> None:
    with pytest.raises(MetadataError):
        normalize_values(SCHEMA, payload)


def test_filters_match_text_number_and_case() -> None:
    values = {"drawing_no": "A-102", "weight": 12.5, "material": "Q235"}
    assert metadata_matches(values, {"drawing_no": "a-102"})
    assert metadata_matches(values, {"material": "q23"})
    assert metadata_matches(values, {"weight": 12.5})
    assert not metadata_matches(values, {"weight": 13})
    assert not metadata_matches(values, {"sheet": "A0"})
    assert metadata_matches(values, {})


def test_suggested_values_keep_only_known_and_valid_fields() -> None:
    assert validate_suggested_values(
        SCHEMA,
        {
            "drawing_no": "A-102",
            "sheet": "A9",
            "weight": "heavy",
            "unknown": "x",
            "issue_date": "2026-01-31",
        },
    ) == {"drawing_no": "A-102", "issue_date": "2026-01-31"}
    assert validate_suggested_values(SCHEMA, None) == {}


def test_suggested_numbers_accept_quoted_decimals_only() -> None:
    assert validate_suggested_values(SCHEMA, {"weight": "12.5"}) == {"weight": 12.5}
    assert validate_suggested_values(SCHEMA, {"weight": "12"}) == {"weight": 12}
    assert validate_suggested_values(SCHEMA, {"weight": "NaN"}) == {}
    assert validate_suggested_values(SCHEMA, {"weight": "12kg"}) == {}


def test_dates_must_be_real_calendar_days() -> None:
    assert (
        normalize_values(SCHEMA, {"drawing_no": "A-1", "issue_date": "2024-02-29"})["issue_date"]
        == "2024-02-29"
    )
    with pytest.raises(MetadataError):
        normalize_values(SCHEMA, {"drawing_no": "A-1", "issue_date": "2025-02-29"})
    assert validate_suggested_values(SCHEMA, {"issue_date": "2026-02-31"}) == {}


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="inf"),
        pytest.param(float("-inf"), id="negative-inf"),
        pytest.param(1 << 20000, id="overflowing-int"),
    ],
)
def test_numbers_must_be_finite(value: float) -> None:
    with pytest.raises(MetadataError):
        normalize_values(SCHEMA, {"drawing_no": "A-1", "weight": value})
    assert validate_suggested_values(SCHEMA, {"weight": value}) == {}
