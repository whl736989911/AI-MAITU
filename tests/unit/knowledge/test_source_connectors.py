"""Unit tests for the folder connectors.

SMB runs against an injected transport. A unit test cannot reach a share, and
what the connector is actually responsible for — recursion, relative paths, and
where the credential is allowed to appear — is more visible through a fake than
through a live server. The fake mirrors the two ``smbclient`` details that
matter: ``DirEntry`` exposes both ``name`` and a *full* ``path``, and every call
takes the credentials as keyword arguments.
"""

from __future__ import annotations

import io
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.knowledge import sources as sources_module
from octop.infra.knowledge.sources import (
    KIND_LOCAL,
    KIND_NFS,
    KIND_SMB,
    LocalFolderConnector,
    SmbConnector,
    SourceDependencyMissing,
    SourceError,
    SourceUnsupported,
    build_connector,
)
from octop.infra.knowledge.sources import smb as smb_module

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX-only path guards")

_SERVER = "fileserver"
_SHARE = "company"
_PASSWORD = "s3cret-share-password"


def _unc(*parts: str) -> str:
    """A UNC path the way ``smbclient`` expects one: two leading backslashes."""
    return "\\".join(("", "", _SERVER, _SHARE, *parts))


# ---------------------------------------------------------------------------
# local
# ---------------------------------------------------------------------------


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "share"
    (root / "reports").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "notes.md").write_text("hello", encoding="utf-8")
    (root / "reports" / "q1.txt").write_text("quarter", encoding="utf-8")
    return root


@pytest.fixture
def octop_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "octop"
    monkeypatch.setenv("OCTOP_HOME", str(home))
    return home


def test_local_walk_lists_files_and_folders_relative_to_the_root(
    tmp_path: Path, octop_home: Path
) -> None:
    root = _tree(tmp_path)

    entries = {entry.path: entry for entry in LocalFolderConnector(str(root)).walk()}

    assert set(entries) == {"notes.md", "empty", "reports", "reports/q1.txt"}
    assert entries["notes.md"].is_dir is False
    assert entries["notes.md"].size == len("hello")
    assert entries["notes.md"].modified_at is not None
    assert entries["reports"].is_dir is True
    assert entries["reports"].size == 0
    assert entries["reports/q1.txt"].size == len("quarter")


def test_local_read_returns_the_file_bytes(tmp_path: Path, octop_home: Path) -> None:
    connector = LocalFolderConnector(str(_tree(tmp_path)))

    assert connector.read_bytes("reports/q1.txt") == b"quarter"


@pytest.mark.parametrize(
    "attempt",
    ["../secret.txt", "reports/../../secret.txt", "/etc/passwd", "reports/../../secret.txt\0"],
)
def test_local_read_refuses_to_leave_the_root(
    tmp_path: Path, octop_home: Path, attempt: str
) -> None:
    """A source path is relative and confined: ``..`` must not read the server."""
    root = _tree(tmp_path)
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")

    with pytest.raises(SourceError):
        LocalFolderConnector(str(root)).read_bytes(attempt)


@posix_only
def test_local_read_refuses_a_symlink_that_points_out_of_the_root(
    tmp_path: Path, octop_home: Path
) -> None:
    """Containment is resolved, not textual: a link out of the tree is out."""
    root = _tree(tmp_path)
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    (root / "escape.txt").symlink_to(tmp_path / "secret.txt")

    with pytest.raises(SourceError, match="escapes"):
        LocalFolderConnector(str(root)).read_bytes("escape.txt")


def test_local_test_lists_the_root_without_walking_it(tmp_path: Path, octop_home: Path) -> None:
    detail = LocalFolderConnector(str(_tree(tmp_path))).test()

    assert "3 item(s)" in detail


def test_local_test_reports_a_root_that_is_not_there(tmp_path: Path, octop_home: Path) -> None:
    connector = LocalFolderConnector(str(tmp_path / "absent"))

    with pytest.raises(SourceError, match="does not exist"):
        connector.test()


