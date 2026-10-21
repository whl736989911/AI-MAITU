"""Docker entrypoint first-run idempotency tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="requires POSIX shell executable semantics",
)
def test_external_database_restart_does_not_repeat_init_or_rewrite_credentials(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    entrypoint = repo / "docker" / "docker-entrypoint.sh"
    home = tmp_path / "home"
    octop_home = home / ".octop"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    init_calls = tmp_path / "init-calls"
    python = bin_dir / "python"
    python.write_text("#!/bin/sh\nprintf 'Test Brand\\n'\n", encoding="utf-8")
    python.chmod(0o755)
    octop = bin_dir / "octop"
    octop.write_text(
        '#!/bin/sh\nif [ "$1" = init ]; then printf \'init\\n\' >> "$INIT_CALLS"; fi\n',
        encoding="utf-8",
    )
    octop.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "OCTOP_HOME": str(octop_home),
            "OCTOP_DEFAULT_PASSWORD": "StrongPassword123",
            "OCTOP_DATABASE_DRIVER": "postgresql",
            "INIT_CALLS": str(init_calls),
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
        }
    )
    subprocess.run(["bash", str(entrypoint), "true"], env=env, check=True, capture_output=True)
    credential_file = octop_home / "credential.txt"
    original_credentials = credential_file.read_bytes()
    assert b"StrongPassword123" in original_credentials
    assert (octop_home / ".entrypoint-init-complete").is_file()
    assert init_calls.read_text(encoding="utf-8").splitlines() == ["init"]

    subprocess.run(["bash", str(entrypoint), "true"], env=env, check=True, capture_output=True)
    assert init_calls.read_text(encoding="utf-8").splitlines() == ["init"]
    assert credential_file.read_bytes() == original_credentials
