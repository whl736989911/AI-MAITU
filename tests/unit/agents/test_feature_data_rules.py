"""Business-boundary regressions for computed dates, recipients, and handover."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.infra.agents.feature_data_responsibility import (
    handover_feature_records,
    resolve_reminder_recipients,
)
from octop.infra.agents.feature_data_rules import (
    apply_computed_fields,
    validate_rule_schema,
    validate_rule_value,
)


def _schema(*, recipient_policy: dict | None = None) -> dict:
    obj = {
        "key": "contract",
        "fields": [
            {"key": "issued_on", "type": "date"},
            {"key": "cycle_months", "type": "integer"},
            {
                "key": "renewal_on",
                "type": "date",
                "computed": {
                    "op": "add_months",
                    "source_field": "issued_on",
                    "amount_field": "cycle_months",
                },
            },
            {"key": "assignee", "type": "user"},
            {"key": "department", "type": "org_unit"},
        ],
    }
    if recipient_policy is not None:
        obj["reminder_recipients"] = recipient_policy
    return {"version": 1, "objects": [obj]}


def test_computed_calendar_arithmetic_clamps_month_end_and_leap_year() -> None:
    schema = validate_rule_schema(_schema())
    values = apply_computed_fields(
        schema,
        {"issued_on": "2024-01-31", "cycle_months": 1},
        object_key="contract",
    )
    assert values["renewal_on"] == "2024-02-29"
    year_schema = {
        "objects": [
            {
                "key": "x",
                "fields": [
                    {"key": "issued", "type": "date"},
                    {
                        "key": "renewed",
                        "type": "date",
                        "computed": {"op": "add_years", "source_field": "issued", "amount": 1},
                    },
                ],
            }
        ]
    }
    assert (
        apply_computed_fields(
            validate_rule_schema(year_schema), {"issued": "2024-02-29"}, object_key="x"
        )["renewed"]
        == "2025-02-28"
    )


def test_computed_add_days_crosses_leap_day() -> None:
    schema = {
        "objects": [
            {
                "key": "x",
                "fields": [
                    {"key": "start", "type": "date"},
                    {
                        "key": "end",
                        "type": "date",
                        "computed": {"op": "add_days", "source_field": "start", "amount": 1},
                    },
                ],
            }
        ]
    }
    assert (
        apply_computed_fields(
            validate_rule_schema(schema), {"start": "2024-02-28"}, object_key="x"
        )["end"]
        == "2024-02-29"
    )


def test_computed_fields_reject_forgery_and_clear_when_input_missing() -> None:
    schema = validate_rule_schema(_schema())
    with pytest.raises(ValueError, match="cannot be supplied"):
        apply_computed_fields(
            schema,
            {"issued_on": "2025-01-01", "cycle_months": 1, "renewal_on": "2099-01-01"},
            object_key="contract",
        )
    assert "renewal_on" not in apply_computed_fields(
        schema,
        {"issued_on": "2025-01-01", "renewal_on": "2026-01-01"},
        object_key="contract",
        reject_supplied=False,
    )


def test_computed_dependency_cycles_and_bad_recipient_fields_are_rejected() -> None:
    cyclic = {
        "objects": [
            {
                "key": "cycle",
                "fields": [
                    {
                        "key": "a",
                        "type": "date",
                        "computed": {"op": "add_days", "source_field": "b", "amount": 1},
                    },
                    {
                        "key": "b",
                        "type": "date",
                        "computed": {"op": "add_days", "source_field": "a", "amount": 1},
                    },
                ],
            }
        ]
    }
    with pytest.raises(ValueError, match="cycle"):
        validate_rule_schema(cyclic)
    with pytest.raises(ValueError, match="user field"):
        validate_rule_schema(_schema(recipient_policy={"user_fields": ["department"]}))
    with pytest.raises(ValueError, match="supported roles"):
        validate_rule_schema(_schema(recipient_policy={"role_keys": ["quality_check"]}))


def test_user_and_org_unit_value_types_are_checked() -> None:
    assert validate_rule_value({"key": "user", "type": "user"}, 7) == 7
    assert validate_rule_value({"key": "unit", "type": "org_unit"}, " finance ") == "finance"
    with pytest.raises(ValueError):
        validate_rule_value({"key": "user", "type": "user"}, True)
    with pytest.raises(ValueError):
        validate_rule_value({"key": "unit", "type": "org_unit"}, " ")


def test_recipient_resolution_checks_active_feature_access_and_private_visibility() -> None:
    users = [
        SimpleNamespace(id=1, username="creator", role="member", disabled=0),
        SimpleNamespace(id=2, username="manager", role="unit_admin", disabled=0),
        SimpleNamespace(id=3, username="disabled", role="unit_admin", disabled=1),
        SimpleNamespace(id=4, username="sales-user", role="member", disabled=0),
        SimpleNamespace(id=5, username="child-user", role="member", disabled=0),
    ]

    class Users:
        def list(self):
            return users

        def get(self, user_id):
            return next((user for user in users if user.id == user_id), None)

        def get_by_username(self, username):
            return next((user for user in users if user.username == username), None)

    class UnitMembers:
        def list_users_in_unit(self, unit_key):
            return {"sales": ["sales-user", "disabled"], "sales-east": ["child-user"]}.get(
                unit_key, []
            )

        def list_children(self, unit_key):
            return ["sales-east"] if unit_key == "sales" else []

    class ACL:
        def get(self, _kind, _resource_id):
            return SimpleNamespace(visibility="public", owner_user_id=1, grants=())

        def scope_for_user(self, _user_id):
            return ("member", ())

    record = SimpleNamespace(
        created_by=1,
        responsible_user_id=2,
        visibility="feature",
        values={"assignee": 1, "department": "sales"},
    )
    policy = {
        "creator": False,
        "owner": False,
        "responsible": True,
        "user_fields": ["assignee"],
        "unit_fields": ["department"],
        "role_keys": ["unit_admin"],
    }
    recipients = resolve_reminder_recipients(
        record=record,
        obj={"reminder_recipients": policy},
        agent=SimpleNamespace(
            user_id=1, agent_id="feat-11", kind="feature", enterprise_unit_key=None
        ),
        user_repo=Users(),
        acl_repo=ACL(),
        unit_repo=UnitMembers(),
    )
    assert recipients == {1, 2, 4, 5}
    record.visibility = "private"
    assert resolve_reminder_recipients(
        record=record,
        obj={"reminder_recipients": policy},
        agent=SimpleNamespace(
            user_id=1, agent_id="feat-11", kind="feature", enterprise_unit_key=None
        ),
        user_repo=Users(),
        acl_repo=ACL(),
        unit_repo=UnitMembers(),
    ) == {1}


def test_handover_requires_manager_active_authorized_target_and_atomic_repo() -> None:
    class Repo:
        def handover_records(self, **kwargs):
            assert kwargs == {
                "agent_id": "feat-11",
                "from_user_id": 1,
                "to_user_id": 2,
                "actor_user_id": 9,
                "object_key": "contract",
            }
            return ["rec-1", "rec-2"]

    class Users:
        rows = {
            2: SimpleNamespace(id=2, role="member", disabled=0),
            9: SimpleNamespace(id=9, role="member", disabled=0),
        }

        def get(self, user_id):
            return self.rows.get(user_id)

    class ACL:
        visibility = "public"

        def get(self, _kind, _resource_id):
            return SimpleNamespace(
                visibility=self.visibility,
                owner_user_id=9,
                grants=(),
            )

        def scope_for_user(self, _user_id):
            return ("member", ())

    feature = SimpleNamespace(
        agent_id="feat-11", kind="feature", user_id=9, enterprise_unit_key=None
    )
    args = {
        "repo": Repo(),
        "feature_row": feature,
        "agent_id": "feat-11",
        "from_user_id": 1,
        "to_user_id": 2,
        "actor_user_id": 9,
        "user_repo": Users(),
        "org_unit_repo": object(),
        "acl_repo": ACL(),
        "object_key": "contract",
    }
    assert handover_feature_records(**args) == {
        "transferred_count": 2,
        "record_ids": ["rec-1", "rec-2"],
    }
    with pytest.raises(PermissionError, match="managers"):
        handover_feature_records(
            **{
                **args,
                "feature_row": SimpleNamespace(
                    agent_id="feat-11", kind="feature", user_id=1, enterprise_unit_key=None
                ),
            }
        )
    with pytest.raises(PermissionError, match="cannot access"):
        handover_feature_records(
            **{**args, "acl_repo": type("DeniedACL", (ACL,), {"visibility": "private"})()}
        )
    args["user_repo"].rows[2].disabled = 1
    with pytest.raises(ValueError, match="active"):
        handover_feature_records(**args)
