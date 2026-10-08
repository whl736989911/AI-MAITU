"""Regression tests for Admin storage tree path and row boundaries."""

from __future__ import annotations

import pytest

from octop.infra.backend.tree_listing import dedupe_tree_rows, normalize_storage_path


def test_storage_path_normalization_rejects_parent_traversal() -> None:
    assert normalize_storage_path("\\docs\\./notes") == "/docs/notes"
    with pytest.raises(ValueError, match="invalid path"):
        normalize_storage_path("/docs/../secrets")


def test_tree_listing_drops_parent_echoes_and_deduplicates_directories() -> None:
    assert dedupe_tree_rows(
        [
            {"path": "/docs/", "is_dir": True},
            {"path": "/docs/readme.md", "is_dir": False},
            {"path": "docs/archive/", "is_dir": False},
            {"path": "docs/archive", "is_dir": True},
        ],
        parent="/docs",
    ) == [
        {"path": "/docs/readme.md", "is_dir": False},
        {"path": "docs/archive", "is_dir": True},
    ]
