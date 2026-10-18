"""Authorized source reads must not depend on a vector index or invent evidence."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.config import var_child_runnable_config

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_collections import KnowledgeCollectionsRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge.collections import KnowledgeCollectionService
from octop.infra.knowledge.default_open import stamp_turn_knowledge_config
from octop.infra.knowledge.files import document_digest, write_document
from octop.infra.knowledge.source_access import KnowledgeSourceService
from octop.infra.knowledge.tools import build_knowledge_tools
from octop.infra.sharing import AclEntry
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def source_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "sources.db")
    run_migrations(pool)
    users = UserRepo(pool)
    owner = users.create(username="source-owner", password_hash="x", role="user")
    reader = users.create(username="source-reader", password_hash="x", role="user")
    knowledge = KnowledgeRepo(pool)
    base = knowledge.create_base(owner_user_id=owner, name="Originals", shared=True)
    services = SimpleNamespace(
        db=pool,
        user_repo=users,
        knowledge_repo=knowledge,
        knowledge_collections_repo=KnowledgeCollectionsRepo(pool),
        paths=PathLayout.from_env(),
    )
    try:
        yield SimpleNamespace(
            services=services,
            api=KnowledgeSourceService(services),
            base=base,
            owner=owner,
            reader=reader,
            acl=ResourceAclRepo(pool),
        )
    finally:
        pool.close()


def _source(env: SimpleNamespace, filename: str, data: bytes, *, path: str | None = None):
    document = env.services.knowledge_repo.create_document(
        kb_id=env.base.id,
        filename=filename,
        path=path or filename,
        content_type="application/octet-stream",
        byte_size=len(data),
        content_hash=document_digest(data),
        status="discovered",
    )
    original = write_document(env.base.id, document.id, filename, data)
    return document, original


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030"])
def test_unindexed_reads_are_bounded_located_and_invalidate_old_versions(
    source_env: SimpleNamespace, encoding: str
) -> None:
    env = source_env
    document, original = _source(env, "notes.txt", "开头\n退款期限为五天\n附件\n".encode(encoding))
    read = env.api.read_document(
        env.base.id,
        document.id,
        actor_user_id=env.reader,
        start=2,
        end=2,
    )
    assert read["text"] == "退款期限为五天"
    assert read["blocks"][0]["locator"] == {"line": 2}
    assert read["verified"] is True and read["complete"] is False
    assert read["next_start"] == 3
    assert read["version"] == document.content_hash
    old_evidence = read["evidence_id"]
    env.services.knowledge_repo.update_document(document.id, status="ready", chunk_count=2)
    original.write_bytes("开头\n退款期限为十天\n附件\n".encode(encoding))
    changed = env.api.read_document(
        env.base.id,
        document.id,
        actor_user_id=env.reader,
        start=2,
        end=2,
    )
    assert changed["text"] == "退款期限为十天"
    assert changed["evidence_id"] != old_evidence
    current = env.services.knowledge_repo.get_document(document.id)
    assert current.status == "discovered" and current.chunk_count == 0
    assert current.content_hash == changed["version"]


def test_source_listing_and_read_obey_folder_restrictions_and_explicit_grants(
    source_env: SimpleNamespace,
) -> None:
    env = source_env
    folder = env.services.knowledge_repo.ensure_folder(env.base.id, "restricted")
    document, _ = _source(env, "budget.md", b"Secret amount: 970", path="restricted/budget.md")
    env.acl.upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=folder.id,
            owner_user_id=env.owner,
            visibility="private",
            unit_key=None,
            version=1,
            grants=(),
        )
    )
    assert env.api.list_documents(env.base.id, actor_user_id=env.reader)["documents"] == []
    with pytest.raises(PermissionError):
        env.api.read_document(env.base.id, document.id, actor_user_id=env.reader)
    env.acl.upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=document.id,
            owner_user_id=env.owner,
            visibility="private",
            unit_key=None,
            version=1,
            grants=(("user", str(env.reader)),),
        )
    )
    assert [
        row["document_id"]
        for row in env.api.list_documents(env.base.id, actor_user_id=env.reader)["documents"]
    ] == [document.id]
    readable = env.api.read_document(env.base.id, document.id, actor_user_id=env.reader)
    assert readable["text"] == "Secret amount: 970"
    env.acl.upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=document.id,
            owner_user_id=env.owner,
            visibility="private",
            unit_key=None,
            version=2,
            grants=(),
        )
    )
    with pytest.raises(PermissionError):
        env.api.read_document(env.base.id, document.id, actor_user_id=env.reader)


def test_pdf_reads_the_requested_page_and_never_claims_rendered_pixels_are_text(
    source_env: SimpleNamespace,
    tmp_path: Path,
) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    pdf_path = tmp_path / "three-pages.pdf"
    with pymupdf.open() as pdf:
        for text in ["Confidential first page", "Refund deadline: five days", "Appendix only"]:
            page = pdf.new_page()
            page.insert_text((72, 72), text)
        pdf.save(pdf_path)
    env = source_env
    document, _ = _source(env, "report.pdf", pdf_path.read_bytes())
    read = env.api.read_document(env.base.id, document.id, actor_user_id=env.reader, start=2, end=2)
    assert "Refund deadline: five days" in read["text"]
    assert "Confidential" not in read["text"] and "Appendix" not in read["text"]
    assert read["total"] == 3 and read["blocks"][0]["locator"] == {"page": 2}
    visual = env.api.read_document(
        env.base.id,
        document.id,
        actor_user_id=env.reader,
        start=2,
        end=2,
        mode="image",
    )
    assert visual["status"] == "image_ready" and visual["verified"] is False
    assert visual["text"] == "" and visual["blocks"] == []
    assert visual["images"][0]["locator"] == {"page": 2}
    assert base64.b64decode(visual["images"][0]["data_base64"]).startswith(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="outside"):
        env.api.read_document(
            env.base.id, document.id, actor_user_id=env.reader, start=4, mode="image"
        )


def test_native_image_default_read_is_delivery_not_verified_recognition(
    source_env: SimpleNamespace,
) -> None:
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO+a8N8AAAAASUVORK5CYII="
    )
    env = source_env
    document, _ = _source(env, "diagram.png", image)
    result = env.api.read_document(env.base.id, document.id, actor_user_id=env.reader)
    assert result["status"] == "image_ready" and result["verified"] is False
    assert result["text"] == "" and result["blocks"] == []
    assert result["total"] == 1 and result["complete"] is True
    assert result["next_start"] is None
    assert result["images"][0]["mime_type"] == "image/png"
    assert result["images"][0]["locator"] == {"kind": "image"}


@pytest.mark.asyncio
async def test_export_rejects_a_source_changed_after_version_approval(
    source_env: SimpleNamespace,
    tmp_path: Path,
) -> None:
    from deepagents.backends.local_shell import LocalShellBackend
    from harness_agent.backends.workspace import BackendWorkspace

    env = source_env
    document, original = _source(env, "approved.md", b"Approved version")
    metadata = env.api.read_document(
        env.base.id,
        document.id,
        actor_user_id=env.reader,
        mode="metadata",
    )
    assert metadata["status"] == "metadata" and metadata["verified"] is False
    assert metadata["collection_updates"]["reason"] == "metadata_only"
    original.write_bytes(b"Changed after approval")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = BackendWorkspace(
        LocalShellBackend(root_dir=str(workspace_root), virtual_mode=False),
        str(workspace_root),
    )
    with pytest.raises(ValueError, match="version changed"):
        await env.api.export_document(
            env.base.id,
            document.id,
            actor_user_id=env.reader,
            expected_version=metadata["version"],
            workspace=workspace,
        )
    assert not (workspace_root / "inbound" / "knowledge" / document.id).exists()


@pytest.mark.asyncio
async def test_tool_reads_collection_before_original_and_resets_receipts_next_turn(
    source_env: SimpleNamespace,
) -> None:
    env = source_env
    document, _ = _source(env, "refund.md", b"Refunds take five business days.")
    collections = KnowledgeCollectionService(env.services)
    collection = collections.create(
        env.base.id,
        [document.id],
        prompt="Summarize refund deadlines",
        actor_user_id=env.owner,
    )[0]
    tools = {tool.name: tool for tool in build_knowledge_tools(env.services, workspace=object())}
    request = {
        "configurable": {
            "user": str(env.reader),
            "knowledge_collection_reads": [collection["id"]],
        }
    }
    for _turn in range(2):
        stamp_turn_knowledge_config(
            request,
            visible_bases=[env.base],
            explicit_ids=[env.base.id],
            owner_user_id=env.reader,
            is_admin=False,
            locale="en",
        )
        token = var_child_runnable_config.set(request)
        try:
            original_args = {"kb_id": env.base.id, "document_id": document.id}
            blocked = json.loads(await tools["read_knowledge_document"].ainvoke(original_args))
            assert blocked == {
                "status": "collection_context_required",
                "collection_ids": [collection["id"]],
            }
            overview = json.loads(
                await tools["read_knowledge_collection"].ainvoke(
                    {"kb_id": env.base.id, "collection_id": collection["id"]}
                )
            )
            assert "Summarize refund deadlines" in overview["topic_document"]
            verified = json.loads(await tools["read_knowledge_document"].ainvoke(original_args))
            assert verified["text"] == "Refunds take five business days."
            assert verified["verified"] is True
        finally:
            var_child_runnable_config.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("nested, initialized", [(True, True), (False, False)])
async def test_shared_original_export_cannot_bypass_main_graph_approval(
    source_env: SimpleNamespace, tmp_path: Path, nested: bool, initialized: bool
) -> None:
    from deepagents.backends.local_shell import LocalShellBackend
    from harness_agent.backends.workspace import BackendWorkspace
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    env = source_env
    document, _ = _source(env, "protected.md", b"Authorized source, not a shared copy.")
    workspace_root = tmp_path / "shared-workspace"
    workspace_root.mkdir()
    workspace = BackendWorkspace(
        LocalShellBackend(root_dir=str(workspace_root), virtual_mode=False),
        str(workspace_root),
    )
    env.services.agent_registry = SimpleNamespace(
        requires_shared_workspace_export_consent=lambda *_: True,
        knowledge_export_approval_ready=lambda *_: initialized,
    )
    env.services.agent_id = "shared-agent"
    env.services.shared_export_approval_enforced = True
    export = next(
        tool
        for tool in build_knowledge_tools(env.services, workspace=workspace)
        if tool.name == "export_knowledge_original"
    )
    export_call = {
        "name": export.name,
        "id": "export-original",
        "args": {
            "kb_id": env.base.id,
            "document_id": document.id,
            "expected_version": document.content_hash,
        },
        "type": "tool_call",
    }
    tool = export
    call = export_call
    if nested:
        child_builder = StateGraph(MessagesState)
        child_builder.add_node("tools", ToolNode([export]))
        child_builder.add_edge(START, "tools")
        child_builder.add_edge("tools", END)
        child = child_builder.compile(checkpointer=MemorySaver())

        async def task() -> str:
            result = await child.ainvoke(
                {"messages": [AIMessage(content="", tool_calls=[export_call])]}
            )
            return result["messages"][-1].content

        tool = StructuredTool.from_function(
            coroutine=task, name="task", description="Run a child task."
        )
        call = {"name": "task", "id": "child-task", "args": {}, "type": "tool_call"}
    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=MemorySaver())
    request = {"configurable": {"user": str(env.reader), "thread_id": "shared-copy"}}
    stamp_turn_knowledge_config(
        request,
        visible_bases=[env.base],
        explicit_ids=[env.base.id],
        owner_user_id=env.reader,
        is_admin=False,
        locale="en",
    )
    result = await graph.ainvoke(
        {"messages": [AIMessage(content="", tool_calls=[call])]}, config=request
    )
    outcome = json.loads(result["messages"][-1].content)
    assert outcome["status"] == "needs_shared_export_confirmation"
    assert not (workspace_root / "inbound").exists()


@pytest.fixture
def oversized_png() -> bytes:
    import io
    import random

    from PIL import Image

    image = Image.frombytes("RGB", (1700, 1700), random.Random(17).randbytes(1700 * 1700 * 3))
    try:
        with io.BytesIO() as output:
            image.save(output, format="PNG")
            return output.getvalue()
    finally:
        image.close()


@pytest.mark.parametrize("embedded", [False, True])
def test_oversized_images_are_lossless_located_paginated_tiles(
    source_env: SimpleNamespace, oversized_png: bytes, embedded: bool
) -> None:
    import io

    from PIL import Image

    from octop.infra.knowledge.source_access import _MAX_IMAGE_BYTES

    assert len(oversized_png) > _MAX_IMAGE_BYTES
    content = oversized_png
    filename = "large.png"
    if embedded:
        from docx import Document

        office = Document()
        office.add_picture(io.BytesIO(oversized_png))
        with io.BytesIO() as output:
            office.save(output)
            content = output.getvalue()
        filename = "report.docx"
    env = source_env
    document, original = _source(env, filename, content)
    canvas = Image.new("RGB", (1700, 1700))
    start = 1
    regions = []
    overviews = 0
    try:
        while True:
            result = env.api.read_document(
                env.base.id,
                document.id,
                actor_user_id=env.reader,
                start=start,
                end=start + 3,
                mode="image",
            )
            assert result["status"] == "image_ready" and result["verified"] is False
            assert result["locator"]["positions"] == [item["locator"] for item in result["images"]]
            for part in result["images"]:
                data = base64.b64decode(part["data_base64"], validate=True)
                assert len(data) <= _MAX_IMAGE_BYTES
                locator = part["locator"]
                assert (locator["image_width"], locator["image_height"]) == (1700, 1700)
                if embedded:
                    assert locator["image"] == 1
                    assert locator["part"].startswith("word/media/")
                with Image.open(io.BytesIO(data)) as image:
                    if locator["image_part"] == "overview":
                        overviews += 1
                        assert max(image.size) <= 1024
                    else:
                        left, top, right, bottom = locator["box"]
                        assert image.size == (right - left, bottom - top)
                        canvas.paste(image, (left, top))
                        regions.append(locator["box"])
            if result["next_start"] is None:
                break
            assert result["next_start"] > start
            start = result["next_start"]
        assert overviews == 1
        assert result["total"] == 1 + len(regions)
        assert len({tuple(region) for region in regions}) == len(regions)
        with Image.open(io.BytesIO(oversized_png)) as expected:
            assert canvas.tobytes() == expected.tobytes()
        xs = sorted({region[0] for region in regions})
        assert all(left + 1024 - right >= 64 for left, right in zip(xs[:-1], xs[1:], strict=True))
        assert original.read_bytes() == content
    finally:
        canvas.close()


def test_region_observations_do_not_alias_or_accept_forged_coordinates(
    source_env: SimpleNamespace, oversized_png: bytes
) -> None:
    env = source_env
    document, _ = _source(env, "regions.png", oversized_png)
    delivered = env.api.read_document(
        env.base.id, document.id, actor_user_id=env.reader, start=1, end=4, mode="image"
    )
    regions = [
        part["locator"] for part in delivered["images"] if part["locator"]["image_part"] == "tile"
    ]
    collections = KnowledgeCollectionService(env.services)

    def observe(locator):
        return collections.record_source_observation(
            env.base.id,
            document.id,
            "The region contains printed business information.",
            evidence_id=delivered["evidence_id"],
            version=delivered["version"],
            locator=locator,
            actor_user_id=env.reader,
        )

    first, second = observe(regions[0]), observe(regions[1])
    assert first["evidence_id"] != second["evidence_id"]
    assert observe(regions[0])["evidence_id"] == first["evidence_id"]
    for observation, region in ((first, regions[0]), (second, regions[1])):
        stored = env.services.knowledge_collections_repo.get_read(
            document.id, delivered["version"], observation["evidence_id"]
        )
        assert stored["locator"]["box"] == region["box"]
        assert stored["status"] == "interpreted"
    with pytest.raises(ValueError):
        observe({**regions[0], "start": 1, "box": [0, 0, 99999, 99999]})
