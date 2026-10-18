"""Regression coverage for prompt-registered, source-ACL-shared collections."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.knowledge_collections import KnowledgeCollectionsRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge.collections import KnowledgeCollectionService
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    users = UserRepo(pool)
    owner = users.create(username="owner", password_hash="x", role="user", locale="en")
    reader = users.create(username="reader", password_hash="x", role="user", locale="en")
    knowledge = KnowledgeRepo(pool)
    kb = knowledge.create_base(owner_user_id=owner, name="Research", shared=True)
    private = knowledge.create_base(owner_user_id=owner, name="Private", shared=False)
    folder = knowledge.ensure_folder(kb.id, "reports")
    child = knowledge.create_document(
        kb_id=kb.id,
        filename="match.md",
        path="reports/child/match.md",
        content_type="text/markdown",
        byte_size=80,
        content_hash="v1",
        status="ready",
    )
    sibling = knowledge.create_document(
        kb_id=kb.id,
        filename="other.md",
        path="reports/other.md",
        content_type="text/markdown",
        byte_size=60,
        content_hash="v1",
        status="ready",
    )
    services = SimpleNamespace(
        db=pool,
        user_repo=users,
        knowledge_repo=knowledge,
        knowledge_collections_repo=KnowledgeCollectionsRepo(pool),
        settings_repo=SettingsRepo(pool),
        paths=PathLayout(tmp_path / "home"),
    )
    services.settings_repo.set("knowledge_bases_enabled", "true")
    return SimpleNamespace(
        services=services,
        api=KnowledgeCollectionService(services),
        owner=owner,
        reader=reader,
        kb=kb,
        private=private,
        folder=folder,
        child=child,
        sibling=sibling,
        knowledge=knowledge,
    )


def _read_evidence(version: str, text: str) -> dict:
    locator = {"kind": "range", "start": 1, "end": 1}
    return {
        "status": "read",
        "version": version,
        "text": text,
        "locator": locator,
        "evidence_id": "source:top",
        "blocks": [
            {
                "text": text,
                "locator": {"line": 3},
                "evidence_id": f"source:{version}:b1",
            }
        ],
        "verified": True,
    }


def test_create_registers_prompt_without_reading_or_parsing(setup) -> None:
    state = setup
    created = state.api.create(
        state.kb.id,
        [state.folder.id, state.child.id],
        prompt="Extract the payment terms",
        actor_user_id=state.owner,
    )
    assert [item["target_kind"] for item in created] == ["folder", "file"]
    for item in created:
        assert item["prompt"] == "Extract the payment terms"
        assert item["generation"]["state"] == "idle"
        assert item["status"] == "pending"
        assert item["coverage"]["total"] == 0
        result = state.api.documents(state.kb.id, item["id"], actor_user_id=state.owner)
        assert result["collection"]["prompt"] == "Extract the payment terms"
        assert "Extract the payment terms" in result["topic_document"]
        assert "needle source body" not in result["topic_document"]
        assert "needle source body" not in result["reading_document"]
        assert Path(result["storage_path"], "topic-index.md").is_file()
        assert Path(result["storage_path"], "reading-index.md").is_file()
        assert state.services.knowledge_collections_repo.topics(item["id"]) == []
    assert (
        state.services.knowledge_collections_repo.reads_for_documents(state.kb.id, [state.child.id])
        == []
    )
    # Empty prompts are valid registrations for reading-accumulation-only use.
    silent = state.api.create(
        state.kb.id, [state.sibling.id], prompt="", actor_user_id=state.owner
    )[0]
    assert silent["prompt"] == ""
    assert silent["generation"]["state"] == "idle"


def test_verified_read_fans_out_to_all_containing_collections_and_dedups(setup) -> None:
    state = setup
    owners_folder = state.api.create(
        state.kb.id, [state.folder.id], prompt="payment", actor_user_id=state.owner
    )[0]
    readers_child = state.api.create(
        state.kb.id, [state.child.id], prompt="payment", actor_user_id=state.reader
    )[0]
    outcome = state.api.record_source_read(
        state.kb.id,
        state.child.id,
        _read_evidence("v1", "needle source body"),
        actor_user_id=state.owner,
    )
    assert outcome["updated"] == 2
    assert set(outcome["collections"]) == {owners_folder["id"], readers_child["id"]}
    for collection_id, actor in (
        (owners_folder["id"], state.owner),
        (readers_child["id"], state.reader),
    ):
        result = state.api.documents(state.kb.id, collection_id, actor_user_id=actor)
        assert "needle source body" in result["reading_document"]
        assert "source:v1:b1" in result["reading_document"]
    reads = state.services.knowledge_collections_repo.reads_for_documents(
        state.kb.id, [state.child.id]
    )
    assert len(reads) == 1
    repeat = state.api.record_source_read(
        state.kb.id,
        state.child.id,
        _read_evidence("v1", "needle source body"),
        actor_user_id=state.reader,
    )
    assert repeat["reason"] == "duplicate"
    assert repeat["updated"] == 0
    assert (
        len(
            state.services.knowledge_collections_repo.reads_for_documents(
                state.kb.id, [state.child.id]
            )
        )
        == 1
    )


def test_stale_or_denied_evidence_never_leaks(setup) -> None:
    state = setup
    collection = state.api.create(
        state.kb.id, [state.child.id], prompt="payment", actor_user_id=state.owner
    )[0]
    stale = state.api.record_source_read(
        state.kb.id,
        state.child.id,
        _read_evidence("old-version", "needle source body"),
        actor_user_id=state.owner,
    )
    assert stale["reason"] == "stale_version"
    assert (
        state.services.knowledge_collections_repo.reads_for_documents(state.kb.id, [state.child.id])
        == []
    )
    state.api.record_source_read(
        state.kb.id,
        state.child.id,
        _read_evidence("v1", "needle source body"),
        actor_user_id=state.owner,
    )
    # A private base the reader cannot read never exposes its collections.
    hidden_doc = state.knowledge.create_document(
        kb_id=state.private.id,
        filename="hidden.md",
        path="hidden.md",
        content_type="text/markdown",
        byte_size=10,
        content_hash="v1",
        status="ready",
    )
    hidden = state.api.create(
        state.private.id, [hidden_doc.id], prompt="payment", actor_user_id=state.owner
    )[0]
    with pytest.raises((LookupError, PermissionError)):
        state.api.documents(state.private.id, hidden["id"], actor_user_id=state.reader)
    with pytest.raises((LookupError, PermissionError)):
        state.api.list(state.private.id, actor_user_id=state.reader)
    # A source update invalidates the recorded evidence for every collection.
    state.knowledge.update_document(state.child.id, content_hash="v2")
    state.api.refresh_for_document(state.kb.id, state.child.id)
    assert (
        state.services.knowledge_collections_repo.reads_for_documents(state.kb.id, [state.child.id])
        == []
    )
    result = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert "needle source body" not in result["reading_document"]


def test_prompt_and_name_changes_need_write_and_mark_topics_stale(setup) -> None:
    state = setup
    collection = state.api.create(
        state.kb.id, [state.child.id], prompt="payment", actor_user_id=state.owner
    )[0]
    repo = state.services.knowledge_collections_repo
    repo.put_topic(
        collection["id"],
        state.child.id,
        "v1",
        status="ready",
        content="payment due in 30 days",
        summary="payment terms",
        citations=(),
        evidence_ids=(),
        model_ref="test/model",
    )
    with pytest.raises(PermissionError):
        state.api.update(
            state.kb.id,
            collection["id"],
            prompt="delivery",
            actor_user_id=state.reader,
        )
    updated = state.api.update(
        state.kb.id, collection["id"], prompt="delivery", actor_user_id=state.owner
    )
    assert updated["prompt"] == "delivery"
    assert updated["generation"]["state"] == "idle"
    topic = repo.topics(collection["id"])[0]
    assert topic["status"] == "stale"
    cleared = state.api.update(state.kb.id, collection["id"], prompt="", actor_user_id=state.owner)
    assert cleared["prompt"] == ""


def test_generate_requires_prompt_and_reports_missing_model_honestly(setup) -> None:
    state = setup
    service = KnowledgeService(state.services)
    document = service.upload_document(
        state.kb.id,
        actor_user_id=state.owner,
        filename="terms.md",
        path="reports/terms.md",
        content_type="text/markdown",
        content=b"# Payment\n\nPayment is due in 30 days.\n",
    )
    collection = state.api.create(
        state.kb.id, [state.folder.id], prompt="", actor_user_id=state.owner
    )[0]
    from octop.infra.errors import OctopError

    with pytest.raises(OctopError):
        state.api.generate(state.kb.id, collection["id"], actor_user_id=state.owner)
    state.api.update(
        state.kb.id,
        collection["id"],
        prompt="Summarize payment obligations",
        actor_user_id=state.owner,
    )
    payload = state.api.generate(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert payload["generation"]["state"] == "error"
    assert payload["coverage"]["failed"] >= 1
    result = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    # The authorized reads still accumulated evidence even though topics failed.
    assert "Payment is due in 30 days." in result["reading_document"]
    # No fabricated topic content appears without a model.
    assert "Payment is due in 30 days." not in result["topic_document"]
    assert state.services.knowledge_collections_repo.reads_for_documents(state.kb.id, [document.id])
    _ = state.sibling


def test_observation_requires_image_receipt_and_grounded_locator(setup) -> None:
    state = setup
    state.api.create(state.kb.id, [state.child.id], prompt="diagrams", actor_user_id=state.owner)
    image_evidence = {
        "status": "image_ready",
        "version": "v1",
        "text": "",
        "verified": False,
        "evidence_id": "source:img1",
        "locator": {"kind": "image", "start": 1, "end": 1},
        "images": [{"mime_type": "image/png", "locator": {"page": 1}}],
        "blocks": [],
    }
    state.api.record_source_read(
        state.kb.id,
        state.child.id,
        image_evidence,
        actor_user_id=state.owner,
    )
    recorded = state.api.record_source_observation(
        state.kb.id,
        state.child.id,
        {"text": "The diagram shows a payment flow between two parties."},
        evidence_id="source:img1",
        version="v1",
        locator={"page": 1},
        actor_user_id=state.owner,
    )
    assert recorded["kind"] == "interpretation"
    assert recorded["status"] == "interpreted"
    repo = state.services.knowledge_collections_repo
    stored = repo.get_read(state.child.id, "v1", recorded["evidence_id"])
    assert stored is not None and stored["text"].startswith("The diagram shows")
    with pytest.raises(ValueError):
        state.api.record_source_observation(
            state.kb.id,
            state.child.id,
            "observation outside the delivered page",
            evidence_id="source:img1",
            version="v1",
            locator={"page": 99},
            actor_user_id=state.owner,
        )
    with pytest.raises(ValueError):
        state.api.record_source_observation(
            state.kb.id,
            state.child.id,
            "stale version observation",
            evidence_id="source:img1",
            version="v0",
            locator={"page": 1},
            actor_user_id=state.owner,
        )
    # A verified TEXT receipt is not an image delivery receipt.
    state.api.record_source_read(
        state.kb.id,
        state.child.id,
        _read_evidence("v1", "plain text body"),
        actor_user_id=state.owner,
    )
    with pytest.raises(ValueError):
        state.api.record_source_observation(
            state.kb.id,
            state.child.id,
            "observation on a text receipt",
            evidence_id="source:v1:b1",
            version="v1",
            locator={"line": 3},
            actor_user_id=state.owner,
        )


def test_deleted_source_prunes_evidence_and_topic_state(setup) -> None:
    state = setup
    collection = state.api.create(
        state.kb.id, [state.child.id], prompt="payment", actor_user_id=state.owner
    )[0]
    state.api.record_source_read(
        state.kb.id,
        state.child.id,
        _read_evidence("v1", "needle source body"),
        actor_user_id=state.owner,
    )
    state.knowledge.delete_document(state.child.id)
    state.api.refresh_for_document(state.kb.id, state.child.id)
    assert (
        state.services.knowledge_collections_repo.reads_for_documents(state.kb.id, [state.child.id])
        == []
    )
    assert state.services.knowledge_collections_repo.topics(collection["id"]) == []
    result = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert result["collection"]["coverage"]["total"] == 0
    assert "needle source body" not in result["reading_document"]


def test_shared_reader_sees_source_derived_collections_and_rename_resyncs(setup) -> None:
    state = setup
    collection = state.api.create(
        state.kb.id, [state.child.id], prompt="payment", actor_user_id=state.owner
    )[0]
    visible = state.api.list(state.kb.id, actor_user_id=state.reader)
    assert [item["id"] for item in visible] == [collection["id"]]
    reader_view = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.reader)
    assert reader_view["collection"]["target_document_id"] == state.child.id
    with pytest.raises(PermissionError):
        state.api.delete(state.kb.id, collection["id"], actor_user_id=state.reader)
    state.knowledge.update_document(
        state.child.id, filename="renamed.md", path="reports/child/renamed.md"
    )
    state.api.refresh_for_document(state.kb.id, state.child.id)
    refreshed = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert refreshed["collection"]["target_path"] == "reports/child/renamed.md"


def test_prompt_change_during_generation_never_presents_old_results_current(
    setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = setup
    service = KnowledgeService(state.services)
    document = service.upload_document(
        state.kb.id,
        actor_user_id=state.owner,
        filename="terms.md",
        path="reports/terms.md",
        content_type="text/markdown",
        content=b"# Terms\n\nPayment is due in 30 days.\n",
    )
    collection = state.api.create(
        state.kb.id, [document.id], prompt="payment", actor_user_id=state.owner
    )[0]
    from octop.infra.knowledge.collections import KnowledgeCollectionService

    def prompt_changes_mid_flight(
        self: KnowledgeCollectionService,
        coll: object,
        document: object,
        version: str,
        evidence: object,
        *,
        system: str,
        llm: object,
        images: object = None,
    ) -> dict:
        # The collection prompt changes while the model call is in flight.
        state.services.knowledge_collections_repo.update(collection["id"], prompt="delivery")
        return {
            "summary": "old payment summary",
            "content": "answered the payment prompt",
            "citations": [],
            "coverage": "complete",
            "conflicts": [],
            "omissions": [],
        }

    monkeypatch.setattr(
        KnowledgeCollectionService, "_resolve_llm", lambda self: (object(), "test/model")
    )
    monkeypatch.setattr(KnowledgeCollectionService, "_topic_llm_json", prompt_changes_mid_flight)
    payload = state.api.generate(state.kb.id, collection["id"], actor_user_id=state.owner)
    repo = state.services.knowledge_collections_repo
    topic = repo.topics(collection["id"])[0]
    # The result answers the old prompt: stored, but never as current.
    assert topic["summary"] == "old payment summary"
    assert topic["status"] == "stale"
    assert payload["generation"]["state"] == "partial"


def test_generate_marks_missing_registered_source_failed(setup: SimpleNamespace) -> None:
    state = setup
    collection = state.api.create(
        state.kb.id, [state.child.id], prompt="payment", actor_user_id=state.owner
    )[0]
    state.knowledge.delete_document(state.child.id)
    payload = state.api.generate(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert payload["generation"]["state"] == "error", payload


def test_generate_skips_unsupported_formats_without_hiding_originals(
    setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octop.infra.knowledge.files import document_digest, write_document
    from octop.infra.knowledge.source_access import KnowledgeSourceService

    state = setup
    folder = state.knowledge.ensure_folder(state.kb.id, "mixed")
    originals = [
        ("terms.LOG", b"Payment is due in 30 days.\n"),
        ("Thumbs.db", b"thumbnail cache"),
        ("drawing.DWG", b"opaque drawing"),
        ("README", b"no declared document format"),
    ]
    documents = []
    for filename, content in originals:
        document = state.knowledge.create_document(
            kb_id=state.kb.id,
            filename=filename,
            path=f"mixed/{filename}",
            content_type="application/octet-stream",
            byte_size=len(content),
            content_hash=document_digest(content),
            status="discovered",
        )
        write_document(state.kb.id, document.id, filename, content)
        documents.append(document)
    collection = state.api.create(
        state.kb.id, [folder.id], prompt="payment terms", actor_user_id=state.owner
    )[0]
    repo = state.services.knowledge_collections_repo
    for document in documents[1:]:
        repo.put_topic(
            collection["id"],
            document.id,
            document.content_hash,
            status="error",
            content="",
            summary="",
            citations=(),
            evidence_ids=(),
            error="earlier unsupported format failure",
        )
    monkeypatch.setattr(
        KnowledgeCollectionService, "_resolve_llm", lambda self: (object(), "test/model")
    )
    monkeypatch.setattr(
        KnowledgeCollectionService,
        "_topic_llm_json",
        lambda *args, **kwargs: {
            "summary": "Payment terms",
            "content": "Payment is due in 30 days.",
            "citations": [{"index": 1}],
            "coverage": "complete",
        },
    )
    payload = state.api.generate(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert payload["generation"]["state"] == "ready"
    assert payload["generation"]["error"] == ""
    assert payload["coverage"] == {
        "total": 1,
        "read": 1,
        "generated": 1,
        "failed": 0,
        "pending": 0,
    }
    source = KnowledgeSourceService(state.services)
    listed = source.list_documents(state.kb.id, path="mixed", actor_user_id=state.owner)
    assert {item["document_id"] for item in listed["documents"]} == {
        document.id for document in documents
    }
    for document, (_, content) in zip(documents, originals, strict=True):
        with source.open_original(state.kb.id, document.id, actor_user_id=state.owner) as original:
            assert original.read_bytes() == content


def test_generate_keeps_parse_failures_for_supported_formats(setup: SimpleNamespace) -> None:
    from octop.infra.knowledge.files import document_digest, write_document

    state = setup
    folder = state.knowledge.ensure_folder(state.kb.id, "broken")
    for filename in ("damaged.pdf", "Thumbs.db"):
        content = b"not a valid document"
        document = state.knowledge.create_document(
            kb_id=state.kb.id,
            filename=filename,
            path=f"broken/{filename}",
            content_type="application/octet-stream",
            byte_size=len(content),
            content_hash=document_digest(content),
            status="discovered",
        )
        write_document(state.kb.id, document.id, filename, content)
    collection = state.api.create(
        state.kb.id, [folder.id], prompt="payment terms", actor_user_id=state.owner
    )[0]
    payload = state.api.generate(state.kb.id, collection["id"], actor_user_id=state.owner)
    assert payload["generation"]["state"] == "error"
    assert payload["coverage"]["total"] == 1
    assert payload["coverage"]["failed"] == 1
    assert payload["coverage"]["pending"] == 0


@pytest.mark.parametrize("fail_last_tile", [False, True])
def test_large_image_topics_inspect_every_region_without_unbounded_requests(
    setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, fail_last_tile: bool
) -> None:
    import base64
    import io
    import json
    import random

    from langchain_core.messages import AIMessage
    from PIL import Image

    from octop.infra.knowledge.files import document_digest, write_document

    state = setup
    image = Image.frombytes("RGB", (1700, 1700), random.Random(17).randbytes(1700 * 1700 * 3))
    try:
        with io.BytesIO() as output:
            image.save(output, format="PNG")
            data = output.getvalue()
    finally:
        image.close()
    document = state.knowledge.create_document(
        kb_id=state.kb.id,
        filename="license.png",
        path="license.png",
        content_type="image/png",
        byte_size=len(data),
        content_hash=document_digest(data),
        status="discovered",
    )
    write_document(state.kb.id, document.id, document.filename, data)
    collection = state.api.create(
        state.kb.id, [document.id], prompt="business identity", actor_user_id=state.owner
    )[0]

    class VisionModel:
        async def ainvoke(self, messages):
            body = messages[-1].content
            if isinstance(body, list):
                image_parts = [part for part in body if part["type"] == "image_url"]
                assert len(image_parts) == 1
                context = json.loads(body[0]["text"])
                location = context["evidence"][0]["locator"]
                if (
                    fail_last_tile
                    and location.get("image_part") == "tile"
                    and location["box"][0] > 0
                    and location["box"][1] > 0
                ):
                    raise RuntimeError("provider could not inspect this tile")
                encoded = image_parts[0]["image_url"]["url"].split(",", 1)[1]
                binary = base64.b64decode(encoded)
                assert len(binary) <= 8 * 1024 * 1024
                with Image.open(io.BytesIO(binary)) as decoded:
                    if location["image_part"] == "tile":
                        left, top, right, bottom = location["box"]
                        assert decoded.size == (right - left, bottom - top)
            # Identical observations of different regions must retain distinct provenance.
            return AIMessage(
                content=json.dumps(
                    {
                        "summary": "Printed business information",
                        "content": "The image region contains printed business information.",
                        "coverage": "partial",
                        "omissions": ["Some text is not confidently legible."],
                    }
                )
            )

    monkeypatch.setattr(
        KnowledgeCollectionService, "_resolve_llm", lambda self: (VisionModel(), "test/vision")
    )
    payload = state.api.generate(state.kb.id, collection["id"], actor_user_id=state.owner)
    repo = state.services.knowledge_collections_repo
    observations = [
        item
        for item in repo.reads_for_documents(state.kb.id, [document.id])
        if item["kind"] == "interpretation"
    ]
    topic = repo.topics(collection["id"])[0]
    if fail_last_tile:
        assert topic["status"] == "needs_visual"
        assert payload["coverage"]["failed"] == 1
        assert len(observations) == 4
    else:
        assert topic["status"] == "partial"
        assert payload["coverage"]["failed"] == 0
        assert len(observations) == 5
        assert {item["evidence_id"] for item in observations}.issubset(
            {item["evidence_id"] for item in topic["citations"]}
        )
        regions = [item["locator"] for item in observations]
        assert any(region["image_part"] == "overview" for region in regions)
        assert any(
            region["image_part"] == "tile"
            and region["box"][0] > 0
            and region["box"][1] > 0
            and region["box"][2:] == [1700, 1700]
            for region in regions
        )
        rendered = state.api.documents(state.kb.id, collection["id"], actor_user_id=state.owner)
        assert all(item["evidence_id"] in rendered["reading_document"] for item in observations)
