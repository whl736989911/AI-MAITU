"""Unit tests for the knowledge-base HTTP adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request

from octop.infra.errors import ErrorCode, OctopError


def _request() -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": "/api/knowledge-bases", "headers": []}
    )


def _services(**extra: object) -> SimpleNamespace:
    return SimpleNamespace(
        settings_repo=SimpleNamespace(get=lambda _key: None, set=lambda *_: None),
        provider_repo=SimpleNamespace(list_all=lambda: []),
        **extra,
    )


@dataclass
class _Base:
    id: str = "kb-1"
    pk: int = 1
    owner_user_id: int = 1
    name: str = "Docs"
    description: str = ""
    default_open: bool = False
    shared: bool = False
    icon_name: str = ""
    embedding_model: str = "model"
    embedding_dim: int = 0
    doc_count: int = 0
    max_documents: int = 100
    created_at: int = 1
    updated_at: int = 1


@dataclass
class _Document:
    id: str = "doc-1"
    pk: int = 1
    kb_id: str = "kb-1"
    path: str = "readme.md"
    filename: str = "readme.md"
    is_dir: bool = False
    content_type: str = "text/markdown"
    byte_size: int = 2
    content_hash: str = ""
    status: str = "pending"
    error_message: str = ""
    chunk_count: int = 0
    created_at: int = 1
    updated_at: int = 1


@pytest.mark.asyncio
async def test_enabling_without_model_maps_prerequisite_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    server = SimpleNamespace(services=_services())

    def fail(*_args: object, **_kwargs: object) -> None:
        raise ValueError("enabling knowledge bases requires an embedding model")

    monkeypatch.setattr(knowledge_bases, "set_feature_enabled", fail)

    with pytest.raises(OctopError) as raised:
        await knowledge_bases.put_feature(
            knowledge_bases.FeatureBody(enabled=True),
            request=_request(),
            server=server,
            _admin=object(),
        )

    assert raised.value.code == ErrorCode.KNOWLEDGE_PREREQUISITES_FAILED


@pytest.mark.asyncio
async def test_enabling_reindexes_documents_when_model_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    settings = {
        "knowledge_bases_enabled": "true",
        "knowledge_embedding_model": "old-model",
        "knowledge_embedding_backend": "onnx",
        "knowledge_embedding_provider_id": "",
    }
    calls: list[tuple[object, str]] = []
    server = SimpleNamespace(
        services=SimpleNamespace(
            settings_repo=SimpleNamespace(get=settings.get, set=settings.__setitem__),
            provider_repo=SimpleNamespace(list_all=lambda: []),
        )
    )

    def set_feature(
        _get: object,
        settings_set: object,
        *,
        enabled: bool,
        model: str | None,
        backend: str | None = None,
        provider_id: str | None = None,
        provider_repo: object = None,
    ) -> None:
        assert enabled is True
        assert model == "new-model"
        settings_set("knowledge_embedding_model", model)

    monkeypatch.setattr(knowledge_bases, "set_feature_enabled", set_feature)
    monkeypatch.setattr(
        knowledge_bases,
        "reindex_all_documents",
        lambda services, model: calls.append((services, model)),
    )
    monkeypatch.setattr(
        knowledge_bases,
        "get_capability",
        lambda get, _provider_repo=None: {
            "feature_enabled": get("knowledge_bases_enabled") == "true",
            "selected_model": get("knowledge_embedding_model"),
            "backend": get("knowledge_embedding_backend") or "onnx",
            "provider_id": get("knowledge_embedding_provider_id") or "",
        },
    )

    await knowledge_bases.put_feature(
        knowledge_bases.FeatureBody(enabled=True, model="new-model"),
        request=_request(),
        server=server,
        _admin=object(),
    )

    assert calls == [(server.services, "new-model")]


@pytest.mark.asyncio
async def test_enterprise_space_route_returns_the_seeded_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one knowledge base a deployment has, read-checked like any other.

    The check is the part worth pinning: the route exists so the dashboard can
    find the space, not so it can bypass the rule that decides who may read it.
    """
    from octop.api.routers import knowledge_bases

    space = _Base(owner_user_id=None, name="Enterprise")
    seen: dict[str, object] = {}

    def readable(kb_id: str, **kwargs: object) -> _Base:
        seen.update({"kb_id": kb_id, **kwargs})
        return space

    service = SimpleNamespace(enterprise_space=lambda: space, get_readable_base=readable)
    server = SimpleNamespace(services=_services())
    user = SimpleNamespace(id=1, is_admin=False)
    monkeypatch.setattr(knowledge_bases, "_knowledge_service", lambda _server: service)

    payload = await knowledge_bases.enterprise_space(request=_request(), server=server, user=user)

    assert seen == {"kb_id": "kb-1", "actor_user_id": 1, "is_admin": False}
    assert payload["knowledge_base_id"] == "kb-1"
    assert payload["owner_username"] is None


