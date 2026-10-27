"""Optional external document processor adapters."""

from typing import Literal

ArchiveReason = Literal[
    "no_processor",
    "cad_unavailable",
    "ocr_disabled",
    "legacy_office_unavailable",
]

ARCHIVE_NO_PROCESSOR = "no_processor"
ARCHIVE_CAD_UNAVAILABLE = "cad_unavailable"
ARCHIVE_OCR_DISABLED = "ocr_disabled"
ARCHIVE_LEGACY_OFFICE_UNAVAILABLE = "legacy_office_unavailable"
ARCHIVE_REASONS = frozenset(
    {
        ARCHIVE_NO_PROCESSOR,
        ARCHIVE_CAD_UNAVAILABLE,
        ARCHIVE_OCR_DISABLED,
        ARCHIVE_LEGACY_OFFICE_UNAVAILABLE,
    }
)
