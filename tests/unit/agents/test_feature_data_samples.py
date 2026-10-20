"""The supplier and controlled-document schemas are accepted as real examples."""

from __future__ import annotations

import json
from pathlib import Path

from octop.infra.agents import feature_data as fd

EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "examples"


def test_supplier_sample_is_a_valid_ledger_with_expiry_reminder() -> None:
    schema = fd.validate_schema(
        json.loads((EXAMPLES / "feature-data-supplier.json").read_text(encoding="utf-8"))
    )
    obj = fd.object_definition(schema, "supplier")
    assert obj["kind"] == "ledger"
    assert obj["controlled"] is False
    expires = fd.field_definition(obj, "expires_at")
    assert expires["reminder_days"] == [90, 60, 30]
    assert fd.field_definition(obj, "license")["type"] == "file"


def test_controlled_document_sample_has_versions_effective_status_and_approval() -> None:
    schema = fd.validate_schema(
        json.loads((EXAMPLES / "feature-data-controlled-document.json").read_text(encoding="utf-8"))
    )
    obj = fd.object_definition(schema, "controlled_document")
    assert obj["kind"] == "document" and obj["controlled"] is True
    assert fd.field_definition(obj, "file")["type"] == "file"
    assert fd.field_definition(obj, "version")["type"] == "text"
    assert {"effective", "obsolete"}.issubset(set(fd.field_definition(obj, "status")["options"]))
    assert fd.field_definition(obj, "revision_reason")["type"] == "textarea"
    assert fd.field_definition(obj, "approved_by")["type"] == "text"
