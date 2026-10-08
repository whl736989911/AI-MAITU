"""Regression tests for deepagents-backends 0.2 result/listing compatibility."""

from __future__ import annotations

from typing import Any

from deepagents.backends.protocol import BackendProtocol, LsResult, ReadResult

from octop.infra.backend.compat import adapt_backend_protocol, is_adapted_backend


class _LegacyRemote(BackendProtocol):
    def __init__(self) -> None:
        self.close_count = 0

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        return "legacy formatted text"

    async def als_info(self, path: str) -> list[dict[str, Any]]:
        assert path == "/docs"
        return [{"path": "/docs/readme.md", "is_dir": False}]

    async def _get_file_data(self, path: str) -> dict[str, Any] | None:
        assert path == "/docs/readme.md"
        return {"content": ["first", "second"]}

    async def close(self) -> None:
        self.close_count += 1


async def test_legacy_listing_and_reads_expose_current_protocol_results() -> None:
    backend = adapt_backend_protocol(_LegacyRemote())
    assert is_adapted_backend(backend)
    try:
        listing = await backend.als("/docs")
        assert isinstance(listing, LsResult)
        assert listing.entries == [{"path": "/docs/readme.md", "is_dir": False}]

        result = await backend.aread("/docs/readme.md", offset=1, limit=1)
        assert isinstance(result, ReadResult)
        assert result.file_data == {"content": "second", "encoding": "utf-8"}
    finally:
        await backend.aclose()
        backend.close()
    assert backend._inner.close_count == 1


def test_current_protocol_backend_is_not_wrapped() -> None:
    class _CurrentRemote(BackendProtocol):
        def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
            return ReadResult(file_data={"content": "ok", "encoding": "utf-8"})

        def ls(self, path: str) -> LsResult:
            return LsResult(entries=[])

    backend = _CurrentRemote()
    assert adapt_backend_protocol(backend) is backend
