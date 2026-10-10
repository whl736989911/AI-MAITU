from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PureWindowsPath

DATA_DIR = Path(os.environ.get("LICENSE_CENTER_DATA", "./data")).expanduser().resolve()
DB_PATH = (
    Path(os.environ.get("LICENSE_CENTER_DB", str(DATA_DIR / "center.sqlite3")))
    .expanduser()
    .resolve()
)
MIGRATIONS = Path(__file__).with_name("migrations")


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _restrict_permissions(path: Path, mode: int) -> None:
    with suppress(OSError):
        path.chmod(mode)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _restrict_permissions(DB_PATH.parent, 0o700)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    _restrict_permissions(DB_PATH, 0o600)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(DB_PATH) + suffix)
        if sidecar.exists():
            _restrict_permissions(sidecar, 0o600)
    return conn


def migrate() -> None:
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
        )
        for path in sorted(MIGRATIONS.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
            ).fetchone():
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?,?)",
                (version, int(time.time())),
            )
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute("SELECT kid,private_path FROM signing_keys").fetchall()
            for row in rows:
                kid = row["kid"]
                expected = f"{kid}.ed25519.fernet"
                private_path = row["private_path"]
                windows_path = PureWindowsPath(private_path)
                path = Path(private_path)
                if ".." in path.parts or ".." in windows_path.parts:
                    raise ValueError(f"Invalid private key path metadata for signing key {kid}")
                if path.is_absolute() or windows_path.is_absolute():
                    basename = windows_path.name if windows_path.is_absolute() else path.name
                    if basename != expected:
                        raise ValueError(f"Invalid private key path metadata for signing key {kid}")
                    conn.execute(
                        "UPDATE signing_keys SET private_path=? WHERE kid=?", (expected, kid)
                    )
                elif private_path != expected or Path(private_path).name != private_path:
                    raise ValueError(f"Invalid private key path metadata for signing key {kid}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


@contextmanager
def transaction(immediate: bool = True) -> Iterator[sqlite3.Connection]:
    conn = connect()
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now() -> int:
    return int(time.time())


def audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    kind: str,
    object_id: str,
    before: str,
    after: str,
    reason: str = "",
) -> None:
    conn.execute(
        "INSERT INTO audit_log(actor,action,object_type,object_id,before_json,after_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (actor, action, kind, object_id, before, after, reason, now()),
    )