@pytest.mark.asyncio
async def test_upload_enqueues_document_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    from octop.api.routers import knowledge_bases

    calls: list[tuple[object, str, str]] = []
    service = SimpleNamespace(
        upload_document=lambda *_args, **_kwargs: _Document(),
    )
    server = SimpleNamespace(services=_services())
    user = SimpleNamespace(id=1, is_admin=False)
    upload = UploadFile(
        file=__import__("io").BytesIO(b"# hi"),
        filename="readme.md",
        headers=Headers({"content-type": "text/markdown"}),
    )
    monkeypatch.setattr(knowledge_bases, "_knowledge_service", lambda _server: service)
    monkeypatch.setattr(knowledge_bases, "assert_knowledge_usable", lambda *_a, **_k: None)
    monkeypatch.setattr(
        knowledge_bases,
        "enqueue_index_document",
        lambda services, kb_id, doc_id: calls.append((services, kb_id, doc_id)),
    )

    response = await knowledge_bases.upload_document(
        "kb-1", request=_request(), upload=upload, server=server, user=user
    )

    assert response["id"] == "doc-1"
    assert calls == [(server.services, "kb-1", "doc-1")]


def test_base_payload_includes_owner_display_fields() -> None:
    from octop.api.routers import knowledge_bases

    owner = SimpleNamespace(username="Test", display_name=None)
    server = SimpleNamespace(
        services=SimpleNamespace(user_repo=SimpleNamespace(get=lambda _id: owner))
    )
    base = _Base(owner_user_id=7)

    payload = knowledge_bases._base_payload(server, base)

    assert payload["owner_user_id"] == 7
    assert payload["owner_username"] == "Test"
    assert payload["owner_display_name"] == "Test"


@pytest.mark.asyncio
async def test_preview_document_returns_extracted_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    service = SimpleNamespace(
        preview_document=lambda *_args, **_kwargs: {
            "id": "doc-1",
            "filename": "notes.md",
            "text": "# Hello",
        }
    )
    server = SimpleNamespace(services=_services())
    user = SimpleNamespace(id=1, is_admin=False)
    monkeypatch.setattr(knowledge_bases, "_knowledge_service", lambda _server: service)

    response = await knowledge_bases.preview_document(
        "kb-1", "doc-1", request=_request(), server=server, user=user
    )

    assert response == {"id": "doc-1", "filename": "notes.md", "text": "# Hello"}


@pytest.mark.asyncio
async def test_download_document_file_returns_original_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from octop.api.routers import knowledge_bases

    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    service = SimpleNamespace(
        resolve_document_file=lambda *_args, **_kwargs: (
            path,
            "报告.pdf",
            "application/pdf",
        )
    )
    server = SimpleNamespace(services=_services())
    user = SimpleNamespace(id=1, is_admin=False)
    monkeypatch.setattr(knowledge_bases, "_knowledge_service", lambda _server: service)

    response = await knowledge_bases.download_document_file(
        "kb-1",
        "doc-1",
        request=_request(),
        disposition="inline",
        server=server,
        user=user,
    )

    assert response.path == path
    assert response.media_type == "application/pdf"
    assert "inline" in response.headers["Content-Disposition"]
    assert "filename*" in response.headers["Content-Disposition"]


