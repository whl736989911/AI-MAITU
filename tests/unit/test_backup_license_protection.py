from __future__ import annotations

import json
from pathlib import Path

from octop.infra.backup.system_archive import (
    _filtered_config_file,
    _filtered_env_file,
    _restore_env_preserving_license,
    _should_skip_path,
)


def test_system_backup_filter_excludes_license_identity_tree() -> None:
    assert _should_skip_path(Path("license/state.json"), skip_chats=False)
    assert _should_skip_path(Path("agents/agent-a/license/state.json"), skip_chats=False)
    assert not _should_skip_path(Path("agents/agent-a/profile.json"), skip_chats=False)


def test_environment_backup_omits_license_trust_and_restore_keeps_local_values(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current.env"
    archive = tmp_path / "archive.env"
    current.write_text(
        "OCTOP_LICENSE_SERVER_URL=https://trusted.example\n"
        "OCTOP_LICENSE_TRUST_FILE=/etc/octop/trust.json\n"
        "OCTOP_LICENSE_CA_FILE=/etc/octop/ca.pem\n"
        "OCTOP_API_KEY=current\n",
        encoding="utf-8",
    )

    _filtered_env_file(current, archive)
    archived_text = archive.read_text(encoding="utf-8")
    assert "OCTOP_API_KEY=current" in archived_text
    assert "OCTOP_LICENSE_" not in archived_text

    archive.write_text(
        "OCTOP_API_KEY=from-backup\nOCTOP_LICENSE_SERVER_URL=https://attacker.example\n",
        encoding="utf-8",
    )
    _restore_env_preserving_license(archive, current)
    restored_text = current.read_text(encoding="utf-8")
    assert "OCTOP_API_KEY=from-backup" in restored_text
    assert "https://trusted.example" in restored_text
    assert "https://attacker.example" not in restored_text


def test_config_backup_omits_legacy_license_trust_options(tmp_path: Path) -> None:
    source = tmp_path / "config.json"
    archive = tmp_path / "archive.json"
    source.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "license_server_url": "https://license.example",
                "license": {"trust_file": "/tmp/untrusted.json"},
            }
        ),
        encoding="utf-8",
    )

    _filtered_config_file(source, archive)
    filtered = json.loads(archive.read_text(encoding="utf-8"))
    assert filtered == {"host": "127.0.0.1"}
