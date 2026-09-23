"""tests/unit/test_host_paths.py"""

from __future__ import annotations

import pytest

from octop.infra.gateway.media.backend_files import is_blocked_host_download_path


@pytest.mark.parametrize(
    ("path", "blocked"),
    [
        ("/Users/me/.harness-browser/screenshots/x.png", True),
        ("/tmp/secret.png", True),
        ("/home/user/x.png", True),
        (r"C:\Users\me\screenshot.png", True),
        (r"c:\users\me\file.png", True),
        (r"C:\Windows\Temp\shot.png", True),
        (r"C:\Program Files\app\data.bin", True),
        (r"%LOCALAPPDATA%\Temp\shot.png", False),  # literal percent — not expanded
        ("/outbound/chart.png", False),
        ("outbound/chart.png", False),
        ("/logo.png", False),
        ("tmp/cache.bin", False),
        ("var/data.json", False),
        ("home/readme.md", False),
    ],
)
def test_is_blocked_host_download_path(path: str, blocked: bool) -> None:
    assert is_blocked_host_download_path(path) is blocked


def test_is_blocked_appdata_temp_segment() -> None:
    assert is_blocked_host_download_path(r"C:\Users\me\AppData\Local\Temp\shot.png") is True