def test_local_root_cannot_be_the_platform_data_directory(tmp_path: Path, octop_home: Path) -> None:
    """Indexing OCTOP_HOME would put the database and the Fernet key in the index."""
    (octop_home / "nested").mkdir(parents=True)

    with pytest.raises(SourceError, match="platform's own data directory"):
        LocalFolderConnector(str(octop_home))
    with pytest.raises(SourceError, match="platform's own data directory"):
        LocalFolderConnector(str(octop_home / "nested"))


def test_local_root_cannot_contain_the_platform_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "data" / "octop"
    home.mkdir(parents=True)
    monkeypatch.setenv("OCTOP_HOME", str(home))

    with pytest.raises(SourceError, match="cannot contain"):
        LocalFolderConnector(str(tmp_path / "data"))


@posix_only
def test_local_root_outside_the_denylist_is_refused(tmp_path: Path, octop_home: Path) -> None:
    """The same host denylist the workspace picker uses applies here."""
    with pytest.raises(SourceError):
        LocalFolderConnector("/etc")


def test_local_root_is_required(tmp_path: Path, octop_home: Path) -> None:
    with pytest.raises(SourceError, match="needs a root path"):
        LocalFolderConnector("   ")


# ---------------------------------------------------------------------------
# smb
# ---------------------------------------------------------------------------


class _FakeEntry:
    """What ``smbclient`` hands back: a ``name`` and a *full* ``path``."""

    def __init__(self, name: str, *, is_dir: bool) -> None:
        self.name = name
        self.path = _unc(name)
        self._is_dir = is_dir

    def is_dir(self) -> bool:
        return self._is_dir


class _FakeSmbClient:
    """The slice of ``smbclient`` the connector uses, recording every call."""

    def __init__(self, tree: dict[str, list[_FakeEntry]], files: dict[str, bytes]) -> None:
        self._tree = tree
        self._files = files
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def scandir(self, path: str, **kwargs: str) -> Iterator[_FakeEntry]:
        self.calls.append(("scandir", path, dict(kwargs)))
        if path not in self._tree:
            raise OSError(f"STATUS_OBJECT_NAME_NOT_FOUND listing {path}")
        return iter(self._tree[path])

    def stat(self, path: str, **kwargs: str) -> SimpleNamespace:
        self.calls.append(("stat", path, dict(kwargs)))
        if path not in self._files:
            raise OSError(f"STATUS_OBJECT_NAME_NOT_FOUND {path}")
        return SimpleNamespace(st_size=len(self._files[path]), st_mtime=1_700_000_000)

    @contextmanager
    def open_file(self, path: str, mode: str = "rb", **kwargs: str) -> Iterator[io.BytesIO]:
        self.calls.append(("open_file", path, dict(kwargs)))
        if path not in self._files:
            raise OSError(f"STATUS_OBJECT_NAME_NOT_FOUND {path}")
        yield io.BytesIO(self._files[path])


def _fake_client() -> _FakeSmbClient:
    return _FakeSmbClient(
        tree={
            _unc(): [_FakeEntry("notes.md", is_dir=False), _FakeEntry("reports", is_dir=True)],
            _unc("reports"): [_FakeEntry("q1.txt", is_dir=False)],
        },
        files={_unc("notes.md"): b"hello", _unc("reports", "q1.txt"): b"quarter"},
    )


@pytest.fixture
def fake_smb(monkeypatch: pytest.MonkeyPatch) -> _FakeSmbClient:
    client = _fake_client()
    monkeypatch.setattr(smb_module, "_smbclient", lambda: client)
    return client


def _smb_connector(*, root_path: str = "") -> SmbConnector:
    return SmbConnector(
        server=_SERVER,
        share=_SHARE,
        root_path=root_path,
        username="DOMAIN\\svc-octop",
        password=_PASSWORD,
    )


def test_smb_walk_recurses_and_returns_paths_relative_to_the_share(
    fake_smb: _FakeSmbClient,
) -> None:
    """Paths must not carry the server or share: they are stored per file."""
    entries = {entry.path: entry for entry in _smb_connector().walk()}

    assert set(entries) == {"notes.md", "reports", "reports/q1.txt"}
    assert entries["notes.md"].size == len("hello")
    assert entries["notes.md"].modified_at == 1_700_000_000
    assert entries["reports"].is_dir is True
    assert entries["reports/q1.txt"].is_dir is False


