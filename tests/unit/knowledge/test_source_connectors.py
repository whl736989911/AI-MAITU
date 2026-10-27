"""Unit tests for the folder connectors.

SMB runs against an injected transport. A unit test cannot reach a share, and
what the connector is actually responsible for — recursion, relative paths, and
where the credential is allowed to appear — is more visible through a fake than
through a live server. The fake mirrors the two ``smbclient`` details that
matter: ``DirEntry`` exposes both ``name`` and a *full* ``path``, and every call
takes the credentials as keyword arguments.
"""

from __future__ import annotations

import ctypes
import errno
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
    UPLOAD_TEMP_PREFIX,
    LocalFolderConnector,
    SmbConnector,
    SourceDependencyMissing,
    SourceError,
    SourceUnsupported,
    build_connector,
)
from octop.infra.knowledge.sources import local as local_module
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
# local staged uploads
# ---------------------------------------------------------------------------


def test_local_stage_upload_publishes_on_clean_exit(tmp_path: Path, octop_home: Path) -> None:
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with connector.stage_upload("reports/q2.txt", b"second") as entry:
        # Staged, not final: the real name appears only when the block exits.
        assert not (root / "reports" / "q2.txt").exists()
        staged = list((root / "reports").glob(f"{UPLOAD_TEMP_PREFIX}*"))
        assert [path.read_bytes() for path in staged] == [b"second"]
        assert entry.path == "reports/q2.txt"
        assert entry.size == len("second")
        assert entry.is_dir is False

    published = root / "reports" / "q2.txt"
    assert published.read_bytes() == b"second"
    # The staged file's mtime survives publication, so change detection sees
    # the timestamp the file itself carries, not the moment of publishing.
    assert int(published.stat().st_mtime) == entry.modified_at
    assert not list((root / "reports").glob(f"{UPLOAD_TEMP_PREFIX}*"))


def test_local_stage_upload_error_keeps_the_tree_unchanged(
    tmp_path: Path, octop_home: Path
) -> None:
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with (
        pytest.raises(RuntimeError, match="indexer died"),
        connector.stage_upload("reports/q2.txt", b"second"),
    ):
        raise RuntimeError("indexer died")

    assert not (root / "reports" / "q2.txt").exists()
    assert not list(root.rglob(f"{UPLOAD_TEMP_PREFIX}*"))
    assert {entry.path for entry in connector.walk()} == {
        "notes.md",
        "empty",
        "reports",
        "reports/q1.txt",
    }


def test_local_stage_upload_refuses_an_existing_file(tmp_path: Path, octop_home: Path) -> None:
    """An upload never replaces: the conflict must reach the caller mapped."""
    root = _tree(tmp_path)
    (root / "notes.md").write_text("hello")
    connector = LocalFolderConnector(str(root))

    with pytest.raises(FileExistsError), connector.stage_upload("notes.md", b"replacement"):
        pass

    assert (root / "notes.md").read_text() == "hello"
    assert not list(root.rglob(f"{UPLOAD_TEMP_PREFIX}*"))


def test_local_stage_upload_loses_to_a_file_created_while_staging(
    tmp_path: Path, octop_home: Path
) -> None:
    """The no-overwrite decision happens at publication, not at staging time."""
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with pytest.raises(FileExistsError), connector.stage_upload("reports/q2.txt", b"second"):
        (root / "reports" / "q2.txt").write_text("arrived mid-flight")  # the race

    assert (root / "reports" / "q2.txt").read_text() == "arrived mid-flight"
    assert not list(root.rglob(f"{UPLOAD_TEMP_PREFIX}*"))


def test_local_stage_upload_needs_an_existing_parent(tmp_path: Path, octop_home: Path) -> None:
    """Staging never creates folders: uploads land where scans already look."""
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with (
        pytest.raises(SourceError, match="does not exist"),
        connector.stage_upload("missing/q2.txt", b"second"),
    ):
        pass

    assert not (root / "missing").exists()


