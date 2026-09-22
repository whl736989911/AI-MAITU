"""Unit tests for managing extraction templates (design §7.2, §7.3, §7.4).

The database is real (migrated SQLite) and the rows are read back through the
repo, because the rules under test are about what is stored: a version that
survives an edit, a deletion that is refused while a binding exists, a template
whose status decides whether it can be bound at all.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.data_sources import DataSourceRepo
from octop.infra.db.repos.extract_templates import STATUS_DISABLED, ExtractTemplateRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge.extract_templates import ExtractTemplateService, TemplateInUse

_CONTRACT_FIELDS = [
    {
        "name": "summary",
        "type": "text",
        "required": True,
        "instruction": "用不超过300字总结合同核心内容",
    },
    {"name": "keywords", "type": "string[]", "required": True},
]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    services = SimpleNamespace(
        db=pool,
        extract_templates_repo=ExtractTemplateRepo(pool),
        data_sources_repo=DataSourceRepo(pool),
        user_repo=UserRepo(pool),
    )
    return SimpleNamespace(services=services, templates=ExtractTemplateService(services))


@pytest.fixture
def owner(env: SimpleNamespace) -> int:
    # ``UserRepo.create`` returns the new row's id.
    return env.services.user_repo.create(username="owner", password_hash="h", role="user")


def _local_source(env: SimpleNamespace, owner_id: int, *, kind: str = "local"):
    base = KnowledgeRepo(env.services.db).create_base(owner_user_id=owner_id, name="Docs")
    return env.services.data_sources_repo.create(
        knowledge_base_id=base.id, name="Share", kind=kind, created_by=owner_id
    )


def _template(env: SimpleNamespace, owner_id: int, **overrides: object):
    kwargs: dict[str, object] = {
        "actor_user_id": owner_id,
        "name": "合同信息提取模板",
        "fields": _CONTRACT_FIELDS,
        "instruction": "重点关注合同主体、期限、金额、付款和违约责任。",
        "applies_to": "doc, docx, pdf",
    }
    kwargs.update(overrides)
    return env.templates.create(**kwargs)  # type: ignore[arg-type]


def test_a_new_template_starts_at_its_first_version(env: SimpleNamespace, owner: int) -> None:
    template, version = _template(env, owner)

    assert (template.current_version, version.version) == (1, 1)
    assert template.status == "active"
    assert [field["name"] for field in version.fields] == ["summary", "keywords"]
    assert version.fields[0]["required"] is True
    assert version.applies_to == "doc, docx, pdf"
    assert env.templates.current_version(template.id).id == version.id


def test_editing_writes_a_new_version_and_keeps_the_old_one(
    env: SimpleNamespace, owner: int
) -> None:
    """design §7.3: an edit does not overwrite what earlier results were built on."""
    template, first = _template(env, owner)

    second = env.templates.add_version(
        template.id,
        actor_user_id=owner,
        fields=[*_CONTRACT_FIELDS, {"name": "amount", "type": "amount"}],
        instruction="也提取金额。",
        applies_to="pdf",
        note="加了金额字段",
    )

    assert second.version == 2
    assert env.templates.get(template.id).current_version == 2
    assert [field["name"] for field in second.fields] == ["summary", "keywords", "amount"]
    # Version 1 is still exactly what it was, and still readable by id.
    kept = env.services.extract_templates_repo.get_version(template.id, 1)
    assert kept is not None
    assert [field["name"] for field in kept.fields] == ["summary", "keywords"]
    assert kept.instruction == first.instruction
    assert [row.version for row in env.templates.list_versions(template.id)] == [2, 1]


def test_renaming_is_not_a_new_version(env: SimpleNamespace, owner: int) -> None:
    """A rename does not make earlier results stale, so it must not bump."""
    template, _ = _template(env, owner)

    renamed = env.templates.update(template.id, actor_user_id=owner, name="合同模板")

    assert renamed.name == "合同模板"
    assert renamed.current_version == 1
    assert len(env.templates.list_versions(template.id)) == 1


def test_a_template_in_use_is_not_deleted(env: SimpleNamespace, owner: int) -> None:
    """design §7.2: a used template is disabled, not removed behind its bindings."""
    template, _ = _template(env, owner)
    source = _local_source(env, owner)
    env.templates.bind(template.id, actor_user_id=owner, data_source_id=source.id)

    with pytest.raises(TemplateInUse) as raised:
        env.templates.delete(template.id)

    assert raised.value.bindings == 1
    assert env.services.extract_templates_repo.get(template.id) is not None

    # Clearing the binding is the way out, and then the template goes — with its
    # versions, which cascade.
    binding = env.templates.list_bindings(template_id=template.id)[0]
    env.templates.unbind(binding.id)

    env.templates.delete(template.id)

    assert env.services.extract_templates_repo.get(template.id) is None
    assert env.services.extract_templates_repo.list_versions(template.id) == []


def test_a_disabled_template_cannot_be_bound(env: SimpleNamespace, owner: int) -> None:
    """design §7.2: disabling stops new bindings while existing ones keep working."""
    template, _ = _template(env, owner)
    source = _local_source(env, owner)
    env.templates.update(template.id, actor_user_id=owner, status=STATUS_DISABLED)

    with pytest.raises(ValueError, match="disabled"):
        env.templates.bind(template.id, actor_user_id=owner, data_source_id=source.id)

    env.templates.update(template.id, actor_user_id=owner, status="active")
    binding = env.templates.bind(template.id, actor_user_id=owner, data_source_id=source.id)
    assert binding.template_id == template.id


def test_a_binding_needs_a_folder_source(env: SimpleNamespace, owner: int) -> None:
    """design §7.4's tree is source → folder → file, which only a folder has."""
    template, _ = _template(env, owner)
    upload_source = _local_source(env, owner, kind="upload")

    with pytest.raises(ValueError, match="folder source"):
        env.templates.bind(template.id, actor_user_id=owner, data_source_id=upload_source.id)


