"""Knowledge tools must report the documents an answer was built on."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.config import var_child_runnable_config

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_collections import KnowledgeCollectionsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge.files import document_digest, write_document
from octop.infra.knowledge.tools import build_knowledge_tools
from octop.infra.utils.paths import PathLayout

MARKER = "<!--octop-kb-citations:"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "citations.db")
    run_migrations(pool)
    users = UserRepo(pool)
    owner = users.create(username="citation-owner", password_hash="x", role="user")
    knowledge = KnowledgeRepo(pool)
    base = knowledge.create_base(owner_user_id=owner, name="企业知识库", shared=True)
    services = SimpleNamespace(
        db=pool,
        user_repo=users,
        knowledge_repo=knowledge,
        knowledge_collections_repo=KnowledgeCollectionsRepo(pool),
        paths=PathLayout.from_env(),
    )
    try:
        yield SimpleNamespace(services=services, base=base, owner=owner)
    finally:
        pool.close()


def _document(env: SimpleNamespace, filename: str, data: bytes, *, folder: str = "") -> str:
    path = f"{folder}/{filename}" if folder else filename
    document = env.services.knowledge_repo.create_document(
        kb_id=env.base.id,
        filename=filename,
        path=path,
        content_type="application/octet-stream",
        byte_size=len(data),
        content_hash=document_digest(data),
        status="ready",
    )
    env.services.knowledge_repo.update_document(document.id, status="ready", chunk_count=1)
    write_document(env.base.id, document.id, filename, data)
    return document.id


def _tools(env: SimpleNamespace) -> dict[str, object]:
    return {tool.name: tool for tool in build_knowledge_tools(env.services, workspace=object())}


@contextmanager
def _ctx(env: SimpleNamespace) -> Iterator[None]:
    token = var_child_runnable_config.set(
        {
            "configurable": {
                "user": str(env.owner),
                "knowledge_base_ids": [env.base.id],
                "user_is_admin": False,
            }
        }
    )
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _split(raw: str) -> tuple[str, list[dict[str, str]]]:
    head, _, trailer = raw.rpartition(MARKER)
    assert trailer.endswith("-->"), "marker must close the tool output"
    return head, json.loads(trailer[: -len("-->")])


@pytest.mark.asyncio
async def test_search_reports_only_matching_documents(env: SimpleNamespace) -> None:
    ledger = _document(env, "试验测量监视设备台账2026.doc", b"ledger", folder="设备")
    _document(env, "地热井口.dwg", b"drawing")
    tools = _tools(env)

    with _ctx(env):
        raw = await tools["search_knowledge_paths"].ainvoke({"kb_id": env.base.id, "query": "台账"})
    head, citations = _split(raw)

    payload = json.loads(head)
    assert payload["status"] == "found"
    assert [row["document_id"] for row in payload["documents"]] == [ledger]
    assert citations == [
        {
            "kb_id": env.base.id,
            "kb_name": "企业知识库",
            "doc_id": ledger,
            "filename": "试验测量监视设备台账2026.doc",
            "path": "设备/试验测量监视设备台账2026.doc",
        }
    ]


@pytest.mark.asyncio
async def test_search_without_match_adds_no_citation(env: SimpleNamespace) -> None:
    _document(env, "地热井口.dwg", b"drawing")
    tools = _tools(env)

    with _ctx(env):
        raw = await tools["search_knowledge_paths"].ainvoke(
            {"kb_id": env.base.id, "query": "不存在"}
        )
    assert MARKER not in raw
    assert json.loads(raw)["status"] == "no_match"


@pytest.mark.asyncio
async def test_read_document_credits_the_document_it_read(env: SimpleNamespace) -> None:
    doc_id = _document(env, "notes.txt", "光谱仪 12 个月校准".encode())
    tools = _tools(env)

    with _ctx(env):
        raw = await tools["read_knowledge_document"].ainvoke(
            {"kb_id": env.base.id, "document_id": doc_id}
        )
    head, citations = _split(raw)

    assert "光谱仪" in head
    assert [row["doc_id"] for row in citations] == [doc_id]
    assert citations[0]["kb_name"] == "企业知识库"
