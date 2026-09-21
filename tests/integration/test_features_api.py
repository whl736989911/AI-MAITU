"""End-to-end: authoring feature definitions over HTTP.

This is the settings UI's path: a definition is posted, lands in the user's own
feature directory, and the running server serves it without a restart. The
bundled library stays read-only throughout.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from octop.infra.features import default_library_root
from octop.infra.server import OctopServer
from tests.support.auth import create_user

BUNDLED_ID = "meeting-notes"
SYSTEM_PROMPT = "Write the summary in three sections."


def _definition(feature_id: str = "weekly-report", **overrides: Any) -> dict[str, Any]:
    """A definition exactly as the settings UI submits it."""
    payload: dict[str, Any] = {
        "id": feature_id,
        "label": {"zh": "周报", "en": "Weekly report"},
        "description": {"zh": "把零散记录整理成周报", "en": "Turn notes into a weekly report"},
        "icon_name": "clipboard-list",
        "unit": "general",
        "input_schema": {
            "type": "object",
            "required": ["notes"],
            "properties": {
                "notes": {
                    "type": "string",
                    "format": "textarea",
                    "title": {"zh": "记录", "en": "Notes"},
                },
            },
        },
        "ui_schema": {"order": ["notes"], "widgets": {"notes": "textarea"}},
        "prompt": {"user_template": "整理成周报：\n{{inputs}}", "system_prompt": SYSTEM_PROMPT},
        "output": {"kind": "markdown"},
        "permissions": {"allow_units": ["*"]},
    }
    payload.update(overrides)
    return payload


async def test_created_feature_is_written_and_served(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env

    response = await client.post("/api/features", headers=auth, json=_definition())

    assert response.status_code == 201, response.text
    assert response.json() == {"feature_id": "weekly-report"}
    feature_dir = srv.paths.features_dir / "weekly-report"
    assert (feature_dir / "feature.json").is_file()
    assert (feature_dir / "PROMPT.md").read_text(encoding="utf-8") == SYSTEM_PROMPT

    detail = (await client.get("/api/features/weekly-report", headers=auth)).json()
    assert detail["label"] == {"zh": "周报", "en": "Weekly report"}
    assert detail["unit"] == "general"
    assert detail["output_kind"] == "markdown"
    assert detail["user_template"] == "整理成周报：\n{{inputs}}"
    assert detail["system_prompt"] == SYSTEM_PROMPT
    assert detail["input_schema"]["required"] == ["notes"]

    cards = (await client.get("/api/features", headers=auth)).json()
    assert "weekly-report" in [card["id"] for card in cards["features"]]
    assert {"key": "general", "count": 2} in cards["units"]


async def test_updated_and_deleted_feature_is_reflected_immediately(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env
    await client.post("/api/features", headers=auth, json=_definition())

    response = await client.put(
        "/api/features/weekly-report",
        headers=auth,
        json=_definition(
            unit="support",
            prompt={"user_template": "换成周报格式：{{inputs}}"},
            output={"kind": "text"},
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"feature_id": "weekly-report"}
    detail = (await client.get("/api/features/weekly-report", headers=auth)).json()
    assert detail["unit"] == "support"
    assert detail["output_kind"] == "text"
    assert detail["system_prompt"] is None
    assert not (srv.paths.features_dir / "weekly-report" / "PROMPT.md").exists()

    deleted = await client.delete("/api/features/weekly-report", headers=auth)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert not (srv.paths.features_dir / "weekly-report").exists()
    gone = await client.get("/api/features/weekly-report", headers=auth)
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "NOT_FOUND"


async def test_invalid_definition_is_refused_without_writing(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env

    response = await client.post(
        "/api/features",
        headers=auth,
        json=_definition(input_schema={"type": "array", "properties": {"a": {"type": "string"}}}),
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "FEATURE_INVALID"
    assert "input_schema.type must be 'object'" in error["message"]
    assert not (srv.paths.features_dir / "weekly-report").exists()
    cards = (await client.get("/api/features", headers=auth)).json()
    assert "weekly-report" not in [card["id"] for card in cards["features"]]


async def test_bundled_feature_cannot_be_modified_or_deleted(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """A shipped definition is refused, never copied into the user's directory."""
    client, srv, auth = env
    shipped = default_library_root() / BUNDLED_ID / "feature.json"
    before = shipped.read_text(encoding="utf-8")

    updated = await client.put(
        f"/api/features/{BUNDLED_ID}",
        headers=auth,
        json=_definition(BUNDLED_ID),
    )
    deleted = await client.delete(f"/api/features/{BUNDLED_ID}", headers=auth)

    assert updated.status_code == 403, updated.text
    assert deleted.status_code == 403, deleted.text
    assert updated.json()["error"]["code"] == "FORBIDDEN"
    assert shipped.read_text(encoding="utf-8") == before
    assert not (srv.paths.features_dir / BUNDLED_ID).exists()
    detail = (await client.get(f"/api/features/{BUNDLED_ID}", headers=auth)).json()
    assert detail["unit"] == "general"


async def test_member_with_the_features_permission_cannot_author(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """Authoring is instance-wide configuration — members run features, not edit them."""
    client, srv, auth = env
    alice = await create_user(client, auth, username="alice")
    await client.post("/api/features", headers=auth, json=_definition())

    assert (await client.get("/api/features", headers=alice)).status_code == 200
    created = await client.post("/api/features", headers=alice, json=_definition("alice-feature"))
    updated = await client.put(
        "/api/features/weekly-report",
        headers=alice,
        json=_definition(prompt={"user_template": "x"}),
    )
    deleted = await client.delete("/api/features/weekly-report", headers=alice)

    assert [r.status_code for r in (created, updated, deleted)] == [403, 403, 403]
    assert created.json()["error"]["code"] == "FORBIDDEN"
    assert not (srv.paths.features_dir / "alice-feature").exists()


async def test_meta_offers_the_editors_choices(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _srv, auth = env

    response = await client.get("/api/features/_meta", headers=auth)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["output_kinds"] == ["markdown", "json", "text"]
    assert "file-text" in payload["icons"]
    assert "general" in payload["units"] and "sales" in payload["units"]
    assert payload["bundled_ids"] == ["meeting-notes", "quote-draft"]


@pytest.mark.parametrize("path", ["/api/features", "/api/features/meeting-notes"])
async def test_member_without_the_features_permission_is_refused(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
    path: str,
) -> None:
    client, _srv, auth = env
    alice = await create_user(client, auth, username="alice", permissions=[])

    response = await client.get(path, headers=alice)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
