from __future__ import annotations

import json

from octop.infra.agents.memory_review import (
    ReviewReceipt,
    facts_digest,
    review,
    save_receipt,
    take_receipt,
    validate_review_state,
)


class _ReviewModel:
    def __init__(self, response: str) -> None:
        self.response = response

    def invoke(self, _messages):
        return type("Response", (), {"content": self.response})()


def test_semantic_review_uses_model_and_filters_unbound_fact_ids() -> None:
    model = _ReviewModel(
        json.dumps(
            {
                "conflicts": [
                    {
                        "fact_id": "fact-1",
                        "document": "USER.md",
                        "evidence": "The manual says X.",
                        "relation": "contradiction",
                    },
                    {
                        "fact_id": "outside",
                        "document": "USER.md",
                        "evidence": "unrelated",
                        "relation": "contradiction",
                    },
                ]
            }
        )
    )
    facts = [{"id": "fact-1", "assertion": "The fact says Y.", "deprecated_at": None}]
    docs = [{"name": "USER.md", "text": "The manual says X."}]

    conflicts = review(model, facts, docs)

    assert conflicts == [
        {
            "id": "fact-1",
            "fact": "The fact says Y.",
            "document": "USER.md",
            "evidence": "The manual says X.",
            "relation": "contradiction",
        }
    ]


def test_review_receipt_is_bound_to_user_agent_and_scope() -> None:
    receipt = ReviewReceipt(
        11, "expert-a", "shared", {"USER.md": "snapshot"}, "digest", {}, [], 9999999999
    )
    token = save_receipt(receipt)

    assert take_receipt(token, user_id=11, agent_id="expert-a", scope="shared") == receipt
    assert take_receipt(token, user_id=12, agent_id="expert-a", scope="shared") is None
    assert take_receipt(token, user_id=11, agent_id="expert-b", scope="shared") is None
    assert take_receipt(token, user_id=11, agent_id="expert-a", scope="private") is None


def test_review_apply_revalidates_document_and_fact_versions_before_mutation() -> None:
    receipt_facts = [{"id": "fact-1", "assertion": "old"}]
    receipt = ReviewReceipt(
        11,
        "expert-a",
        "shared",
        {"USER.md": "snapshot"},
        facts_digest(receipt_facts),
        {"fact-1": receipt_facts[0]},
        [],
        9999999999,
    )
    current_docs = [type("Doc", (), {"name": "USER.md", "snapshot": "new"})()]
    current_facts = [{"id": "fact-1", "assertion": "changed"}]

    try:
        validate_review_state(receipt, ["fact-1"], current_docs, current_facts)
    except ValueError as exc:
        assert "manual documents changed" in str(exc)
    else:
        raise AssertionError("stale manual snapshot was accepted")

    current_docs[0].snapshot = "snapshot"
    try:
        validate_review_state(receipt, ["fact-1"], current_docs, current_facts)
    except ValueError as exc:
        assert "structured facts changed" in str(exc)
    else:
        raise AssertionError("stale fact version was accepted")


def test_review_apply_selects_only_unchanged_requested_facts() -> None:
    facts = [
        {"id": "fact-1", "assertion": "unchanged"},
        {"id": "fact-2", "assertion": "not selected"},
    ]
    docs = [type("Doc", (), {"name": "USER.md", "snapshot": "snapshot"})()]
    receipt = ReviewReceipt(
        11,
        "expert-a",
        "shared",
        {"USER.md": "snapshot"},
        facts_digest(facts),
        {"fact-1": facts[0], "fact-2": facts[1]},
        [],
        9999999999,
    )

    assert validate_review_state(receipt, ["fact-1"], docs, facts) == ["fact-1"]


def test_semantic_review_rejects_evidence_from_another_document() -> None:
    model = _ReviewModel(
        json.dumps(
            {
                "conflicts": [
                    {
                        "fact_id": "fact-1",
                        "document": "USER.md",
                        "evidence": "The manual says X.",
                        "relation": "contradiction",
                    }
                ],
            }
        )
    )
    facts = [{"id": "fact-1", "assertion": "The fact says Y."}]
    docs = [
        {"name": "USER.md", "text": "No interface preference has been confirmed."},
        {"name": "MEMORY.md", "text": "The manual says X."},
    ]

    assert review(model, facts, docs) == []
