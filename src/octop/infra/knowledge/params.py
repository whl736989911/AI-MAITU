"""Instance-wide knowledge indexing parameters."""

from __future__ import annotations

from collections.abc import Callable

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120

_CHUNK_SIZE_KEY = "knowledge_chunk_size"
_CHUNK_OVERLAP_KEY = "knowledge_chunk_overlap"

_MIN_CHUNK_SIZE = 100
_MAX_CHUNK_SIZE = 8000

SettingsGet = Callable[[str], str | None]
SettingsSet = Callable[[str, str], None]


def _as_int(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    """Parse a stored integer setting; invalid or out-of-range values use *default*."""
    if value is None or not str(value).strip():
        return default
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def _validate_chunk_window(size: int, overlap: int) -> None:
    if size < _MIN_CHUNK_SIZE or size > _MAX_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be between {_MIN_CHUNK_SIZE} and {_MAX_CHUNK_SIZE}")
    if overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")


def get_advanced_settings(settings_get: SettingsGet) -> dict[str, int]:
    """Return resolved chunking parameters (missing values use defaults)."""
    size = _as_int(
        settings_get(_CHUNK_SIZE_KEY),
        default=DEFAULT_CHUNK_SIZE,
        minimum=_MIN_CHUNK_SIZE,
        maximum=_MAX_CHUNK_SIZE,
    )
    overlap = _as_int(
        settings_get(_CHUNK_OVERLAP_KEY),
        default=DEFAULT_CHUNK_OVERLAP,
        minimum=0,
        maximum=max(size - 1, 0),
    )
    if overlap >= size:
        overlap = min(DEFAULT_CHUNK_OVERLAP, size - 1) if size > 1 else 0
    return {
        "chunk_size": size,
        "chunk_overlap": overlap,
    }


def set_advanced_settings(
    settings_get: SettingsGet,
    settings_set: SettingsSet,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> None:
    """Persist provided advanced settings; omitted fields stay unchanged."""
    current = get_advanced_settings(settings_get)
    next_size = current["chunk_size"] if chunk_size is None else int(chunk_size)
    next_overlap = current["chunk_overlap"] if chunk_overlap is None else int(chunk_overlap)
    if chunk_size is not None or chunk_overlap is not None:
        _validate_chunk_window(next_size, next_overlap)
        if chunk_size is not None:
            settings_set(_CHUNK_SIZE_KEY, str(next_size))
        if chunk_overlap is not None:
            settings_set(_CHUNK_OVERLAP_KEY, str(next_overlap))
