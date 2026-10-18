from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import tomllib
import uuid
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from packaging.version import InvalidVersion, Version

from . import crypto, storage
from .storage import audit, connect, now, transaction

MAX_ARCHIVE_SIZE = 512 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 20_000
_REQUIRED_FILES = {
    "pyproject.toml",
    "uv.lock",
    "docker/Dockerfile",
    "dashboard/package.json",
    "dashboard/package-lock.json",
}
_LEGACY_DB = storage.DATA_DIR / "releases.sqlite3"
_LEGACY_KEY = storage.DATA_DIR / "release-signing-key.pem"
_RELEASES_DIR = storage.DATA_DIR / "releases"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _migrate_legacy_distribution() -> None:
    """Fold the previous release database and signer into the one center database."""
    imported_key_path: Path | None = None
    if not storage.DB_PATH.is_file():
        raise RuntimeError("Center database must be migrated before release-store adoption")
    with transaction() as conn:
        imported = conn.execute(
            "SELECT 1 FROM center_imports WHERE source='legacy-release-center-v1'"
        ).fetchone()
        if imported is None:
            if _LEGACY_DB.is_file():
                try:
                    source = sqlite3.connect(_LEGACY_DB.as_uri() + "?mode=ro", uri=True)
                    source.row_factory = sqlite3.Row
                    rows = source.execute("SELECT * FROM releases").fetchall()
                except sqlite3.Error as exc:
                    raise RuntimeError("Cannot read the legacy release database safely") from exc
                finally:
                    if "source" in locals():
                        source.close()
                for row in rows:
                    conn.execute(
                        "INSERT INTO releases(id,version,filename,sha256,size,release_notes,"
                        "package_type,platform,architecture,published,created_at,published_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        tuple(
                            row[name]
                            for name in (
                                "id",
                                "version",
                                "filename",
                                "sha256",
                                "size",
                                "release_notes",
                                "package_type",
                                "platform",
                                "architecture",
                                "published",
                                "created_at",
                                "published_at",
                            )
                        ),
                    )
            if _LEGACY_KEY.is_file():
                try:
                    imported_key_path, public_pem = crypto.import_release_signing_key(
                        "maitu-release-1", _LEGACY_KEY.read_bytes()
                    )
                except (OSError, ValueError, TypeError) as exc:
                    raise RuntimeError("Cannot import the legacy release signing identity") from exc
                conn.execute(
                    "INSERT INTO release_signing_keys(kid,private_path,public_pem,state,created_at) "
                    "VALUES(?,?,?,'active',?)",
                    ("maitu-release-1", imported_key_path.name, public_pem, now()),
                )
            conn.execute(
                "INSERT INTO center_imports(source,imported_at) VALUES(?,?)",
                ("legacy-release-center-v1", now()),
            )
    if imported_key_path is not None:
        _LEGACY_KEY.unlink(missing_ok=True)

    with connect() as conn:
        rows = conn.execute("SELECT state FROM release_signing_keys").fetchall()
        active = any(row["state"] == "active" for row in rows)
    if not rows:
        kid = "maitu-release-1"
        path, public_pem = crypto.generate_release_signing_key(kid)
        with transaction() as conn:
            conn.execute(
                "INSERT INTO release_signing_keys(kid,private_path,public_pem,state,created_at) "
                "VALUES(?,?,?,'active',?)",
                (kid, path.name, public_pem, now()),
            )
            audit(
                conn,
                "local-system",
                "release_key.bootstrap",
                "release_signing_key",
                kid,
                "{}",
                json.dumps({"state": "active"}),
            )
    elif not active:
        raise RuntimeError(
            "Release signer has no active key; refusing to start or replace its identity"
        )