@pytest.mark.asyncio
async def test_download_document_file_missing_maps_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("knowledge document original file not found")

    service = SimpleNamespace(resolve_document_file=missing)
    server = SimpleNamespace(services=_services())
    user = SimpleNamespace(id=1, is_admin=False)
    monkeypatch.setattr(knowledge_bases, "_knowledge_service", lambda _server: service)

    with pytest.raises(OctopError) as raised:
        await knowledge_bases.download_document_file(
            "kb-1",
            "doc-1",
            request=_request(),
            disposition="attachment",
            server=server,
            user=user,
        )

    assert raised.value.code == ErrorCode.KNOWLEDGE_NOT_FOUND


def test_row_payload_includes_has_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from octop.api.routers import knowledge_bases
    from octop.infra.knowledge import files as knowledge_files

    monkeypatch.setattr(
        knowledge_files,
        "documents_dir",
        lambda _kb_id: tmp_path,
    )
    doc = _Document(filename="notes.md")
    missing = knowledge_bases._row_payload(doc)
    assert missing["has_original"] is False

    (tmp_path / f"{doc.id}.md").write_text("hi", encoding="utf-8")
    present = knowledge_bases._row_payload(doc)
    assert present["has_original"] is True
    assert present["document_id"] == doc.id


@pytest.mark.asyncio
async def test_embedding_options_excludes_onnx_local_provider() -> None:
    from octop.api.routers import knowledge_bases

    providers = [
        SimpleNamespace(
            id=1,
            name="ONNX (Local)",
            api_key="onnx",
            get_models=lambda: [
                {
                    "id": "BAAI/bge-small-zh-v1.5",
                    "name": "bge",
                    "embedding": True,
                }
            ],
        ),
        SimpleNamespace(
            id=2,
            name="OpenAI",
            api_key="sk-test",
            get_models=lambda: [
                {"id": "text-embedding-3-small", "name": "embed", "embedding": True},
                {"id": "gpt-4o", "name": "GPT-4o"},
            ],
        ),
    ]
    server = SimpleNamespace(
        services=SimpleNamespace(provider_repo=SimpleNamespace(list_all=lambda: providers))
    )

    options = await knowledge_bases.embedding_options(server=server, _admin=object())

    assert [row["provider_name"] for row in options["remote"]] == ["OpenAI"]
    assert options["remote"][0]["models"] == [{"id": "text-embedding-3-small", "name": "embed"}]
    assert isinstance(options["onnx"], list)
    from octop.infra.agents.providers.onnx_catalog import ONNX_PRESET_MODEL_IDS

    onnx_ids = [row["id"] for row in options["onnx"]]
    assert onnx_ids == list(ONNX_PRESET_MODEL_IDS)
    assert all(row.get("recommended") for row in options["onnx"])
    assert all("downloaded" in row for row in options["onnx"])
    assert any(row.get("size_gb") for row in options["onnx"])


@pytest.mark.asyncio
async def test_ocr_options_only_lists_image_capable_models() -> None:
    from octop.api.routers import knowledge_bases

    provider = SimpleNamespace(
        id=2,
        name="OpenAI",
        enabled=True,
        api_key="sk-test",
        base_url="https://example.test/v1",
        get_models=lambda: [
            {"id": "text-only", "name": "Text", "input": ["text"]},
            {"id": "vision-1", "name": "Vision", "input": ["text", "image"]},
            {"id": "embed-1", "name": "Embed", "embedding": True, "input": ["image"]},
        ],
    )
    server = SimpleNamespace(
        services=SimpleNamespace(provider_repo=SimpleNamespace(list_all=lambda: [provider]))
    )

    options = await knowledge_bases.ocr_options(server=server, _admin=object())

    assert options["local"] == {"id": "rapidocr", "name": "RapidOCR (ONNX)"}
    assert options["remote"] == [
        {
            "provider_id": "2",
            "provider_name": "OpenAI",
            "models": [{"id": "vision-1", "name": "Vision"}],
        }
    ]


