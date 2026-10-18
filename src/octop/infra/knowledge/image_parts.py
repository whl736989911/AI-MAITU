"""Bounded, lossless image tiles in display-oriented source pixel coordinates."""

from __future__ import annotations

import base64
import io
import warnings
from pathlib import Path
from typing import IO, Any

from PIL import Image, ImageOps

_TILE_EDGE = 1024
_TILE_OVERLAP = 64
MAX_IMAGE_PARTS = 257


def _starts(length: int) -> list[int]:
    if length <= _TILE_EDGE:
        return [0]
    stride = _TILE_EDGE - _TILE_OVERLAP
    steps = (length - _TILE_EDGE + stride - 1) // stride
    if steps + 2 > MAX_IMAGE_PARTS:
        raise ValueError("source image requires too many tiles for safe processing")
    return [index * (length - _TILE_EDGE) // steps for index in range(steps + 1)]


def _dimensions(image: Image.Image) -> tuple[int, int]:
    width, height = image.size
    return (height, width) if image.getexif().get(274, 1) in {5, 6, 7, 8} else (width, height)


def image_part_count(source: Path | IO[bytes], byte_size: int, max_bytes: int) -> int:
    if byte_size <= max_bytes:
        return 1
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source) as image:
            width, height = _dimensions(image)
            return 1 + len(_starts(width)) * len(_starts(height))


def _encoded(image: Image.Image, locator: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    converted = image.convert("RGBA" if has_alpha else "RGB")
    try:
        converted.info.clear()
        with io.BytesIO() as output:
            converted.save(output, format="PNG")
            data = output.getvalue()
        if len(data) > max_bytes:
            raise ValueError("image tile exceeds transfer limit")
        return {
            "mime_type": "image/png",
            "data_base64": base64.b64encode(data).decode("ascii"),
            "locator": locator,
        }
    finally:
        converted.close()


def read_image_parts(
    source: Path | IO[bytes],
    *,
    byte_size: int,
    max_bytes: int,
    mime_type: str,
    locator: dict[str, Any],
    start: int,
    stop: int,
) -> tuple[list[dict[str, Any]], int]:
    if byte_size <= max_bytes:
        if start != 1:
            raise ValueError("source image position is outside the document")
        if isinstance(source, Path):
            data = source.read_bytes()
        else:
            source.seek(0)
            data = source.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("source image changed while reading")
        return [
            {
                "mime_type": mime_type,
                "data_base64": base64.b64encode(data).decode("ascii"),
                "locator": locator,
            }
        ], 1
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source) as original:
            width, height = _dimensions(original)
            xs, ys = _starts(width), _starts(height)
            total = 1 + len(xs) * len(ys)
            if start > total:
                raise ValueError("source image position is outside the document")
            oriented = (
                ImageOps.exif_transpose(original)
                if original.getexif().get(274, 1) != 1
                else original
            )
            try:
                images = []
                for position in range(start, min(stop, total) + 1):
                    if position == 1:
                        box = (0, 0, width, height)
                        part = ImageOps.contain(
                            oriented,
                            (min(width, _TILE_EDGE), min(height, _TILE_EDGE)),
                            Image.Resampling.LANCZOS,
                        )
                        role = "overview"
                    else:
                        row, column = divmod(position - 2, len(xs))
                        left, top = xs[column], ys[row]
                        box = (
                            left,
                            top,
                            min(left + _TILE_EDGE, width),
                            min(top + _TILE_EDGE, height),
                        )
                        part = oriented.crop(box)
                        role = "tile"
                    try:
                        images.append(
                            _encoded(
                                part,
                                {
                                    **locator,
                                    "image_part": role,
                                    "box": list(box),
                                    "image_width": width,
                                    "image_height": height,
                                    "coordinate_space": "display_pixels",
                                },
                                max_bytes,
                            )
                        )
                    finally:
                        part.close()
                return images, total
            finally:
                if oriented is not original:
                    oriented.close()