def _validate_archive(filename: str, content: bytes) -> dict[str, str]:
    match = re.fullmatch(r"maitu-(.+)\.zip", filename)
    if not match:
        raise ValueError("filename must be maitu-{PEP440_version}.zip")
    try:
        filename_version = Version(match.group(1))
    except InvalidVersion as exc:
        raise ValueError("filename version must be a valid PEP 440 version") from exc
    if len(content) > MAX_ARCHIVE_SIZE:
        raise OverflowError("archive exceeds upload size limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("upload is not a valid ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_ENTRIES:
            raise ValueError("archive entry count is invalid")
        seen: set[str] = set()
        total = 0
        for info in infos:
            name = info.orig_filename
            path = PurePosixPath(name)
            if (
                not name
                or "\x00" in name
                or "\\" in name
                or name.startswith("/")
                or path.is_absolute()
                or ":" in name
                or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
            ):
                raise ValueError("archive contains an unsafe path")
            normalized = name.rstrip("/").casefold()
            if normalized in seen:
                raise ValueError("archive contains duplicate paths")
            seen.add(normalized)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("archive contains a symbolic link")
            if stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError("archive contains a non-regular entry")
            if info.flag_bits & 0x1:
                raise ValueError("encrypted ZIP entries are not supported")
            if info.file_size < 0:
                raise ValueError("archive contains an invalid entry")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_SIZE:
                raise OverflowError("archive exceeds decompressed size limit")
        actual_total = 0
        try:
            for info in infos:
                with archive.open(info) as entry:
                    while chunk := entry.read(1024 * 1024):
                        actual_total += len(chunk)
                        if actual_total > MAX_UNCOMPRESSED_SIZE:
                            raise OverflowError("archive exceeds decompressed size limit")
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise ValueError("archive contains invalid compressed data") from exc
        files = {info.filename for info in infos if not info.is_dir()}
        missing = _REQUIRED_FILES - files
        if not any(name.startswith("src/octop/") for name in files):
            raise ValueError("archive is missing the src/octop project sources")
        if missing:
            raise ValueError(
                f"archive is missing required project files: {', '.join(sorted(missing))}"
            )
        if "maitu-release.json" not in files:
            raise ValueError("archive is missing maitu-release.json")
        if archive.getinfo("maitu-release.json").file_size > 1024 * 1024:
            raise ValueError("release metadata is too large")
        if archive.getinfo("pyproject.toml").file_size > 4 * 1024 * 1024:
            raise ValueError("pyproject.toml is too large")
        try:
            metadata = json.loads(archive.read("maitu-release.json"))
            project = tomllib.loads(archive.read("pyproject.toml").decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("archive release metadata or pyproject.toml is invalid") from exc
        if not isinstance(metadata, dict) or not isinstance(project.get("project"), dict):
            raise ValueError("release metadata and project table must be objects")
        version = metadata.get("version")
        project_version = project["project"].get("version")
        if not isinstance(version, str) or not isinstance(project_version, str):
            raise ValueError("release metadata and pyproject.toml require string project versions")
        try:
            parsed_version = Version(version)
            parsed_project_version = Version(project_version)
        except InvalidVersion as exc:
            raise ValueError("release metadata and pyproject.toml require valid versions") from exc
        if any(
            metadata.get(key) != value
            for key, value in {
                "product": "maitu",
                "package_type": "source",
                "platform": "linux",
            }.items()
        ):
            raise ValueError(
                "release metadata has an unsupported product, package_type, or platform"
            )
        architecture = metadata.get("architecture")
        if not isinstance(architecture, str) or architecture not in {"amd64", "arm64"}:
            raise ValueError("release architecture must be amd64 or arm64")
        if (
            version != match.group(1)
            or project_version != version
            or parsed_version != filename_version
            or parsed_project_version != filename_version
        ):
            raise ValueError("filename, release metadata, and pyproject.toml versions must match")
        notes = metadata.get("release_notes")
        if not isinstance(notes, str):
            raise ValueError("release_notes must be a string")
        return {
            "version": str(parsed_version),
            "release_notes": notes,
            "package_type": "source",
            "platform": "linux",
            "architecture": architecture,
        }


def _record(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["published"] = bool(result["published"])
    return result


def _release_keys() -> dict[str, str]:
    with connect() as conn:
        return {
            row["kid"]: row["public_pem"]
            for row in conn.execute(
                "SELECT kid,public_pem FROM release_signing_keys ORDER BY created_at,kid"
            ).fetchall()
        }


def _stage_release_key(actor: str, reason: str) -> dict[str, str]:
    kid = "release-" + secrets.token_hex(8)
    path, public_pem = crypto.generate_release_signing_key(kid)
    with transaction() as conn:
        conn.execute(
            "INSERT INTO release_signing_keys(kid,private_path,public_pem,state,created_at) "
            "VALUES(?,?,?,'staged',?)",
            (kid, path.name, public_pem, now()),
        )
        audit(
            conn,
            actor,
            "release_key.stage",
            "release_signing_key",
            kid,
            "{}",
            json.dumps({"public_pem": public_pem, "state": "staged"}),
            reason,
        )
    return {"kid": kid, "public_pem": public_pem, "state": "staged"}


def _activate_release_key(kid: str, actor: str, reason: str) -> dict[str, str]:
    with transaction() as conn:
        staged = conn.execute(
            "SELECT kid FROM release_signing_keys WHERE kid=? AND state='staged'", (kid,)
        ).fetchone()
        if staged is None:
            raise HTTPException(status_code=404, detail="Staged release signing key not found")
        old = [
            row["kid"]
            for row in conn.execute(
                "SELECT kid FROM release_signing_keys WHERE state='active'"
            ).fetchall()
        ]
        conn.execute("UPDATE release_signing_keys SET state='retired' WHERE state='active'")
        conn.execute("UPDATE release_signing_keys SET state='active' WHERE kid=?", (kid,))
        audit(
            conn,
            actor,
            "release_key.activate",
            "release_signing_key",
            kid,
            json.dumps({"active": old}),
            json.dumps({"active": kid}),
            reason,
        )
    return {"kid": kid, "state": "active"}


def create_router(
    require_roles: Callable[..., Any],
    csrf_check: Callable[[dict[str, Any], str | None], None],
    recent_mfa: Callable[[dict[str, Any]], None],
    client_token: str,
) -> APIRouter:
    router = APIRouter()
    operators = require_roles("owner", "operator")
    readers = require_roles("owner", "operator", "reader")

    async def client_auth(request: Request) -> None:
        if len(client_token) < 32:
            raise HTTPException(
                status_code=503, detail="Update client authentication is not configured"
            )
        authorization = request.headers.get("authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied.encode(), client_token.encode()):
            raise HTTPException(status_code=401, detail="Invalid client token")

    @router.get("/api/v1/releases/public-keys")
    async def release_public_keys() -> dict[str, Any]:
        return {"algorithm": "EdDSA", "keys": await asyncio.to_thread(_release_keys)}

    @router.post("/api/v1/admin/releases/{filename}", status_code=201)
    async def upload_release(
        filename: str,
        request: Request,
        user: dict[str, Any] = Depends(operators),
        csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> JSONResponse:
        csrf_check(user, csrf)
        recent_mfa(user)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/zip"
        ):
            raise HTTPException(status_code=415, detail="Content-Type must be application/zip")
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > MAX_ARCHIVE_SIZE:
                    raise HTTPException(status_code=413, detail="Archive exceeds upload size limit")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length") from None
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_ARCHIVE_SIZE:
                raise HTTPException(status_code=413, detail="Archive exceeds upload size limit")
            chunks.append(chunk)
        content = b"".join(chunks)
        try:
            metadata = await asyncio.to_thread(_validate_archive, filename, content)
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        release_id = uuid.uuid4().hex
        digest = hashlib.sha256(content).hexdigest()
        created_at = _utc_now()
        _RELEASES_DIR.mkdir(parents=True, exist_ok=True)
        archive_path = _RELEASES_DIR / f"{release_id}.zip"
        fd, temp_path = tempfile.mkstemp(prefix=".upload-", dir=_RELEASES_DIR)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, archive_path)
            with transaction() as conn:
                conn.execute(
                    "INSERT INTO releases(id,version,filename,sha256,size,release_notes,package_type,platform,architecture,published,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0,?)",
                    (
                        release_id,
                        metadata["version"],
                        filename,
                        digest,
                        len(content),
                        metadata["release_notes"],
                        metadata["package_type"],
                        metadata["platform"],
                        metadata["architecture"],
                        created_at,
                    ),
                )
                row = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
            return JSONResponse(_record(row), status_code=201)
        except sqlite3.IntegrityError as exc:
            archive_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=409,
                detail="A release with this version and architecture already exists",
            ) from exc
        except BaseException:
            archive_path.unlink(missing_ok=True)
            raise
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @router.get("/api/v1/admin/releases")
    async def list_releases(_: dict[str, Any] = Depends(readers)) -> list[dict[str, Any]]:
        def query() -> list[dict[str, Any]]:
            with connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM releases ORDER BY created_at DESC,id DESC"
                ).fetchall()
            return [_record(row) for row in rows]

        return await asyncio.to_thread(query)

    @router.post("/api/v1/admin/releases/{release_id}/publish")
    async def publish_release(
        release_id: str,
        request: Request,
        user: dict[str, Any] = Depends(operators),
        csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict[str, Any]:
        csrf_check(user, csrf)
        recent_mfa(user)
        body = await request.json()
        reason = body.get("reason") if isinstance(body, dict) else None
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise HTTPException(status_code=400, detail="A publication reason is required")
        with transaction() as conn:
            row = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Release not found")
            if row["published"]:
                raise HTTPException(status_code=409, detail="Published releases are immutable")
            published_at = _utc_now()
            conn.execute(
                "UPDATE releases SET published=1,published_at=? WHERE id=? AND published=0",
                (published_at, release_id),
            )
            updated = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
            audit(
                conn,
                user["username"],
                "release.publish",
                "release",
                release_id,
                json.dumps(_record(row)),
                json.dumps(_record(updated)),
                reason.strip(),
            )
        return _record(updated)

    @router.get("/api/v1/releases/latest", dependencies=[Depends(client_auth)])
    async def latest_release(platform: str, architecture: str) -> dict[str, str | None]:
        if platform != "linux" or architecture not in {"amd64", "arm64"}:
            raise HTTPException(status_code=400, detail="Unsupported release target")
        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM releases WHERE published=1 AND platform=? AND architecture=?",
                (platform, architecture),
            ).fetchall()
        if not rows:
            return {"manifest": None}
        newest = max(rows, key=lambda row: Version(row["version"]))
        claims = {
            "iss": "maitu-license-center",
            "aud": "maitu-updater",
            "product": "maitu",
            "protocol": 1,
            "release_id": newest["id"],
            "version": newest["version"],
            "filename": newest["filename"],
            "sha256": newest["sha256"],
            "size": newest["size"],
            "release_notes": newest["release_notes"],
            "package_type": newest["package_type"],
            "platform": newest["platform"],
            "architecture": newest["architecture"],
            "iat": int(
                datetime.fromisoformat(newest["published_at"].replace("Z", "+00:00")).timestamp()
            ),
        }
        return {"manifest": await asyncio.to_thread(crypto.sign_release, claims)}

    @router.get("/api/v1/releases/{release_id}/download", dependencies=[Depends(client_auth)])
    async def download_release(release_id: str) -> FileResponse:
        with connect() as conn:
            row = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if row is None or not row["published"]:
            raise HTTPException(status_code=404, detail="Published release not found")
        path = _RELEASES_DIR / f"{release_id}.zip"
        if not path.is_file():
            raise HTTPException(status_code=500, detail="Published release archive is unavailable")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=row["filename"],
            headers={
                "ETag": f'"{row["sha256"]}"',
                "Cache-Control": "private, max-age=31536000, immutable",
            },
        )

    @router.get("/api/v1/admin/release-signing-keys")
    async def release_signing_keys(_: dict[str, Any] = Depends(readers)) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT kid,public_pem,state,created_at FROM release_signing_keys ORDER BY created_at,kid"
            ).fetchall()
        return [dict(row) for row in rows]

    @router.post("/api/v1/admin/release-signing-keys/stage")
    async def stage_release_key(
        request: Request,
        user: dict[str, Any] = Depends(require_roles("owner")),
        csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict[str, str]:
        csrf_check(user, csrf)
        recent_mfa(user)
        body = await request.json()
        reason = body.get("reason") if isinstance(body, dict) else None
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise HTTPException(status_code=400, detail="A key staging reason is required")
        return await asyncio.to_thread(_stage_release_key, user["username"], reason.strip())

    @router.post("/api/v1/admin/release-signing-keys/{kid}/activate")
    async def activate_release_key(
        kid: str,
        request: Request,
        user: dict[str, Any] = Depends(require_roles("owner")),
        csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict[str, str]:
        csrf_check(user, csrf)
        recent_mfa(user)
        body = await request.json()
        reason = body.get("reason") if isinstance(body, dict) else None
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise HTTPException(status_code=400, detail="A key activation reason is required")
        return await asyncio.to_thread(_activate_release_key, kid, user["username"], reason.strip())

    return router
