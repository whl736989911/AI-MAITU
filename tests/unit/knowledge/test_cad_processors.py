"""Regression coverage for CAD text extraction and source-relative locators."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from octop.infra.knowledge import cad_preview
from octop.infra.knowledge.processors.libredwg import _parsed_json


def test_cad_preview_does_not_reuse_previous_content_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    script = tmp_path / "dwg_preview.py"
    script.write_text("# fake converter", encoding="utf-8")
    monkeypatch.setenv("OCTOP_DWG_PREVIEW_SCRIPT", str(script))
    first_drawing = tmp_path / "first.dwg"
    second_drawing = tmp_path / "second.dwg"
    first_drawing.write_bytes(b"revision one")
    second_drawing.write_bytes(b"revision two")

    def run_converter(args, **_kwargs):
        original, output_dir = Path(args[2]), Path(args[3])
        output_name = "a-old.png" if original == first_drawing else "z-new.png"
        (output_dir / output_name).write_bytes(
            b"old preview" if original == first_drawing else b"new preview"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cad_preview.subprocess, "run", run_converter)
    first = cad_preview.render_preview(
        kb_id="kb",
        doc_id="drawing",
        original=first_drawing,
        content_hash="1" * 64,
        filename="drawing.dwg",
    )
    second = cad_preview.render_preview(
        kb_id="kb",
        doc_id="drawing",
        original=second_drawing,
        content_hash="2" * 64,
        filename="drawing.dwg",
    )

    assert first.read_bytes() == b"old preview"
    assert second.read_bytes() == b"new preview"


def test_libredwg_json_extracts_text_and_resolves_real_handle_references() -> None:
    parsed = _parsed_json(
        "plan.dwg",
        {
            "OBJECTS": [
                {
                    "entity": "TEXT",
                    "handle": [0, 2, 628],
                    "layer": [5, 1, 16, 16],
                    "entmode": 2,
                    "ins_pt": [974.3, 2831.8],
                    "text_value": "示例标注",
                },
                {
                    "entity": "MTEXT",
                    "handle": [0, 2, 4923],
                    "ownerhandle": [4, 2, 1163, 1163],
                    "layer": [5, 1, 16, 16],
                    "entmode": 0,
                    "ins_pt": [4273.1, 2972.7, 0.0],
                    "text": "{\\Ftxt|c0;\\H1.875x;\\C7;%%C\\fSimSun|b1|i0|c134|p2;705%%P5\\P(\\Ftxt|c0;%%C\\fSimSun|b1|i0|c134|p2;673)}",
                },
                {
                    "object": "LAYOUT",
                    "handle": [0, 1, 34],
                    "ownerhandle": [12, 1, 8, 26],
                    "layout_name": "Model",
                    "block_header": [4, 1, 31, 31],
                },
                {"object": "BLOCK_HEADER", "handle": [4, 2, 1163], "name": "FlangeBlock"},
                {"object": "BLOCK_HEADER", "handle": [4, 1, 31], "name": "*Model_Space"},
                {"object": "LAYER", "handle": [5, 1, 16], "name": "NOTES"},
            ]
        },
        "engineering/plan.dwg",
    )

    assert parsed.text == "示例标注\nØ705±5\n(Ø673)"
    assert parsed.filename == "plan.dwg"
    assert parsed.source_path == "engineering/plan.dwg"
    assert parsed.sections == ("Model",)
    assert parsed.blocks[0].locator == {
        "kind": "cad_entity",
        "entity": "TEXT",
        "ordinal": 0,
        "coordinate_space": "model_space",
        "handle": "628",
        "layer": "NOTES",
        "layout": "Model",
        "position": {"x": 974.3, "y": 2831.8},
    }
    assert parsed.blocks[1].locator == {
        "kind": "cad_entity",
        "entity": "MTEXT",
        "ordinal": 1,
        "coordinate_space": "block_local",
        "handle": "4923",
        "layer": "NOTES",
        "block": "FlangeBlock",
        "position": {"x": 4273.1, "y": 2972.7, "z": 0.0},
    }


def test_dxf_parser_keeps_layer_handle_and_coordinates(tmp_path: Path) -> None:
    from octop.infra.knowledge.processors.libredwg import parse_dxf

    path = tmp_path / "plan.dxf"
    path.write_text(
        "0\nSECTION\n2\nENTITIES\n0\nTEXT\n5\nF1\n8\nNOTES\n10\n12.5\n20\n34\n1\nPump room\n0\nENDSEC\n0\nEOF\n",
        encoding="utf-8",
    )
    parsed = parse_dxf(path, source_path="plans/plan.dxf")

    assert parsed.text == "Pump room"
    assert parsed.source_path == "plans/plan.dxf"
    assert parsed.blocks[0].locator == {
        "kind": "cad_entity",
        "entity": "TEXT",
        "handle": "F1",
        "layer": "NOTES",
        "position": {"x": "12.5", "y": "34"},
    }


def test_dxf_mtext_joins_leading_chunks_before_final_chunk(tmp_path: Path) -> None:
    from octop.infra.knowledge.processors.libredwg import parse_dxf

    path = tmp_path / "notes.dxf"
    path.write_text(
        "0\nSECTION\n2\nENTITIES\n0\nMTEXT\n8\nNOTES\n"
        "3\n说明：\n3\n段落一\\P\n1\n段落二\\U+00B1公差\n0\nENDSEC\n0\nEOF\n",
        encoding="utf-8",
    )
    parsed = parse_dxf(path, source_path="notes.dxf")

    assert parsed.text == "说明：段落一\n段落二±公差"
    assert parsed.blocks[0].locator["layer"] == "NOTES"
