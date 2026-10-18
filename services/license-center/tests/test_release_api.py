from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from helpers import reauthenticate

from license_center import api


def source_zip(version: str, architecture: str = "amd64", *, unsafe: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pyproject.toml", f'[project]\nname="octop"\nversion="{version}"\n')
        for name in (
            "uv.lock",
            "docker/Dockerfile",
            "dashboard/package.json",
            "dashboard/package-lock.json",
            "src/octop/__init__.py",
        ):
            archive.writestr(name, "test source")
        archive.writestr(
            "maitu-release.json",
            json.dumps(
                {
                    "product": "maitu",
                    "version": version,
                    "package_type": "source",
                    "platform": "linux",
                    "architecture": architecture,
                    "release_notes": f"Release {version}",
                }
            ),
        )
        if unsafe:
            archive.writestr("../escape", "bad")
    return output.getvalue()


def upload(client, csrf, version: str, payload: bytes | None = None):
    return client.post(
        f"/api/v1/admin/releases/maitu-{version}.zip",
        content=payload if payload is not None else source_zip(version),
        headers={**csrf, "Content-Type": "application/zip"},
    )


def decode_manifest(client, token: str) -> dict:
    header, payload, signature = token.split(".")

    def decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    keys = client.get("/api/v1/releases/public-keys").json()["keys"]
    key_id = json.loads(decode(header))["kid"]
    key = serialization.load_pem_public_key(keys[key_id].encode())
    signed = f"{header}.{payload}".encode("ascii")
    key.verify(decode(signature), signed)
    with pytest.raises(InvalidSignature):
        key.verify(decode(signature), signed + b"tampered")
    return json.loads(decode(payload))


def test_canonical_release_upload_auth_archive_and_draft_boundary(center):
    client, csrf = center
    content = source_zip("1.2.0")
    with TestClient(api.app, base_url="https://127.0.0.1") as anonymous:
        assert (
            anonymous.post(
                "/api/v1/admin/releases/maitu-1.2.0.zip",
                content=content,
                headers={"Content-Type": "application/zip"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/admin/releases/maitu-1.2.0.zip",
            content=content,
            headers={"Content-Type": "application/zip"},
        ).status_code
        == 403
    )
    unsafe = upload(client, csrf, "1.2.0", source_zip("1.2.0", unsafe=True))
    assert unsafe.status_code == 400
    mismatch = upload(client, csrf, "1.2.0", source_zip("1.2.1"))
    assert mismatch.status_code == 400
    assert upload(client, csrf, "1.2.1", b"not a ZIP archive").status_code == 400

    draft_response = upload(client, csrf, "1.2.0")
    assert draft_response.status_code == 201, draft_response.text
    draft = draft_response.json()
    assert draft["published"] is False
    assert client.get(f"/api/v1/releases/{draft['id']}/download").status_code == 401
    assert (
        client.get("/api/v1/releases/latest?platform=linux&architecture=amd64").status_code == 401
    )
    client_headers = {
        "Authorization": "Bearer " + "client-token-that-is-long-enough-for-safe-testing-01"
    }
    assert client.get(
        "/api/v1/releases/latest?platform=linux&architecture=amd64", headers=client_headers
    ).json() == {"manifest": None}


def test_canonical_publish_signed_manifest_order_and_immutable_download(center):
    client, csrf = center
    older_zip = source_zip("1.9.0")
    newer_zip = source_zip("1.10.0")
    older_response = upload(client, csrf, "1.9.0", older_zip)
    newer_response = upload(client, csrf, "1.10.0", newer_zip)
    assert older_response.status_code == newer_response.status_code == 201
    older, newer = older_response.json(), newer_response.json()
    rotated_csrf = reauthenticate(client, csrf)
    stale_csrf = client.post(
        f"/api/v1/admin/releases/{older['id']}/publish",
        headers=csrf,
        json={"reason": "Publish 1.9.0"},
    )
    assert stale_csrf.status_code == 403
    assert (
        client.post(
            f"/api/v1/admin/releases/{older['id']}/publish",
            headers=rotated_csrf,
            json={},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/v1/admin/releases/{older['id']}/publish",
            headers=rotated_csrf,
            json={"reason": "Publish 1.9.0"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/admin/releases/{newer['id']}/publish",
            headers=rotated_csrf,
            json={"reason": "Publish 1.10.0"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/admin/releases/{newer['id']}/publish",
            headers=rotated_csrf,
            json={"reason": "Duplicate publication"},
        ).status_code
        == 409
    )

    client_headers = {
        "Authorization": "Bearer " + "client-token-that-is-long-enough-for-safe-testing-01"
    }
    response = client.get(
        "/api/v1/releases/latest?platform=linux&architecture=amd64", headers=client_headers
    )
    assert response.status_code == 200
    claims = decode_manifest(client, response.json()["manifest"])
    assert claims["version"] == "1.10.0"
    assert claims["iss"] == "maitu-license-center" and claims["aud"] == "maitu-updater"
    assert claims["protocol"] == 1 and claims["product"] == "maitu"
    assert claims["sha256"] == hashlib.sha256(newer_zip).hexdigest()
    assert claims["size"] == len(newer_zip)
    downloaded = client.get(f"/api/v1/releases/{newer['id']}/download", headers=client_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == newer_zip
    assert downloaded.headers["etag"] == f'"{hashlib.sha256(newer_zip).hexdigest()}"'
    assert client.get(f"/api/v1/releases/{newer['id']}/download").status_code == 401


def test_release_signing_keyring_is_stable_across_app_restart(center):
    client, _ = center
    before = client.get("/api/v1/releases/public-keys").json()
    with TestClient(api.app, base_url="https://127.0.0.1") as restarted:
        after = restarted.get("/api/v1/releases/public-keys").json()
    assert before == after
    assert before["algorithm"] == "EdDSA"
    assert set(before["keys"]) == {"maitu-release-1"}
