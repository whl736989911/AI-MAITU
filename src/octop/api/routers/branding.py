"""Runtime brand settings and uploaded brand assets."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from octop.api.deps import get_server, require_admin
from octop.branding_defaults import DEFAULTS

router = APIRouter()

SLOTS = frozenset(DEFAULTS["logos"])
MAX_LOGO_BYTES = 5 * 1024 * 1024
BRAND_SETTINGS_KEY = "runtime_branding"
_SLOT_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _state(server: Any) -> dict[str, Any]:
    repo = server.services.settings_repo if server.services else None
    try:
        custom = json.loads((repo.get(BRAND_SETTINGS_KEY) if repo else None) or "{}")
    except (TypeError, ValueError):
        custom = {}
    value: dict[str, Any] = {group: fields.copy() for group, fields in DEFAULTS.items()}
    for group in ("name", "full_name", "short_name", "description", "colors", "pwa"):
        value[group].update(custom.get(group, {}))
    stored_logos = custom.get("logos", {})
    for slot, filename in stored_logos.items():
        if (
            slot in SLOTS
            and isinstance(filename, str)
            and re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|webp|svg)", filename)
        ):
            value["logos"][slot] = f"/api/branding/assets/{slot}/{filename}"
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value["version"] = hashlib.sha256(serialized.encode()).hexdigest()[:16]
    value["is_custom"] = bool(custom)
    return value


def _validate_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) > 500:
        raise HTTPException(status_code=422, detail=f"Invalid {field}")
    return value.strip()


def _merge(server: Any, data: dict[str, Any]) -> dict[str, Any]:
    stored_raw = server.services.settings_repo.get(BRAND_SETTINGS_KEY)
    try:
        custom = json.loads(stored_raw or "{}")
    except (TypeError, ValueError):
        custom = {}
    for group in ("name", "full_name", "short_name", "description", "colors", "pwa"):
        if group not in data:
            continue
        supplied = data[group]
        if supplied is None:
            custom.pop(group, None)
            continue
        if not isinstance(supplied, dict):
            raise HTTPException(status_code=422, detail=f"Invalid {group}")
        custom_group = custom.setdefault(group, {})
        for key, val in supplied.items():
            if key not in DEFAULTS[group]:
                raise HTTPException(status_code=422, detail=f"Unknown {group} field")
            if val is None or val == "":
                custom_group.pop(key, None)
            elif group == "colors" or group == "pwa":
                if not isinstance(val, str) or not _COLOR_RE.fullmatch(val):
                    raise HTTPException(status_code=422, detail=f"Invalid color: {key}")
                custom_group[key] = val.upper()
            else:
                value = _validate_text(val, f"{group}.{key}")
                if value:
                    custom_group[key] = value
                else:
                    custom_group.pop(key, None)
        if not custom_group:
            custom.pop(group, None)
    if "logos" in data:
        supplied_logos = data["logos"]
        if supplied_logos is None:
            custom.pop("logos", None)
        elif not isinstance(supplied_logos, dict):
            raise HTTPException(status_code=422, detail="Invalid logos")
        else:
            logos = custom.setdefault("logos", {})
            for slot, url in supplied_logos.items():
                if slot not in SLOTS:
                    raise HTTPException(status_code=422, detail=f"Unknown logo slot: {slot}")
                if url is None or url == "" or url == DEFAULTS["logos"][slot]:
                    logos.pop(slot, None)
                else:
                    m = re.fullmatch(
                        r"/api/branding/assets/"
                        + re.escape(slot)
                        + r"/([a-f0-9]{32}\.(png|jpg|webp|svg))(?:\?v=[a-f0-9]+)?",
                        str(url),
                    )
                    if not m:
                        raise HTTPException(status_code=422, detail=f"Invalid logo URL: {slot}")
                    logos[slot] = m.group(1)
            if not logos:
                custom.pop("logos", None)
    if custom:
        server.services.settings_repo.set(
            BRAND_SETTINGS_KEY, json.dumps(custom, ensure_ascii=False)
        )
    else:
        server.services.settings_repo.delete(BRAND_SETTINGS_KEY)
    return _state(server)


def _apply_api_title(request: Request, brand: dict[str, Any]) -> None:
    request.app.title = f"{brand['name']['en']} API"
    request.app.openapi_schema = None


@router.get("/branding")
async def get_branding(server: Any = Depends(get_server)) -> dict[str, Any]:
    """Public brand configuration; contains no secrets."""
    return _state(server)


@router.put("/branding")
async def put_branding(
    body: dict[str, Any],
    request: Request,
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    brand = _merge(server, body)
    _apply_api_title(request, brand)
    return brand


@router.delete("/branding")
async def delete_branding(
    request: Request,
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    server.services.settings_repo.delete(BRAND_SETTINGS_KEY)
    brand = _state(server)
    _apply_api_title(request, brand)
    return brand


@router.post("/branding/logo/{slot}")
async def upload_brand_logo(
    slot: str,
    file: UploadFile = File(...),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, str]:
    if slot not in SLOTS:
        raise HTTPException(status_code=404, detail="Unknown logo slot")
    mime = (file.content_type or "").split(";")[0].lower()
    extension = _SLOT_EXTENSIONS.get(mime)
    if extension is None:
        raise HTTPException(status_code=415, detail="Logo must be PNG, JPEG, SVG, or WebP")
    payload = await file.read(MAX_LOGO_BYTES + 1)
    if not payload or len(payload) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail="Logo exceeds 5 MiB")
    if mime == "image/png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=415, detail="Invalid PNG file")
    if mime == "image/jpeg" and not payload.startswith(b"\xff\xd8\xff"):
        raise HTTPException(status_code=415, detail="Invalid JPEG file")
    if mime == "image/webp" and not (payload.startswith(b"RIFF") and payload[8:12] == b"WEBP"):
        raise HTTPException(status_code=415, detail="Invalid WebP file")
    if mime == "image/svg+xml":
        if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
            raise HTTPException(status_code=415, detail="SVG document types are not allowed")
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise HTTPException(status_code=415, detail="Invalid SVG file") from exc
        if root.tag.rsplit("}", 1)[-1] != "svg":
            raise HTTPException(status_code=415, detail="Expected an SVG root")
        unsafe = {"script", "foreignObject", "iframe", "object", "embed", "style"}
        if any(el.tag.rsplit("}", 1)[-1] in unsafe for el in root.iter()) or any(
            key.lower().startswith("on")
            or "url(" in val.lower()
            or (
                key.lower() in {"href", "{http://www.w3.org/1999/xlink}href"}
                and val.strip().lower().startswith(("http:", "https:", "javascript:", "data:"))
            )
            for el in root.iter()
            for key, val in el.attrib.items()
        ):
            raise HTTPException(status_code=415, detail="Unsafe SVG content")
    filename = hashlib.sha256(payload).hexdigest()[:32] + extension
    asset_dir = server.paths.root / "branding"
    asset_dir.mkdir(parents=True, exist_ok=True)
    asset_path = asset_dir / filename
    if not asset_path.exists():
        asset_path.write_bytes(payload)
    saved = server.services.settings_repo.get(BRAND_SETTINGS_KEY)
    try:
        custom = json.loads(saved or "{}")
    except (TypeError, ValueError):
        custom = {}
    custom.setdefault("logos", {})[slot] = filename
    server.services.settings_repo.set(BRAND_SETTINGS_KEY, json.dumps(custom, ensure_ascii=False))
    version = hashlib.sha256(payload).hexdigest()[:16]
    return {"url": f"/api/branding/assets/{slot}/{filename}?v={version}"}


@router.get("/branding/assets/{slot}/{filename}")
async def get_brand_asset(
    slot: str,
    filename: str,
    request: Request,
    server: Any = Depends(get_server),
) -> Response:
    if slot not in SLOTS or not re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|webp|svg)", filename):
        raise HTTPException(status_code=404, detail="Brand asset not found")
    path = (server.paths.root / "branding" / filename).resolve()
    if not path.is_relative_to((server.paths.root / "branding").resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="Brand asset not found")
    payload = path.read_bytes()
    etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }[path.suffix]
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(payload, media_type=media_type, headers=headers)


@router.get("/branding/manifest.webmanifest", response_model=None)
async def branding_manifest(
    request: Request,
    server: Any = Depends(get_server),
) -> JSONResponse | Response:
    brand = _state(server)
    icon_types = {
        slot: {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
        }.get(Path(url.split("?", 1)[0]).suffix, "image/png")
        for slot, url in brand["logos"].items()
    }
    manifest = {
        "name": brand["name"]["zh"],
        "short_name": brand["short_name"]["zh"],
        "description": brand["description"]["zh"],
        "start_url": "/",
        "display": "standalone",
        "theme_color": brand["pwa"]["theme_color"],
        "background_color": brand["pwa"]["background_color"],
        "icons": [
            {
                "src": brand["logos"]["pwa_192"],
                "sizes": "192x192",
                "type": icon_types["pwa_192"],
            },
            {
                "src": brand["logos"]["pwa_512"],
                "sizes": "512x512",
                "type": icon_types["pwa_512"],
                "purpose": "any maskable",
            },
        ],
    }
    body = json.dumps(manifest, ensure_ascii=False)
    etag = '"' + hashlib.sha256((brand["version"] + body).encode()).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        manifest,
        headers=headers,
        media_type="application/manifest+json",
    )