@pytest.mark.asyncio
async def test_embedding_options_omits_non_recommended_onnx() -> None:
    from octop.api.routers import knowledge_bases
    from octop.infra.agents.providers.onnx_catalog import ONNX_PRESET_MODEL_IDS

    extra = "BAAI/bge-small-en-v1.5"
    server = SimpleNamespace(
        services=SimpleNamespace(
            provider_repo=SimpleNamespace(list_all=lambda: []),
            settings_repo=SimpleNamespace(
                get=lambda key: extra if key == "knowledge_embedding_model" else None
            ),
        )
    )

    options = await knowledge_bases.embedding_options(server=server, _admin=object())

    assert extra not in [row["id"] for row in options["onnx"]]
    assert [row["id"] for row in options["onnx"]] == list(ONNX_PRESET_MODEL_IDS)


@pytest.mark.asyncio
async def test_embedding_options_all_onnx_includes_extras() -> None:
    from octop.api.routers import knowledge_bases
    from octop.infra.agents.providers.onnx_catalog import ONNX_PRESET_MODEL_IDS

    server = SimpleNamespace(
        services=SimpleNamespace(provider_repo=SimpleNamespace(list_all=lambda: []))
    )

    options = await knowledge_bases.embedding_options(all_onnx=True, server=server, _admin=object())

    ids = [row["id"] for row in options["onnx"]]
    assert ids[: len(ONNX_PRESET_MODEL_IDS)] == list(ONNX_PRESET_MODEL_IDS)
    assert len(ids) > len(ONNX_PRESET_MODEL_IDS)
    assert any(not row.get("recommended") for row in options["onnx"])
    assert all("downloaded" in row for row in options["onnx"])


@pytest.mark.asyncio
async def test_reindex_document_enqueues_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    calls: list[tuple[object, str, str]] = []
    service = SimpleNamespace(
        reindex_document=lambda *_args, **_kwargs: _Document(status="pending"),
    )
    server = SimpleNamespace(services=_services())
    user = SimpleNamespace(id=1, is_admin=False)
    monkeypatch.setattr(knowledge_bases, "_knowledge_service", lambda _server: service)
    monkeypatch.setattr(knowledge_bases, "_require_usable", lambda *_a, **_k: None)
    monkeypatch.setattr(
        knowledge_bases,
        "enqueue_index_document",
        lambda services, kb_id, doc_id: calls.append((services, kb_id, doc_id)),
    )

    response = await knowledge_bases.reindex_document(
        "kb-1", "doc-1", request=_request(), server=server, user=user
    )

    assert response["status"] == "pending"
    assert calls == [(server.services, "kb-1", "doc-1")]


@pytest.mark.asyncio
async def test_onnx_download_starts_catalog_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from octop.api.routers import knowledge_bases

    class _State:
        def to_dict(self) -> dict[str, object]:
            return {
                "status": "downloading",
                "progress": 0.1,
                "model_name": "BAAI/bge-small-zh-v1.5",
                "error": None,
            }

    async def start(model: str) -> _State:
        assert model == "BAAI/bge-small-zh-v1.5"
        return _State()

    monkeypatch.setattr(knowledge_bases.DOWNLOAD_MANAGER, "start_download", start)

    response = await knowledge_bases.start_onnx_download(
        knowledge_bases.OnnxDownloadBody(model="BAAI/bge-small-zh-v1.5"),
        request=_request(),
        _=object(),
    )

    assert response["status"] == "downloading"
    assert response["model_name"] == "BAAI/bge-small-zh-v1.5"


@pytest.mark.asyncio
async def test_onnx_activate_enables_downloaded_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    saved: dict[str, object] = {}

    async def ready(**_kwargs: object) -> str:
        return "ready"

    monkeypatch.setattr(knowledge_bases, "ensure_local_embedding_deps_async", ready)
    monkeypatch.setattr(knowledge_bases, "assert_catalog_model", lambda model: model)
    monkeypatch.setattr(knowledge_bases, "is_model_downloaded", lambda _model: True)
    monkeypatch.setattr(
        knowledge_bases,
        "save_config",
        lambda _setter, config: saved.update(config.to_dict()) or config,
    )
    monkeypatch.setattr(
        knowledge_bases,
        "status_payload",
        lambda *_args, **_kwargs: {"enabled": True, "model": "BAAI/bge-small-zh-v1.5"},
    )
    server = SimpleNamespace(services=_services())

    response = await knowledge_bases.activate_onnx_service(
        knowledge_bases.OnnxDownloadBody(model="BAAI/bge-small-zh-v1.5"),
        request=_request(),
        server=server,
        _=object(),
    )

    assert saved == {"enabled": True, "model": "BAAI/bge-small-zh-v1.5"}
    assert response["enabled"] is True


