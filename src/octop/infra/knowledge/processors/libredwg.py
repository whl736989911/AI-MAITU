"""Optional LibreDWG CLI adapter for genuine DWG text extraction."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from octop.infra.knowledge.parse import ParsedBlock, ParsedDocument

_ENV = "OCTOP_LIBREDWG_DWGREAD"
_TIMEOUT_SECONDS = 120
_MAX_JSON_BYTES = 128 * 1024 * 1024
_TEXT_ENTITIES = frozenset({"TEXT", "MTEXT", "ATTRIB", "ATTDEF"})


class CadProcessorUnavailable(RuntimeError):
    """LibreDWG's converter is not installed/configured."""


class CadProcessingFailed(RuntimeError):
    """LibreDWG failed to extract valid CAD text."""


def find_dwgread() -> str | None:
    configured = os.environ.get(_ENV, "").strip()
    if configured:
        candidate = Path(configured)
        return str(candidate) if candidate.is_file() else None
    return shutil.which("dwgread") or shutil.which("dwgread.exe")


def capability() -> dict[str, object]:
    return {"id": "libredwg", "available": find_dwgread() is not None}


def parse_dwg(path: Path, *, source_path: str) -> ParsedDocument:
    binary = find_dwgread()
    if binary is None:
        raise CadProcessorUnavailable(
            f"DWG extraction unavailable: install LibreDWG 0.14 and configure {_ENV}"
        )
    with tempfile.TemporaryDirectory(prefix="octop-libredwg-") as directory:
        output = Path(directory) / "drawing.json"
        try:
            result = subprocess.run(
                [binary, "-O", "JSON", "-o", str(output), str(path)],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CadProcessingFailed(f"LibreDWG JSON extraction failed: {exc}") from exc
        if result.returncode or not output.is_file():
            detail = (result.stderr or result.stdout).strip()[:1000]
            raise CadProcessingFailed(
                f"LibreDWG JSON extraction failed (exit {result.returncode}): {detail}"
            )
        if output.stat().st_size > _MAX_JSON_BYTES:
            raise CadProcessingFailed("LibreDWG JSON output exceeds the 128 MiB safety limit")
        try:
            data = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CadProcessingFailed(f"LibreDWG emitted invalid JSON: {exc}") from exc
    return _parsed_json(path.name, data, source_path)


def parse_dxf(path: Path, *, source_path: str) -> ParsedDocument:
    try:
        return _parsed(path.name, path.read_text(encoding="utf-8", errors="replace"), source_path)
    except OSError as exc:
        raise CadProcessingFailed(f"DXF read failed: {exc}") from exc


def _parsed_json(filename: str, data: Any, source_path: str) -> ParsedDocument:
    if not isinstance(data, dict) or not isinstance(data.get("OBJECTS"), list):
        raise CadProcessingFailed("LibreDWG JSON has no OBJECTS inventory")
    objects = [value for value in data["OBJECTS"] if isinstance(value, dict)]
    by_handle = {
        handle: obj for obj in objects if (handle := _stored_handle(obj.get("handle"))) is not None
    }
    layouts: dict[int, str] = {}
    for obj in objects:
        if obj.get("object") == "LAYOUT":
            block_handle = _reference_handle(obj.get("block_header"))
            name = _name(obj.get("layout_name"))
            if block_handle is not None and name:
                layouts[block_handle] = name
    layout_names = tuple(
        dict.fromkeys(
            name
            for obj in objects
            if obj.get("object") == "LAYOUT"
            if (name := _name(obj.get("layout_name")))
        )
    )
    blocks: list[ParsedBlock] = []
    for obj in objects:
        entity_value = obj.get("entity")
        entity = entity_value.upper() if isinstance(entity_value, str) else ""
        if entity not in _TEXT_ENTITIES:
            continue
        text_key = "text" if entity == "MTEXT" else "text_value"
        value = obj.get(text_key)
        if not isinstance(value, str) or not value.strip():
            continue
        owner_handle = _reference_handle(obj.get("ownerhandle"))
        owner = by_handle.get(owner_handle) if owner_handle is not None else None
        entmode = obj.get("entmode")
        layout = layouts.get(owner_handle) if owner_handle is not None else None
        coordinate_space = "drawing"
        if entmode == 0:
            coordinate_space = "block_local"
        elif entmode == 2:
            coordinate_space = "model_space"
            layout = next((name for name in layout_names if name.casefold() == "model"), layout)
        elif entmode == 1:
            coordinate_space = "paper_space"
        layer_handle = _reference_handle(obj.get("layer"))
        layer_obj = by_handle.get(layer_handle) if layer_handle is not None else None
        layer = _name(obj.get("layer"))
        if layer_obj is not None:
            layer = _name(layer_obj.get("name"))
        blocks.append(
            _cad_block(
                entity=entity,
                text=_normalize_mtext(value) if entity == "MTEXT" else value.strip(),
                handle=obj.get("handle"),
                layer=layer,
                layout=layout,
                position=obj.get("ins_pt"),
                coordinate_space=coordinate_space,
                block=_name(owner.get("name")) if owner is not None else None,
                ordinal=len(blocks),
            )
        )
    if not blocks:
        raise CadProcessingFailed("LibreDWG JSON contained no extractable TEXT/MTEXT entities")
    return ParsedDocument(
        text="\n".join(block.text for block in blocks),
        filename=filename,
        source_path=source_path,
        sections=layout_names,
        blocks=tuple(blocks),
    )


def _stored_handle(value: Any) -> int | None:
    if isinstance(value, list) and len(value) >= 3 and isinstance(value[2], int):
        return value[2]
    if isinstance(value, int):
        return value
    return None


def _reference_handle(value: Any) -> int | None:
    if isinstance(value, list) and len(value) >= 4 and isinstance(value[3], int):
        return value[3]
    if isinstance(value, int):
        return value
    return None


def _normalize_mtext(value: str) -> str:
    text = re.sub(r"\\[fFhHcCaAqQwWtT][^;]*;", "", value)
    text = text.replace("\\P", "\n").replace("\\~", " ")
    text = text.replace("%%C", "Ø").replace("%%c", "Ø")
    text = text.replace("%%P", "±").replace("%%p", "±")
    text = text.replace("%%D", "°").replace("%%d", "°")
    text = re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda match: chr(int(match.group(1), 16)), text)
    return text.replace("{", "").replace("}", "").strip()


