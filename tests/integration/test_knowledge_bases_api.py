"""HTTP ACL for knowledge-base instance settings vs page CRUD."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from octop.infra.knowledge.data_sources import DataSourceService, FolderSettings
from octop.infra.knowledge.files import document_digest
from octop.infra.knowledge.service import KnowledgeService
from tests.support.auth import create_user, resolve_user_id


async def test_feature_toggle_requires_knowledge_settings(env: Any) -> None:
    client, _server, admin_auth = env
    page_auth = await create_user(client, admin_auth, username="kb_page_user")
    denied = await client.put(
        "/api/knowledge-bases/feature",
        headers=page_auth,
        json={"enabled": False},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    settings_auth = await create_user(
        client,
        admin_auth,
        username="kb_settings_user",
        permissions=["knowledge_bases", "knowledge_settings"],
    )
    allowed = await client.put(
        "/api/knowledge-bases/feature",
        headers=settings_auth,
        json={"enabled": False},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["feature_enabled"] is False


async def test_embedding_options_require_knowledge_settings(env: Any) -> None:
    client, _server, admin_auth = env
    page_auth = await create_user(client, admin_auth, username="kb_options_user")
    denied = await client.get("/api/knowledge-bases/embedding-options", headers=page_auth)
    assert denied.status_code == 403, denied.text

    settings_auth = await create_user(
        client,
        admin_auth,
        username="kb_options_admin",
        permissions=["knowledge_settings"],
    )
    allowed = await client.get("/api/knowledge-bases/embedding-options", headers=settings_auth)
    assert allowed.status_code == 200, allowed.text
    assert "onnx" in allowed.json()


async def _space_id(client: Any, admin_auth: dict[str, str]) -> str:
    """The deployment's one knowledge space, as an account that may read it."""
    listed = await client.get("/api/knowledge-bases", headers=admin_auth)
    assert listed.status_code == 200, listed.text
    return listed.json()[0]["knowledge_base_id"]


async def _private_base_id(server: Any, admin_id: int) -> str:
    """A base only its owner may read — the other half of the two refusals."""
    base = server.services.knowledge_repo.create_base(owner_user_id=admin_id, name="Private notes")
    return base.id


#: The page's own reads: the list, one base, and that base's documents.
def _page_reads(kb_id: str) -> list[str]:
    root = f"/api/knowledge-bases/{kb_id}"
    return ["/api/knowledge-bases", root, f"{root}/documents"]


async def test_reads_need_a_page_key(env: Any) -> None:
    """②: revoking both keys closes the reads, not only the page.

    The page is reachable through ``knowledge_bases`` *or* ``knowledge_settings``
    and its writes were already gated, so the reads used to be the quiet way back
    in: the entry point disappeared while ``GET /api/knowledge-bases``,
    ``GET /{kb_id}`` and ``GET /{kb_id}/documents`` still answered 200 to a
    direct API call (design §2.4).
    """
    client, _server, admin_auth = env
    kb_id = await _space_id(client, admin_auth)
    keyless = await create_user(client, admin_auth, username="kb_keyless", permissions=[])

    for path in _page_reads(kb_id):
        refused = await client.get(path, headers=keyless)
        assert refused.status_code == 403, f"{path}: {refused.status_code} {refused.text}"
        assert refused.json()["error"]["code"] == "FORBIDDEN"
        # The refusal names the keys that would open it, both of them: a client
        # cannot tell from one key which page it is being refused.
        assert refused.json()["error"]["details"]["permission"] == [
            "knowledge_bases",
            "knowledge_settings",
        ]


async def test_either_page_key_reads_the_page(env: Any) -> None:
    """②: holding a key still reads — ``knowledge_bases`` or ``knowledge_settings``."""
    client, _server, admin_auth = env
    kb_id = await _space_id(client, admin_auth)

    for key in ("knowledge_bases", "knowledge_settings"):
        auth = await create_user(
            client, admin_auth, username=f"kb_page_reader_{key}", permissions=[key]
        )
        for path in _page_reads(kb_id):
            allowed = await client.get(path, headers=auth)
            assert allowed.status_code == 200, f"{key} {path}: {allowed.text}"


async def test_search_is_the_pages_read_too(env: Any) -> None:
    """⑤: the page's own search accepts the key that opened the page."""
    client, _server, admin_auth = env
    kb_id = await _space_id(client, admin_auth)
    path = f"/api/knowledge-bases/{kb_id}/search?q=handbook"

    keyless = await create_user(client, admin_auth, username="kb_search_keyless", permissions=[])
    refused = await client.get(path, headers=keyless)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "FORBIDDEN"

    settings_auth = await create_user(
        client, admin_auth, username="kb_search_settings", permissions=["knowledge_settings"]
    )
    allowed = await client.get(path, headers=settings_auth)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json() == []


