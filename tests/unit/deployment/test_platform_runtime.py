"""Deployment Compose generation keeps protected data mounts without exposing daemon access."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.deployment import platform
from scripts.deployment.common import DeploymentError
from scripts.deployment.platform import (
    _build_platform_override,
    _local_docker_socket,
    _verify_runtime_mounts,
)


@pytest.mark.skipif(os.name != "posix", reason="Deployment Docker sockets are Unix sockets")
def test_platform_rejects_remote_docker_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker.example:2375")

    with pytest.raises(DeploymentError):
        _local_docker_socket()


@pytest.mark.skipif(os.name != "posix", reason="Deployment Docker sockets are Unix sockets")
def test_platform_rejects_missing_local_docker_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_HOST", f"unix://{tmp_path / 'missing.sock'}")

    with pytest.raises(DeploymentError):
        _local_docker_socket()


@pytest.mark.skipif(os.name != "posix", reason="Deployment Docker sockets are Unix sockets")
def test_platform_accepts_present_local_docker_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "docker.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        monkeypatch.setenv("DOCKER_HOST", f"unix://{socket_path}")

        assert _local_docker_socket() == socket_path
    finally:
        server.close()


def test_adopted_override_cannot_replace_original_data_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        platform,
        "compose",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps(
                {
                    "services": {
                        "maitu": {
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(tmp_path / "replacement-data"),
                                    "target": "/data/.octop",
                                }
                            ]
                        }
                    }
                }
            )
        ),
    )

    with pytest.raises(DeploymentError):
        _verify_runtime_mounts(
            {},
            {"type": "bind", "source": str(tmp_path / "original-data")},
            {},
        )


def test_platform_override_does_not_mount_host_docker_socket(tmp_path: Path) -> None:
    override = tmp_path / "platform.compose.override.yml"
    _build_platform_override(
        override,
        host="127.0.0.1",
        published_port=8088,
        target_port=8088,
        server_url="https://center.example",
        license_keys_path=tmp_path / "trust" / "license-trusted-keys.json",
        release_keys_path=tmp_path / "trust" / "release-trusted-keys.json",
        license_state_dir=tmp_path / "license-state",
        update_queue_dir=tmp_path / "update-queue",
        ca_path=None,
    )

    assert "/var/run/docker.sock" not in override.read_text(encoding="utf-8")
