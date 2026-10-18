from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout
from urllib3.exceptions import ReadTimeoutError

from octop.infra.backend.isolated_execute import (
    IsolatedFilesystemBackend,
    _resolve_image_and_workspace,
)


def _mapped_workspace(tmp_path: Path) -> tuple[Path, Path, Path, MagicMock]:
    container_root = tmp_path / "platform" / "data"
    managed_root = container_root / "agents"
    workspace = managed_root / "agent-a"
    workspace.mkdir(parents=True)
    host_volume = tmp_path / "docker-volume"
    mapped = host_volume / "agents" / "agent-a"
    mapped.mkdir(parents=True)
    client = MagicMock()
    client.api.inspect_container.return_value = {
        "Image": "sha256:platform-image",
        "Mounts": [
            {
                "Type": "volume",
                "Destination": str(container_root),
                "Source": str(host_volume),
            }
        ],
    }
    return workspace, managed_root, mapped, client


def test_resolves_only_managed_agent_subdirectory(tmp_path: Path) -> None:
    workspace, managed_root, mapped, client = _mapped_workspace(tmp_path)

    _image, host_workspace = _resolve_image_and_workspace(client, workspace, managed_root)

    assert host_workspace == mapped.resolve()


def test_refuses_foreign_workspace_even_if_mounted(tmp_path: Path) -> None:
    _workspace, managed_root, _mapped, client = _mapped_workspace(tmp_path)
    foreign = tmp_path / "platform" / "data" / "license"
    foreign.mkdir()

    with pytest.raises(RuntimeError):
        _resolve_image_and_workspace(client, foreign, managed_root)


@pytest.mark.parametrize(
    "timeout_error",
    [
        TimeoutError(),
        ReadTimeout(),
        RequestsConnectionError(ReadTimeoutError(None, "/containers/owned/wait", "timed out")),
    ],
    ids=["builtin-timeout", "requests-timeout", "wrapped-stream-timeout"],
)
def test_timeout_kills_and_removes_disposable_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout_error: Exception
) -> None:
    workspace, managed_root, _mapped, client = _mapped_workspace(tmp_path)
    container = MagicMock()
    container.logs.return_value = iter(())
    container.wait.side_effect = timeout_error
    client.containers.run.return_value = container
    monkeypatch.setitem(
        sys.modules,
        "docker",
        SimpleNamespace(
            from_env=lambda **_kwargs: client,
            types=SimpleNamespace(LogConfig=lambda **kwargs: kwargs),
        ),
    )

    from deepagents.backends.filesystem import FilesystemBackend

    backend = IsolatedFilesystemBackend(
        filesystem=FilesystemBackend(root_dir=workspace),
        managed_root=managed_root,
        locale="en",
        feature_agent=False,
        readonly_dirs=[],
    )
    response = backend.execute("sleep 10", timeout=1)

    assert response.exit_code == 124
    container.kill.assert_called_once()
    container.remove.assert_called_once_with(force=True, v=True)


@pytest.mark.asyncio
async def test_cancellation_kills_and_removes_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, managed_root, _mapped, client = _mapped_workspace(tmp_path)
    container = MagicMock()
    killed = threading.Event()
    removed = threading.Event()

    def logs(**_kwargs):
        while not killed.wait(0.01):
            yield b""

    container.logs.side_effect = logs
    container.kill.side_effect = killed.set
    container.remove.side_effect = lambda **_kwargs: removed.set()
    client.containers.run.return_value = container
    monkeypatch.setitem(
        sys.modules,
        "docker",
        SimpleNamespace(
            from_env=lambda **_kwargs: client,
            types=SimpleNamespace(LogConfig=lambda **kwargs: kwargs),
        ),
    )

    from deepagents.backends.filesystem import FilesystemBackend

    backend = IsolatedFilesystemBackend(
        filesystem=FilesystemBackend(root_dir=workspace),
        managed_root=managed_root,
        locale="en",
        feature_agent=False,
        readonly_dirs=[],
    )
    task = asyncio.create_task(backend.aexecute("sleep 100", timeout=120))
    for _ in range(200):
        if client.containers.run.called:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(200):
        if removed.is_set():
            break
        await asyncio.sleep(0.01)

    assert killed.is_set()
    assert removed.is_set()