async def test_a_write_refusal_is_not_a_read_refusal(env: Any) -> None:
    """④: 403 says which permission was missing, not "no access" to everything."""
    client, server, admin_auth = env
    space_id = await _space_id(client, admin_auth)
    member = await create_user(
        client, admin_auth, username="kb_write_refused", permissions=["knowledge_bases"]
    )

    # The space is readable by everyone and owned by the system, so this account
    # may open it and may not change it: exactly the case the old wording got
    # wrong ("you do not have access to this knowledge base").
    readable = await client.get(f"/api/knowledge-bases/{space_id}", headers=member)
    assert readable.status_code == 200, readable.text
    refused = await client.patch(
        f"/api/knowledge-bases/{space_id}", headers=member, json={"name": "Renamed"}
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "KNOWLEDGE_WRITE_FORBIDDEN"

    # A base this account may not read at all keeps the access refusal.
    admin_id = int(server.services.user_repo.get_by_username("admin").id)
    private_id = await _private_base_id(server, admin_id)
    denied = await client.get(f"/api/knowledge-bases/{private_id}", headers=member)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "KNOWLEDGE_FORBIDDEN"


@pytest.mark.parametrize("disposition", ["inline", "attachment"])
async def test_original_download_preserves_bytes_and_disposition(
    env: Any, disposition: str
) -> None:
    client, server, auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    actor = await resolve_user_id(client, auth, "admin")
    service = KnowledgeService(services)
    kb_id = service.enterprise_space().id
    content = "# 原始资料\r\n\r\n保留原始编码与换行。\r\n".encode()
    document = service.upload_document(
        kb_id,
        actor_user_id=actor,
        filename="报告.md",
        path="originals/报告.md",
        content_type="text/markdown",
        content=content,
    )

    response = await client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document.id}/file",
        params={"disposition": disposition},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    assert response.content == content
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"].startswith(disposition + ";")
    assert "filename*=" in response.headers["content-disposition"]


async def test_original_download_missing_document_returns_not_found(env: Any) -> None:
    client, server, auth = env
    server.services.settings_repo.set("knowledge_bases_enabled", "true")
    kb_id = KnowledgeService(server.services).enterprise_space().id

    response = await client.get(
        f"/api/knowledge-bases/{kb_id}/documents/missing-original/file", headers=auth
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "KNOWLEDGE_NOT_FOUND"


async def test_render_non_cad_document_returns_unsupported_media_type(env: Any) -> None:
    client, server, auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    actor = await resolve_user_id(client, auth, "admin")
    service = KnowledgeService(services)
    kb_id = service.enterprise_space().id
    document = service.upload_document(
        kb_id,
        actor_user_id=actor,
        filename="notes.txt",
        content_type="text/plain",
        content=b"not a CAD drawing",
    )

    response = await client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document.id}/render", headers=auth
    )

    assert response.status_code == 415, response.text
    original = await client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{document.id}/file", headers=auth
    )
    assert original.status_code == 200, original.text
    assert original.content == b"not a CAD drawing"


async def test_cad_render_uses_current_source_bytes_before_next_scan(
    env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, server, auth = env
    services = server.services
    actor = await resolve_user_id(client, auth, "admin")
    kb_id = KnowledgeService(services).enterprise_space().id
    share = tmp_path / "external-cad"
    share.mkdir()
    original = share / "drawing.dwg"
    original.write_bytes(b"drawing revision one")
    source = DataSourceService(services).create(
        kb_id,
        actor_user_id=actor,
        name="Drawings",
        kind="local",
        folder=FolderSettings(root_path=str(share)),
    )
    stored_hash = document_digest(original.read_bytes())
    document = services.knowledge_repo.create_document(
        kb_id=kb_id,
        filename=original.name,
        path=f"{source.id}/{original.name}",
        data_source_id=source.id,
        source_path=original.name,
        content_type="application/acad",
        byte_size=original.stat().st_size,
        content_hash=stored_hash,
        status="discovered",
    )
    converter = tmp_path / "preview_converter.py"
    converter.write_text(
        r"""
import hashlib
import struct
import sys
import zlib
from pathlib import Path

def chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))

source = Path(sys.argv[1]).read_bytes()
image = b"\x89PNG\r\n\x1a\n"
image += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
image += chunk(b"IDAT", zlib.compress(b"\x00" + hashlib.sha256(source).digest()[:3]))
image += chunk(b"IEND", b"")
Path(sys.argv[2], "preview.png").write_bytes(image)
counter = Path(__file__).with_suffix(".calls")
counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OCTOP_DWG_PREVIEW_SCRIPT", str(converter))
    monkeypatch.setenv("OCTOP_DWG_PREVIEW_PYTHON", sys.executable)
    endpoint = f"/api/knowledge-bases/{kb_id}/documents/{document.id}/render"

    first = await client.get(endpoint, headers=auth)
    original.write_bytes(b"drawing revision two")
    second = await client.get(endpoint, headers=auth)
    repeated = await client.get(endpoint, headers=auth)

    assert first.status_code == second.status_code == repeated.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert first.content != second.content
    assert repeated.content == second.content
    assert converter.with_suffix(".calls").read_text() == "2"
    assert services.knowledge_repo.get_document(document.id).content_hash == stored_hash