@pytest.mark.parametrize(
    "attempt",
    [
        "../outside.txt",
        "reports/../../outside.txt",
        "/etc/octop-upload.txt",
        "C:/Windows/octop-upload.txt",
        "notes.md:hidden",
    ],
)
def test_local_stage_upload_refuses_to_write_outside_the_root(
    tmp_path: Path, octop_home: Path, attempt: str
) -> None:
    """An upload is a write: traversal, absolute, drive and ADS forms refuse."""
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with pytest.raises(SourceError), connector.stage_upload(attempt, b"second"):
        pass

    assert not list(root.rglob(f"{UPLOAD_TEMP_PREFIX}*"))


def test_local_stage_upload_refuses_the_reserved_prefix(tmp_path: Path, octop_home: Path) -> None:
    """A final name under the staging prefix could never be scanned again."""
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with (
        pytest.raises(SourceError, match="reserved"),
        connector.stage_upload(f"{UPLOAD_TEMP_PREFIX}fake.txt", b"x"),
    ):
        pass

    assert not list(root.rglob(f"{UPLOAD_TEMP_PREFIX}*"))


@posix_only
def test_local_stage_upload_refuses_a_symlink_that_points_out_of_the_root(
    tmp_path: Path, octop_home: Path
) -> None:
    """A final name that is a link out of the tree is an escape, not a target."""
    root = _tree(tmp_path)
    (root / "escape.txt").symlink_to(tmp_path / "outside.txt")
    connector = LocalFolderConnector(str(root))

    with (
        pytest.raises(SourceError, match="escapes the source root"),
        connector.stage_upload("escape.txt", b"second"),
    ):
        pass


@posix_only
def test_local_stage_upload_refuses_a_folder_symlink_out_of_the_root(
    tmp_path: Path, octop_home: Path
) -> None:
    """Containment follows the real path, not the path as spelled."""
    root = _tree(tmp_path)
    (root / "reports").rename(root / "real_reports")
    (root / "reports").symlink_to(tmp_path / "elsewhere")
    connector = LocalFolderConnector(str(root))

    with (
        pytest.raises(SourceError, match="escapes the source root"),
        connector.stage_upload("reports/q2.txt", b"second"),
    ):
        pass


def test_local_walk_omits_staging_files(tmp_path: Path, octop_home: Path) -> None:
    """A crash mid-upload must not leave half a document in the index."""
    root = _tree(tmp_path)
    (root / f"{UPLOAD_TEMP_PREFIX}one").write_bytes(b"partial")
    (root / "reports" / f"{UPLOAD_TEMP_PREFIX}two").write_bytes(b"partial")
    connector = LocalFolderConnector(str(root))

    entries = {entry.path: entry for entry in connector.walk()}

    assert set(entries) == {"notes.md", "empty", "reports", "reports/q1.txt"}


