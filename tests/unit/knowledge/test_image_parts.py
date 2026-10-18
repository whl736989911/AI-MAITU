from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from octop.infra.knowledge.image_parts import image_part_count, read_image_parts


@pytest.mark.parametrize("alpha", [False, True])
def test_image_tiles_preserve_orientation_colors_and_transparency(tmp_path: Path, alpha: bool):
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255)]
    if alpha:
        colors = [
            (*color, opacity)
            for color, opacity in zip(colors, [0, 64, 128, 192, 255, 32], strict=True)
        ]
    image = Image.new("RGBA" if alpha else "RGB", (3, 2))
    image.putdata(colors)
    metadata = PngInfo()
    metadata.add_text("padding", "x" * 2048)
    exif = image.getexif()
    exif[274] = 6
    path = tmp_path / "rotated.png"
    image.save(path, format="PNG", pnginfo=metadata, exif=exif)
    image.close()
    original = path.read_bytes()
    assert len(original) > 256
    assert image_part_count(path, len(original), 256) == 2
    parts, total = read_image_parts(
        path,
        byte_size=len(original),
        max_bytes=256,
        mime_type="image/png",
        locator={"kind": "image"},
        start=1,
        stop=4,
    )
    assert total == 2
    for part in parts:
        binary = base64.b64decode(part["data_base64"])
        assert len(binary) <= 256
        assert part["locator"]["box"] == [0, 0, 2, 3]
        with Image.open(io.BytesIO(binary)) as decoded:
            assert decoded.size == (2, 3)
            assert decoded.tobytes() == bytes(
                channel for index in [3, 0, 4, 1, 5, 2] for channel in colors[index]
            )
    assert path.read_bytes() == original


def test_extreme_image_shape_is_refused_instead_of_unbounded_tiling(tmp_path: Path):
    path = tmp_path / "extreme.png"
    image = Image.new("RGB", (600_000, 1))
    image.save(path, format="PNG")
    image.close()
    with pytest.raises(ValueError):
        image_part_count(path, path.stat().st_size, 256)