@pytest.mark.asyncio
async def test_rename_document_returns_row_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    server = SimpleNamespace(services=_services())
    renamed = _Document(id="doc-1", is_dir=True, path="docs/new", filename="new")

    class _FakeService:
        def rename_document(self, *_args: object, **_kwargs: object) -> _Document:
            return renamed

    monkeypatch.setattr(knowledge_bases, "_knowledge_service", lambda _server: _FakeService())

    result = await knowledge_bases.rename_document(
        kb_id="kb-1",
        doc_id="doc-1",
        body=knowledge_bases.RenameDocumentBody(new_name="new"),
        request=_request(),
        server=server,
        user=SimpleNamespace(id=1, is_admin=False),
    )

    assert result["document_id"] == "doc-1"
    assert result["path"] == "docs/new"
    assert result["filename"] == "new"


@pytest.mark.asyncio
async def test_rename_document_maps_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    def fail(*_args: object, **_kwargs: object) -> None:
        raise LookupError("knowledge document not found")

    monkeypatch.setattr(knowledge_bases, "_knowledge_service", fail)

    with pytest.raises(OctopError) as raised:
        await knowledge_bases.rename_document(
            kb_id="kb-1",
            doc_id="doc-1",
            body=knowledge_bases.RenameDocumentBody(new_name="new"),
            request=_request(),
            server=SimpleNamespace(services=_services()),
            user=object(),
        )

    assert raised.value.code == ErrorCode.KNOWLEDGE_NOT_FOUND


@pytest.mark.asyncio
async def test_rename_document_maps_name_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    def fail(*_args: object, **_kwargs: object) -> None:
        raise ValueError("a knowledge document with this name already exists")

    monkeypatch.setattr(knowledge_bases, "_knowledge_service", fail)

    with pytest.raises(OctopError) as raised:
        await knowledge_bases.rename_document(
            kb_id="kb-1",
            doc_id="doc-1",
            body=knowledge_bases.RenameDocumentBody(new_name="dup"),
            request=_request(),
            server=SimpleNamespace(services=_services()),
            user=object(),
        )

    assert raised.value.code == ErrorCode.KNOWLEDGE_NAME_TAKEN


@pytest.mark.asyncio
async def test_rename_document_maps_invalid_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.api.routers import knowledge_bases

    def fail(*_args: object, **_kwargs: object) -> None:
        raise ValueError("invalid knowledge document name")

    monkeypatch.setattr(knowledge_bases, "_knowledge_service", fail)

    with pytest.raises(OctopError) as raised:
        await knowledge_bases.rename_document(
            kb_id="kb-1",
            doc_id="doc-1",
            body=knowledge_bases.RenameDocumentBody(new_name="a/b"),
            request=_request(),
            server=SimpleNamespace(services=_services()),
            user=object(),
        )

    assert raised.value.code == ErrorCode.KNOWLEDGE_NAME_INVALID


@pytest.mark.asyncio
async def test_update_base_accepts_max_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    from octop.api.routers import knowledge_bases

    received: dict[str, object] = {}

    class _StubSvc:
        def update_base(self, *_: object, **kw: object) -> object:
            received.update(kw)
            return _Base(max_documents=42)

    monkeypatch.setattr(knowledge_bases, "_knowledge_service", lambda _s: _StubSvc())

    response = await knowledge_bases.update_base(
        kb_id="kb-1",
        body=knowledge_bases.UpdateBaseBody(max_documents=42),
        request=_request(),
        server=SimpleNamespace(services=_services()),
        user=SimpleNamespace(id=1, is_admin=False),
    )
    assert received["max_documents"] == 42
    assert response["max_documents"] == 42


