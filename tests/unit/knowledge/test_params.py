"""Invalid chunk windows must not reach indexing."""

from __future__ import annotations

import pytest

from octop.infra.knowledge import params


def test_advanced_settings_reject_invalid_overlap() -> None:
    values: dict[str, str] = {}

    with pytest.raises(ValueError):
        params.set_advanced_settings(
            values.get, values.__setitem__, chunk_size=100, chunk_overlap=100
        )