def test_local_stage_upload_publishes_without_hardlink_support(
    tmp_path: Path, octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mounted NAS without links still gets an atomic no-replace publish."""
    if os.name != "posix":
        pytest.skip("the no-replace rename fallback is a POSIX path")
    if not hasattr(ctypes.CDLL(None, use_errno=True), "renameat2"):
        pytest.skip("renameat2 is not available on this libc")

    def refuse(_src: Path, _dst: Path) -> None:
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr(os, "link", refuse)
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with connector.stage_upload("reports/q2.txt", b"second"):
        pass

    assert (root / "reports" / "q2.txt").read_bytes() == b"second"
    assert not list(root.rglob(f"{UPLOAD_TEMP_PREFIX}*"))


def test_local_stage_upload_fails_closed_without_a_safe_primitive(
    tmp_path: Path, octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without links and without renameat2, publishing refuses — never replaces."""
    if os.name != "posix":
        pytest.skip("the no-replace rename fallback is a POSIX path")

    def refuse(_src: Path, _dst: Path) -> None:
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    def absent(_staged: Path, _final: Path) -> None:
        raise OSError(errno.ENOSYS, "renameat2 is not available on this platform")

    monkeypatch.setattr(os, "link", refuse)
    monkeypatch.setattr(local_module, "_rename_noreplace", absent)
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with (
        pytest.raises(SourceError, match="no safe"),
        connector.stage_upload("reports/q2.txt", b"second"),
    ):
        pass

    assert not (root / "reports" / "q2.txt").exists()
    assert not list(root.rglob(f"{UPLOAD_TEMP_PREFIX}*"))


def test_local_stage_upload_publishes_by_rename_when_links_are_unsupported(
    tmp_path: Path, octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows fallback: rename itself refuses to replace an existing file."""
    if os.name != "nt":
        pytest.skip("the plain-rename fallback is a Windows path")

    def refuse(_src: Path, _dst: Path) -> None:
        raise OSError(errno.EPERM, "Operation not supported")

    monkeypatch.setattr(os, "link", refuse)
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))

    with connector.stage_upload("reports/q2.txt", b"second"):
        pass

    assert (root / "reports" / "q2.txt").read_bytes() == b"second"
    assert not list((root / "reports").glob(f"{UPLOAD_TEMP_PREFIX}*"))


def test_local_stage_upload_survives_a_failed_temp_cleanup(
    tmp_path: Path, octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A published file must not be rolled back because its staging link stuck."""
    root = _tree(tmp_path)
    connector = LocalFolderConnector(str(root))
    real_unlink = Path.unlink

    def stubborn_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name.startswith(UPLOAD_TEMP_PREFIX):
            raise PermissionError(errno.EACCES, "locked by a scanner", str(self))
        real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", stubborn_unlink)

    with connector.stage_upload("reports/q2.txt", b"second") as entry:
        pass

    assert (root / "reports" / "q2.txt").read_bytes() == b"second"
    assert entry.size == len("second")


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
    """The slice of ``smbclient`` the connector uses, recording every call.

    The write side mirrors the two behaviours publication depends on:
    ``rename`` is sent with ``ReplaceIfExists`` false and fails on an existing
    destination, and a staged file only exists once its ``open_file("wb")``
    context has been closed.
    """

    def __init__(
        self,
        tree: dict[str, list[_FakeEntry]],
        files: dict[str, bytes],
        reparse_points: set[str] | None = None,
    ) -> None:
        self._tree = tree
        self._files = files
        self._dirs = set(tree)
        self._reparse_points = reparse_points or set()
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def scandir(self, path: str, **kwargs: str) -> Iterator[_FakeEntry]:
        self.calls.append(("scandir", path, dict(kwargs)))
        if path not in self._tree:
            raise OSError(f"STATUS_OBJECT_NAME_NOT_FOUND listing {path}")
        return iter(self._tree[path])

    def stat(self, path: str, **kwargs: str) -> SimpleNamespace:
        self.calls.append(("stat", path, dict(kwargs)))
        return self._lookup(path)

    def lstat(self, path: str, **kwargs: str) -> SimpleNamespace:
        self.calls.append(("lstat", path, dict(kwargs)))
        if path in self._reparse_points:
            # What ``smbclient`` reports for a mount point or symlink that is
            # stat'd without following it.
            return SimpleNamespace(
                st_size=0,
                st_mtime=1_700_000_000,
                st_file_attributes=0x400,
                st_reparse_tag=0xA000_0003,
            )
        return self._lookup(path)

    def _lookup(self, path: str) -> SimpleNamespace:
        if path in self._files:
            return SimpleNamespace(
                st_size=len(self._files[path]),
                st_mtime=1_700_000_000,
                st_file_attributes=0,
            )
        if path in self._dirs:
            return SimpleNamespace(st_size=0, st_mtime=1_700_000_000, st_file_attributes=0x10)
        raise OSError(f"STATUS_OBJECT_NAME_NOT_FOUND {path}")

    @contextmanager
    def open_file(self, path: str, mode: str = "rb", **kwargs: str) -> Iterator[io.BytesIO]:
        self.calls.append(("open_file", path, dict(kwargs)))
        if mode == "wb":
            if path in self._dirs or path in self._reparse_points:
                raise OSError(f"STATUS_ACCESS_DENIED {path}")
            buffer = io.BytesIO()
            yield buffer
            self._files[path] = buffer.getvalue()
            return
        if path not in self._files:
            raise OSError(f"STATUS_OBJECT_NAME_NOT_FOUND {path}")
        yield io.BytesIO(self._files[path])

    def rename(self, src: str, dst: str, **kwargs: str) -> None:
        self.calls.append(("rename", dst, dict(kwargs)))
        if src not in self._files:
            raise OSError(f"STATUS_OBJECT_NAME_NOT_FOUND {src}")
        if dst in self._files or dst in self._dirs or dst in self._reparse_points:
            raise OSError(f"STATUS_OBJECT_NAME_COLLISION {dst}")
        self._files[dst] = self._files.pop(src)

    def unlink(self, path: str, **kwargs: str) -> None:
        self.calls.append(("unlink", path, dict(kwargs)))
        if path not in self._files:
            raise OSError(f"STATUS_OBJECT_NAME_NOT_FOUND {path}")
        del self._files[path]


def _fake_client(reparse_points: set[str] | None = None) -> _FakeSmbClient:
    return _FakeSmbClient(
        tree={
            _unc(): [_FakeEntry("notes.md", is_dir=False), _FakeEntry("reports", is_dir=True)],
            _unc("reports"): [_FakeEntry("q1.txt", is_dir=False)],
        },
        files={_unc("notes.md"): b"hello", _unc("reports", "q1.txt"): b"quarter"},
        reparse_points=reparse_points,
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
# smb staged uploads
# ---------------------------------------------------------------------------


def test_smb_stage_upload_publishes_on_clean_exit(fake_smb: _FakeSmbClient) -> None:
    connector = _smb_connector()

    with connector.stage_upload("reports/q2.txt", b"second") as entry:
        # Staged, not final: the real name appears only when the block exits.
        assert _unc("reports", "q2.txt") not in fake_smb._files
        staged = [path for path in fake_smb._files if UPLOAD_TEMP_PREFIX in path]
        assert len(staged) == 1
        assert entry.path == "reports/q2.txt"
        assert entry.size == len("second")
        assert entry.modified_at == 1_700_000_000

    assert fake_smb._files[_unc("reports", "q2.txt")] == b"second"
    assert not any(UPLOAD_TEMP_PREFIX in path for path in fake_smb._files)


def test_smb_stage_upload_error_keeps_the_share_unchanged(fake_smb: _FakeSmbClient) -> None:
    connector = _smb_connector()
    before = dict(fake_smb._files)

    with (
        pytest.raises(RuntimeError, match="indexer died"),
        connector.stage_upload("reports/q2.txt", b"second"),
    ):
        raise RuntimeError("indexer died")

    assert fake_smb._files == before


def test_smb_stage_upload_refuses_an_existing_file(fake_smb: _FakeSmbClient) -> None:
    """The rename is sent no-replace: the conflict surfaces as FileExistsError."""
    connector = _smb_connector()

    with pytest.raises(FileExistsError), connector.stage_upload("notes.md", b"replacement"):
        pass

    assert fake_smb._files[_unc("notes.md")] == b"hello"
    assert not any(UPLOAD_TEMP_PREFIX in path for path in fake_smb._files)


def test_smb_stage_upload_loses_to_a_file_created_while_staging(
    fake_smb: _FakeSmbClient,
) -> None:
    """A target created mid-flight wins the collision at publication time."""
    connector = _smb_connector()

    with pytest.raises(FileExistsError), connector.stage_upload("reports/q2.txt", b"second"):
        fake_smb._files[_unc("reports", "q2.txt")] = b"arrived mid-flight"  # the race

    assert fake_smb._files[_unc("reports", "q2.txt")] == b"arrived mid-flight"
    assert not any(UPLOAD_TEMP_PREFIX in path for path in fake_smb._files)


def test_smb_stage_upload_needs_an_existing_parent(fake_smb: _FakeSmbClient) -> None:
    with pytest.raises(SourceError), _smb_connector().stage_upload("missing/q2.txt", b"second"):
        pass

    assert not any(UPLOAD_TEMP_PREFIX in path for path in fake_smb._files)


def test_smb_stage_upload_stays_inside_the_configured_root(fake_smb: _FakeSmbClient) -> None:
    """Upload paths carry the configured root, and only those are accepted."""
    connector = _smb_connector(root_path="reports")

    with connector.stage_upload("reports/q2.txt", b"second"):
        pass

    assert fake_smb._files[_unc("reports", "q2.txt")] == b"second"

    for attempt in ["notes.md", "other/x.txt"]:
        with (
            pytest.raises(SourceError, match="escapes the configured source root"),
            connector.stage_upload(attempt, b"second"),
        ):
            pass
    # Nothing was staged or written outside the configured root.
    assert fake_smb._files[_unc("notes.md")] == b"hello"
    assert not any(UPLOAD_TEMP_PREFIX in path for path in fake_smb._files)


@pytest.mark.parametrize(
    "attempt",
    [
        "/abs/q2.txt",
        "//fileserver/company/q2.txt",
        "../q2.txt",
        "reports/../../q2.txt",
        "C:/q2.txt",
        "reports/q2.txt:hidden",
        "reports/.octop-upload-fake.txt",
    ],
)
def test_smb_stage_upload_refuses_paths_that_leave_the_share(
    fake_smb: _FakeSmbClient, attempt: str
) -> None:
    """An upload is a write: absolute, traversal, drive and ADS forms refuse."""
    connector = _smb_connector(root_path="reports")

    with pytest.raises(SourceError), connector.stage_upload(attempt, b"second"):
        pass

    assert not any(UPLOAD_TEMP_PREFIX in path for path in fake_smb._files)


def test_smb_stage_upload_refuses_to_traverse_a_reparse_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mount point or symlink mid-path would redirect the write elsewhere."""
    client = _fake_client(reparse_points={_unc("elsewhere")})
    client._tree[_unc()] = [_FakeEntry("elsewhere", is_dir=True)]
    client._dirs.add(_unc("elsewhere"))
    client._files[_unc("elsewhere", "q2.txt")] = b"outside-original"
    monkeypatch.setattr(smb_module, "_smbclient", lambda: client)

    with (
        pytest.raises(SourceError, match="reparse point"),
        SmbConnector(server=_SERVER, share=_SHARE).stage_upload("elsewhere/q2.txt", b"new"),
    ):
        pass

    assert client._files[_unc("elsewhere", "q2.txt")] == b"outside-original"
    assert not any(UPLOAD_TEMP_PREFIX in path for path in client._files)


def test_smb_stage_upload_treats_the_configured_root_as_the_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured root itself is trusted: containment starts below it."""
    client = _fake_client(reparse_points={_unc("reports")})
    monkeypatch.setattr(smb_module, "_smbclient", lambda: client)

    with _smb_connector(root_path="reports").stage_upload("reports/q2.txt", b"second"):
        pass

    assert client._files[_unc("reports", "q2.txt")] == b"second"


def test_smb_walk_omits_staging_files(fake_smb: _FakeSmbClient) -> None:
    """A crash mid-upload must not leave half a document in the index."""
    fake_smb._tree[_unc()].append(_FakeEntry(f"{UPLOAD_TEMP_PREFIX}789", is_dir=False))
    fake_smb._files[_unc(UPLOAD_TEMP_PREFIX + "789")] = b"partial"

    entries = {entry.path for entry in _smb_connector().walk()}

    assert entries == {"notes.md", "reports", "reports/q1.txt"}


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