def _name(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        for key in ("name", "layout_name", "layer_name", "value"):
            result = value.get(key)
            if isinstance(result, str) and result:
                return result
    return None


def _cad_block(
    *,
    entity: str,
    text: str,
    handle: Any,
    layer: Any,
    layout: Any,
    position: Any,
    coordinate_space: str,
    block: str | None,
    ordinal: int,
) -> ParsedBlock:
    layer_name = _name(layer)
    layout_name = _name(layout)
    locator: dict[str, object] = {
        "kind": "cad_entity",
        "entity": entity,
        "ordinal": ordinal,
        "coordinate_space": coordinate_space,
    }
    if isinstance(handle, list) and len(handle) >= 3:
        locator["handle"] = str(handle[2])
    elif isinstance(handle, (str, int)):
        locator["handle"] = str(handle)
    if layer_name:
        locator["layer"] = layer_name
    if layout_name:
        locator["layout"] = layout_name
    if block:
        locator["block"] = block
    if isinstance(position, list):
        axes = ("x", "y", "z")
        coordinates = {axes[i]: value for i, value in enumerate(position[:3])}
        if coordinates:
            locator["position"] = coordinates
    elif isinstance(position, dict):
        coordinates = {axis: position[axis] for axis in ("x", "y", "z") if axis in position}
        if coordinates:
            locator["position"] = coordinates
    heading = layout_name or block or layer_name or ""
    return ParsedBlock(text, "cad_text", locator, heading=heading)


def _parsed(filename: str, dxf: str, source_path: str) -> ParsedDocument:
    blocks = _text_entities(dxf)
    text = "\n".join(block.text for block in blocks)
    if not text.strip():
        raise CadProcessingFailed("CAD file has no extractable TEXT/MTEXT entities")
    return ParsedDocument(
        text=text,
        source_path=source_path,
        filename=filename,
        sections=tuple(dict.fromkeys(str(block.locator["layer"]) for block in blocks)),
        blocks=tuple(blocks),
    )


def _text_entities(dxf: str) -> list[ParsedBlock]:
    lines = dxf.splitlines()
    pairs = [(lines[i].strip(), lines[i + 1].rstrip()) for i in range(0, len(lines) - 1, 2)]
    blocks: list[ParsedBlock] = []
    entity = ""
    values: dict[str, list[str]] = {}

    def emit() -> None:
        if entity not in _TEXT_ENTITIES:
            return
        # DXF MTEXT stores the leading chunks in group 3 and its final chunk in group 1.
        raw = "".join(part for code in ("3", "1") for part in values.get(code, []))
        clean = _normalize_mtext(raw) if entity == "MTEXT" else raw.strip()
        if not clean.strip():
            return
        layer = (values.get("8") or ["0"])[-1]
        handle = (values.get("5") or [str(len(blocks) + 1)])[-1]
        x = (values.get("10") or [""])[-1]
        y = (values.get("20") or [""])[-1]
        locator: dict[str, object] = {
            "kind": "cad_entity",
            "entity": entity,
            "handle": handle,
            "layer": layer,
        }
        if x and y:
            locator["position"] = {"x": x, "y": y}
        blocks.append(ParsedBlock(clean.strip(), "cad_text", locator, heading=layer))

    for code, value in pairs:
        if code == "0":
            emit()
            entity, values = value.strip(), {}
        elif entity:
            values.setdefault(code, []).append(value.strip())
    emit()
    return blocks