@pytest.mark.asyncio
async def test_update_base_rejects_max_documents_out_of_range() -> None:
    from pydantic import ValidationError

    from octop.api.routers import knowledge_bases

    with pytest.raises(ValidationError):
        knowledge_bases.UpdateBaseBody(max_documents=-1)
    with pytest.raises(ValidationError):
        knowledge_bases.UpdateBaseBody(max_documents=10_001)
    assert knowledge_bases.UpdateBaseBody(max_documents=0).max_documents == 0
    assert knowledge_bases.UpdateBaseBody(max_documents=10_000).max_documents == 10_000
    assert knowledge_bases.UpdateBaseBody().max_documents is None


def test_map_knowledge_error_unclassified_returns_internal_error() -> None:
    from octop.api.routers.knowledge_bases import _map_knowledge_error

    err = _map_knowledge_error(RuntimeError("column max_documents does not exist"), locale="zh")
    assert err.code == ErrorCode.INTERNAL_ERROR
    assert err.status == 500

    err_generic = _map_knowledge_error(Exception("unexpected db failure"), locale="zh")
    assert err_generic.code == ErrorCode.INTERNAL_ERROR
    assert err_generic.status == 500


def test_map_knowledge_error_prerequisites_distinguished() -> None:
    from octop.api.routers.knowledge_bases import _map_knowledge_error

    err = _map_knowledge_error(
        RuntimeError("knowledge embedding prerequisites are not satisfied"), locale="zh"
    )
    assert err.code == ErrorCode.KNOWLEDGE_PREREQUISITES_FAILED
    assert err.status == 409

    err_model = _map_knowledge_error(
        ValueError("enabling knowledge bases requires an embedding model"), locale="zh"
    )
    assert err_model.code == ErrorCode.KNOWLEDGE_PREREQUISITES_FAILED
    assert err_model.status == 409


def test_map_knowledge_error_tells_a_write_refusal_from_a_read_one() -> None:
    """④: a refusal that is about *changing* a readable base says so.

    Read and edit are separate permissions (design §5.1). Mapping every
    ``PermissionError`` to ``KNOWLEDGE_FORBIDDEN`` told an actor who was looking
    at the base that they had no access to it, which is the one thing that was
    demonstrably untrue of them.
    """
    from octop.api.routers.knowledge_bases import _map_knowledge_error
    from octop.infra.knowledge.service import (
        ACCESS_READ,
        ACCESS_WRITE,
        KnowledgeAccessDenied,
    )

    refused_write = _map_knowledge_error(
        KnowledgeAccessDenied("knowledge base write access is required", access=ACCESS_WRITE),
        locale="zh",
    )
    assert refused_write.code == ErrorCode.KNOWLEDGE_WRITE_FORBIDDEN
    assert refused_write.status == 403

    # The owner-only refusal is a write refusal too: it is raised by the routes
    # that change a base, never by the ones that read it.
    refused_owner = _map_knowledge_error(
        KnowledgeAccessDenied("knowledge base owner access is required", access=ACCESS_WRITE),
        locale="zh",
    )
    assert refused_owner.code == ErrorCode.KNOWLEDGE_WRITE_FORBIDDEN

    refused_read = _map_knowledge_error(
        KnowledgeAccessDenied("knowledge base read access is required", access=ACCESS_READ),
        locale="zh",
    )
    assert refused_read.code == ErrorCode.KNOWLEDGE_FORBIDDEN
    assert refused_read.status == 403

    # Two codes, two sentences: what the client shows cannot be the same line.
    assert refused_write.localized_message("zh") != refused_read.localized_message("zh")

    # A bare ``PermissionError`` (anything that never learned the distinction)
    # keeps the access wording rather than claiming a write was refused.
    assert (
        _map_knowledge_error(
            PermissionError("knowledge base read access is required"), locale="zh"
        ).code
        is ErrorCode.KNOWLEDGE_FORBIDDEN
    )