def test_smb_walk_respects_a_root_path(fake_smb: _FakeSmbClient) -> None:
    connector = _smb_connector(root_path="reports")
    entries = connector.walk()

    # Scanning starts at the root and stays inside it: one listing of the root,
    # and every path below it relative to the share.
    assert [call[1] for call in fake_smb.calls if call[0] == "scandir"] == [_unc("reports")]
    assert {entry.path for entry in entries} == {"reports/q1.txt"}


def test_smb_read_returns_the_file_bytes(fake_smb: _FakeSmbClient) -> None:
    assert _smb_connector().read_bytes("reports/q1.txt") == b"quarter"


def test_smb_credentials_travel_as_arguments_and_never_in_the_path(
    fake_smb: _FakeSmbClient,
) -> None:
    """The ``smb://user:password@host`` form would put the secret where errors quote it."""
    connector = _smb_connector()
    connector.walk()
    connector.read_bytes("notes.md")
    connector.test()

    assert fake_smb.calls, "the fake should have been called"
    for _method, path, kwargs in fake_smb.calls:
        assert _PASSWORD not in path
        assert "DOMAIN\\svc-octop" not in path
        assert kwargs.get("password") == _PASSWORD
        assert kwargs.get("username") == "DOMAIN\\svc-octop"


def test_smb_omits_credentials_it_does_not_have(fake_smb: _FakeSmbClient) -> None:
    """A guest share is configured with no account, and no empty kwargs either."""
    SmbConnector(server=_SERVER, share=_SHARE).test()

    for _method, _path, kwargs in fake_smb.calls:
        assert kwargs == {}


def test_smb_failure_message_carries_no_secret(fake_smb: _FakeSmbClient) -> None:
    """An unreachable share must be explainable in a log line (design §4)."""
    with pytest.raises(SourceError) as raised:
        _smb_connector().read_bytes("missing.txt")

    message = str(raised.value)
    assert _PASSWORD not in message
    assert "missing.txt" in message


def test_smb_test_counts_the_root(fake_smb: _FakeSmbClient) -> None:
    assert "2 item(s)" in _smb_connector().test()


def test_smb_without_the_dependency_says_what_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "smbclient", None)

    with pytest.raises(SourceDependencyMissing, match="pip install smbprotocol"):
        _smb_connector().test()


@pytest.mark.parametrize(("server", "share"), [("", "share"), ("server", ""), ("ser/ver", "share")])
def test_smb_requires_a_usable_server_and_share(server: str, share: str) -> None:
    with pytest.raises(SourceError):
        SmbConnector(server=server, share=share)


def test_smb_refuses_a_path_that_leaves_the_share(fake_smb: _FakeSmbClient) -> None:
    with pytest.raises(SourceError):
        _smb_connector().read_bytes("../../etc/passwd")


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_build_connector_routes_each_kind(tmp_path: Path, octop_home: Path) -> None:
    local = build_connector(kind=KIND_LOCAL, root_path=str(_tree(tmp_path)))
    assert isinstance(local, LocalFolderConnector)

    smb = build_connector(kind=KIND_SMB, server=_SERVER, share=_SHARE)
    assert isinstance(smb, SmbConnector)


def test_build_connector_refuses_nfs_with_a_reason() -> None:
    """No fake NFS connector: the message is the whole feature this build has."""
    with pytest.raises(SourceUnsupported, match="libnfs"):
        build_connector(kind=KIND_NFS, server="nas", share="export")


def test_build_connector_refuses_an_unknown_kind() -> None:
    with pytest.raises(SourceUnsupported, match="unknown source kind"):
        build_connector(kind="ftp")


def test_only_folder_kinds_resolve(tmp_path: Path, octop_home: Path) -> None:
    assert sources_module.is_folder_kind(KIND_LOCAL) is True
    assert sources_module.is_folder_kind(KIND_SMB) is True
    assert sources_module.is_folder_kind("upload") is False
    assert sources_module.is_folder_kind("") is False
