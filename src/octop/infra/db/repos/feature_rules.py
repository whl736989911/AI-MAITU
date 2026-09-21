"""Feature rules — corrections distilled per feature, awaiting human review.

One row is one rule statement. Rules are feature-scoped on purpose (a quote
rule must not reach meeting notes) and always carry ``source_task_ids``: a rule
without provenance cannot be audited. Content lives here only as text — the
tasks a rule was induced from stay in ``feature_tasks``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

DRAFT = "draft"
"""Status of a freshly extracted/user-written rule — not injected yet."""

APPROVED = "approved"
"""Status of a reviewed rule that may be injected into prompts."""

REJECTED = "rejected"
"""Status of a reviewed rule that must never be injected."""


@dataclass(frozen=True)
class FeatureRuleRow:
    id: str
    feature_id: str
    rule_text: str
    status: str
    source_task_ids: str
    proposed_by: str
    approved_by: int | None
    created_at: int
    reviewed_at: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> FeatureRuleRow:
        return cls(
            id=str(row["id"]),
            feature_id=str(row["feature_id"]),
            rule_text=str(row["rule_text"]),
            status=str(row["status"]),
            source_task_ids=str(row["source_task_ids"]),
            proposed_by=str(row["proposed_by"]),
            approved_by=None if row["approved_by"] is None else int(row["approved_by"]),
            created_at=int(row["created_at"]),
            reviewed_at=None if row["reviewed_at"] is None else int(row["reviewed_at"]),
        )

    def source_task_id_list(self) -> list[str]:
        """Task ids this rule was induced from (empty when the column is unreadable)."""
        try:
            parsed = json.loads(self.source_task_ids)
        except ValueError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]


class FeatureRuleRepo:
    """Data-access object for the ``feature_rules`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create_many(
        self,
        *,
        feature_id: str,
        rule_texts: Sequence[str],
        source_task_ids: Sequence[str],
        proposed_by: str,
        status: str = DRAFT,
    ) -> list[FeatureRuleRow]:
        """Insert one rule per text and return them in argument order.

        One transaction for the whole batch: a half-written extraction (three
        rules of five) would be indistinguishable from a deliberate one. Ids are
        ULIDs, so ``ORDER BY id`` is insertion order.
        """
        texts = [text for text in rule_texts if text.strip()]
        if not texts:
            return []
        created_at = now_ts()
        sources = json.dumps(list(source_task_ids), ensure_ascii=False)
        created = [
            FeatureRuleRow(
                id=new_ulid(),
                feature_id=feature_id,
                rule_text=text,
                status=status,
                source_task_ids=sources,
                proposed_by=proposed_by,
                approved_by=None,
                created_at=created_at,
                reviewed_at=None,
            )
            for text in texts
        ]
        with self._db.transaction() as conn:
            conn.executemany(
                "INSERT INTO feature_rules("
                "id, feature_id, rule_text, status, source_task_ids, proposed_by, "
                "approved_by, created_at, reviewed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        row.id,
                        row.feature_id,
                        row.rule_text,
                        row.status,
                        row.source_task_ids,
                        row.proposed_by,
                        row.approved_by,
                        row.created_at,
                        row.reviewed_at,
                    )
                    for row in created
                ],
            )
        return created

    def get(self, rule_id: str) -> FeatureRuleRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM feature_rules WHERE id = ?",
                (rule_id,),
            ).fetchone()
        return FeatureRuleRow.from_row(row) if row is not None else None

    def list_for_feature(self, feature_id: str, limit: int = 100) -> list[FeatureRuleRow]:
        """Every rule of one feature, newest first (review state included)."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_rules WHERE feature_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (feature_id, limit),
            ).fetchall()
        return map_rows(rows, FeatureRuleRow)

    def list_approved(self, feature_id: str, limit: int) -> list[FeatureRuleRow]:
        """Approved rules, most recently reviewed first — the injection order.

        ``id`` breaks ties: approvals made within the same second still come back
        newest-first because rule ids are time-ordered ULIDs.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_rules WHERE feature_id = ? AND status = ? "
                "ORDER BY reviewed_at DESC, id DESC LIMIT ?",
                (feature_id, APPROVED, limit),
            ).fetchall()
        return map_rows(rows, FeatureRuleRow)

    def mark_reviewed(
        self,
        rule_id: str,
        *,
        status: str,
        approved_by: int | None,
        reviewed_at: int | None = None,
    ) -> FeatureRuleRow | None:
        """Move a rule out of ``draft`` and return it.

        ``None`` means the rule does not exist or was already reviewed — the
        ``status = 'draft'`` guard lives in the statement so two reviewers racing
        on one rule cannot both win.
        """
        with self._db.transaction() as conn:
            row = conn.execute(
                "UPDATE feature_rules SET status = ?, approved_by = ?, reviewed_at = ? "
                "WHERE id = ? AND status = ? RETURNING *",
                (
                    status,
                    approved_by,
                    now_ts() if reviewed_at is None else reviewed_at,
                    rule_id,
                    DRAFT,
                ),
            ).fetchone()
        return FeatureRuleRow.from_row(row) if row is not None else None
