"""Feature rules — corrections distilled per feature, awaiting human review.

One row is one rule statement. Rules are feature-scoped on purpose (a quote
rule must not reach meeting notes) and always carry ``source_task_ids``: a rule
without provenance cannot be audited. Content lives here only as text — the
tasks a rule was induced from stay in ``feature_tasks``.

A rule also lives at one of three scope layers: ``personal`` (only its owner
reads it, ``owner_user_id``), ``unit`` (its department reads it, ``unit_key``),
or ``global`` (everyone reads it — the layer of every rule written before v23).
The layer decides who may approve the rule, so the write paths below take it
explicitly rather than defaulting silently: :func:`octop.infra.features.rules.extract_rules`
and :func:`~octop.infra.features.rules.submit_rule` are the two ways a row is born.
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

SCOPE_PERSONAL = "personal"
"""Layer of a rule only its owner gets in their prompts (``owner_user_id``)."""

SCOPE_UNIT = "unit"
"""Layer of a rule a whole department gets in their prompts (``unit_key``)."""

SCOPE_GLOBAL = "global"
"""Layer of a rule every caller gets — the layer of every pre-v23 rule."""

SCOPES = (SCOPE_PERSONAL, SCOPE_UNIT, SCOPE_GLOBAL)
"""Every layer, narrowest first: the order a prompt injects and tags them in."""


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
    scope: str
    owner_user_id: int | None
    unit_key: str | None

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
            # A row written before the scope columns existed reads as ``global``
            # rather than as an unnamed layer.
            scope=str(row["scope"] or SCOPE_GLOBAL),
            owner_user_id=None if row["owner_user_id"] is None else int(row["owner_user_id"]),
            unit_key=row["unit_key"] or None,
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


def _readable_layers(
    *,
    user_id: int | None,
    unit_key: str | None,
    every_unit: bool = False,
) -> tuple[str, tuple[int | str | None, ...]]:
    """SQL predicate for the layers one caller reads, plus its parameters.

    The global layer needs no key; a personal rule is matched on the caller's id
    and a unit rule on the caller's unit. ``every_unit`` is for an admin's review
    queue — an admin decides every department's rules, so they read all of them
    instead of only the one they happen to belong to. Personal rules are the
    caller's own either way. The parameters are returned alongside the fragment so
    both read paths bind them in the same order, and the literals come first to
    keep that order fixed.
    """
    unit_clause = "scope = ?" if every_unit else "(scope = ? AND unit_key = ?)"
    params: tuple[int | str | None, ...] = (SCOPE_GLOBAL, SCOPE_PERSONAL, user_id, SCOPE_UNIT)
    if not every_unit:
        params += (unit_key,)
    return (
        f"(scope = ? OR (scope = ? AND owner_user_id = ?) OR {unit_clause})",
        params,
    )


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
        scope: str = SCOPE_GLOBAL,
        owner_user_id: int | None = None,
        unit_key: str | None = None,
    ) -> list[FeatureRuleRow]:
        """Insert one rule per text and return them in argument order.

        One transaction for the whole batch: a half-written extraction (three
        rules of five) would be indistinguishable from a deliberate one. Ids are
        ULIDs, so ``ORDER BY id`` is insertion order.

        ``scope`` defaults to the layer every pre-v23 rule was in — the callers
        that know better (extraction, submission) pass the layer explicitly.
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
                scope=scope,
                owner_user_id=owner_user_id,
                unit_key=unit_key,
            )
            for text in texts
        ]
        with self._db.transaction() as conn:
            conn.executemany(
                "INSERT INTO feature_rules("
                "id, feature_id, rule_text, status, source_task_ids, proposed_by, "
                "approved_by, created_at, reviewed_at, scope, owner_user_id, unit_key"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        row.scope,
                        row.owner_user_id,
                        row.unit_key,
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
        """Every rule of one feature, newest first (review state included).

        The unfiltered read — audit and repair paths only. What a *caller* may
        see goes through :meth:`list_visible`, which drops other people's
        personal rules.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feature_rules WHERE feature_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (feature_id, limit),
            ).fetchall()
        return map_rows(rows, FeatureRuleRow)

    def list_visible(
        self,
        feature_id: str,
        *,
        user_id: int | None,
        unit_key: str | None,
        is_admin: bool = False,
        limit: int = 100,
    ) -> list[FeatureRuleRow]:
        """Rules of one feature this caller may see, newest first.

        The global layer, the caller's own department, and the caller's own
        personal rules — under any status, since the review queue needs the
        drafts. An admin reads every department's rules because they decide them.
        Someone else's personal rules are not in that set for anyone: they are
        that person's own preferences, and no one else can decide them.
        """
        layer_sql, layer_params = _readable_layers(
            user_id=user_id,
            unit_key=unit_key,
            every_unit=is_admin,
        )
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM feature_rules WHERE feature_id = ? AND {layer_sql} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (feature_id, *layer_params, limit),
            ).fetchall()
        return map_rows(rows, FeatureRuleRow)

    def list_injectable(
        self,
        feature_id: str,
        *,
        user_id: int | None,
        unit_key: str | None,
        limit: int,
    ) -> list[FeatureRuleRow]:
        """Approved rules this caller's prompts get, personal layer first.

        One query, ranked by layer (personal → unit → global) and oldest
        last within each layer (most recently reviewed first, ``id`` breaking
        ties for same-second approvals). The ranking is what keeps the cap
        honest: the caller's own rules take their slots before the wider layers
        are considered, so a global rule can never push out a personal one.

        A caller with no unit matches no unit rule and a caller with no id
        matches no personal rule — SQL never matches ``= NULL``.
        """
        layer_sql, layer_params = _readable_layers(user_id=user_id, unit_key=unit_key)
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM feature_rules WHERE feature_id = ? AND status = ? "
                f"AND {layer_sql} "
                "ORDER BY CASE scope WHEN ? THEN 0 WHEN ? THEN 1 ELSE 2 END, "
                "reviewed_at DESC, id DESC LIMIT ?",
                (
                    feature_id,
                    APPROVED,
                    *layer_params,
                    SCOPE_PERSONAL,
                    SCOPE_UNIT,
                    limit,
                ),
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