def test_a_pattern_that_cannot_compile_is_refused_when_it_is_bound(
    env: SimpleNamespace, owner: int
) -> None:
    """Refused at write time: a binding that silently never matches is worse."""
    template, _ = _template(env, owner)
    source = _local_source(env, owner)

    with pytest.raises(ValueError, match="invalid match_regex"):
        env.templates.bind(
            template.id, actor_user_id=owner, data_source_id=source.id, match_regex="(["
        )


def test_a_binding_path_may_not_climb_out_of_its_source(env: SimpleNamespace, owner: int) -> None:
    template, _ = _template(env, owner)
    source = _local_source(env, owner)

    with pytest.raises(ValueError, match=r"'\.\.'"):
        env.templates.bind(
            template.id, actor_user_id=owner, data_source_id=source.id, path="../elsewhere"
        )

    # A plain path is normalized to the form the scan reports.
    binding = env.templates.bind(
        template.id, actor_user_id=owner, data_source_id=source.id, path="/legal/contracts/"
    )
    assert binding.path == "legal/contracts"


def test_resolve_answers_for_a_path_in_a_source(env: SimpleNamespace, owner: int) -> None:
    """The end-to-end question: which template reads this file?"""
    source = _local_source(env, owner)
    source_wide, _ = _template(env, owner, name="全部文件", applies_to="")
    contracts, _ = _template(env, owner, name="合同", applies_to="")
    env.templates.bind(source_wide.id, actor_user_id=owner, data_source_id=source.id)
    env.templates.bind(
        contracts.id, actor_user_id=owner, data_source_id=source.id, path="legal/contract.pdf"
    )

    matched, content_type = env.templates.resolve(
        data_source_id=source.id, path="legal/contract.pdf"
    )
    assert matched.template_id == contracts.id
    assert matched.level == "file"
    assert content_type == "application/pdf"

    elsewhere, _ = env.templates.resolve(data_source_id=source.id, path="handbook.md")
    assert elsewhere.template_id == source_wide.id
    assert elsewhere.level == "source"


def test_resolve_reports_two_templates_claiming_one_file(env: SimpleNamespace, owner: int) -> None:
    """design §7.4: the clash is reported, and nothing is picked."""
    source = _local_source(env, owner)
    first, _ = _template(env, owner, name="甲", applies_to="")
    second, _ = _template(env, owner, name="乙", applies_to="")
    env.templates.bind(first.id, actor_user_id=owner, data_source_id=source.id)
    env.templates.bind(second.id, actor_user_id=owner, data_source_id=source.id)

    matched, _ = env.templates.resolve(data_source_id=source.id, path="handbook.md")

    assert matched.conflicted
    assert matched.template_id is None
    assert sorted(matched.conflicts) == sorted([first.id, second.id])


def test_a_disabled_template_stops_applying(env: SimpleNamespace, owner: int) -> None:
    """``candidates_for_matching`` drops disabled templates, which is the point."""
    source = _local_source(env, owner)
    template, _ = _template(env, owner)
    env.templates.bind(template.id, actor_user_id=owner, data_source_id=source.id)
    # The template declares it is for doc/docx/pdf, so a contract is what it covers.
    matched, _ = env.templates.resolve(data_source_id=source.id, path="contract.pdf")
    assert matched.template_id == template.id

    env.templates.update(template.id, actor_user_id=owner, status=STATUS_DISABLED)

    disabled, _ = env.templates.resolve(data_source_id=source.id, path="contract.pdf")
    assert disabled.template_id is None


def test_an_unknown_template_is_not_found(env: SimpleNamespace, owner: int) -> None:
    with pytest.raises(LookupError, match="not found"):
        env.templates.get("nosuchtemplate")
    with pytest.raises(LookupError, match="not found"):
        env.templates.bind("nosuchtemplate", actor_user_id=owner, data_source_id="whatever")
    with pytest.raises(LookupError, match="not found"):
        env.templates.unbind("nosuchbinding")
