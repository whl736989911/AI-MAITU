"""Current-agent, namespace-scoped memory archives.

Archives contain one harness-memory namespace and only checkpoint threads
owned by the authenticated caller. They never contain a database image.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

_FORMAT = 1
_MAX_ARCHIVE = 512 * 1024 * 1024
_MAX_MEMBER = 256 * 1024 * 1024
_SAFE_ID = re.compile(r"^[0-9a-f]{32}\.zip$")


def backup_root(state_root: Path, agent_id: str, scope: str, user_id: int | None) -> Path:
    if (
        scope not in {"shared", "private"}
        or not isinstance(user_id, int)
        or user_id <= 0
        or not agent_id
        or Path(agent_id).name != agent_id
        or agent_id in {".", ".."}
    ):
        raise ValueError("memory archives require a valid scope, agent, and authenticated user")
    root = state_root.resolve()
    for part in ("memory-backups", agent_id, scope, f"user-{user_id}"):
        root = root / part
        if root.is_symlink():
            raise ValueError("memory archive path must not contain symlinks")
        root.mkdir(exist_ok=True)
    return root


def _fallback_saver(path: Path, *, create: bool) -> tuple[Any | None, sqlite3.Connection | None]:
    if path.is_symlink():
        raise ValueError("fallback checkpoint path is not system-managed")
    exists = path.is_file()
    if not exists and not create:
        return None, None
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        tables = {
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if {"checkpoints", "writes"} <= tables:
            saver.is_setup = True
        elif exists and not create:
            raise ValueError("fallback checkpoint database has an unsupported schema")
        else:
            saver.setup()
        return saver, conn
    except Exception:
        conn.close()
        raise


def _serialize_thread_checkpoints(
    saver: Any, source: str, thread_ids: list[str]
) -> list[dict[str, Any]]:
    serde = getattr(saver, "serde", None)
    if serde is None:
        raise ValueError("checkpoint serializer is unavailable")
    rows: list[dict[str, Any]] = []
    for thread_id in sorted(set(thread_ids)):
        for item in saver.list({"configurable": {"thread_id": thread_id}}):
            kind, payload = serde.dumps_typed(item.checkpoint)
            metadata = json.dumps(
                item.metadata, ensure_ascii=False, separators=(",", ":"), default=str
            )
            pending = []
            for task_id, channel, value in item.pending_writes or []:
                value_kind, value_bytes = serde.dumps_typed(value)
                pending.append(
                    [
                        str(task_id),
                        str(channel),
                        value_kind,
                        base64.b64encode(value_bytes).decode("ascii"),
                    ]
                )
            rows.append(
                {
                    "source": source,
                    "thread_id": thread_id,
                    "config": item.config,
                    "parent_config": item.parent_config,
                    "checkpoint_type": kind,
                    "checkpoint": base64.b64encode(payload).decode("ascii"),
                    "metadata": metadata,
                    "pending_writes": pending,
                }
            )
    return rows


def _thread_checkpoint_rows(
    memory: Any, thread_ids: list[str], *, fallback_path: Path | None, memory_enabled: bool
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if memory_enabled:
        ensure = getattr(memory, "_ensure_checkpointer", None)
        if callable(ensure):
            ensure()
        saver = getattr(memory, "_checkpointer", None)
        if saver is not None:
            rows.extend(_serialize_thread_checkpoints(saver, "memory", thread_ids))
    if fallback_path is not None:
        saver, conn = _fallback_saver(fallback_path, create=False)
        try:
            if saver is not None:
                rows.extend(_serialize_thread_checkpoints(saver, "fallback", thread_ids))
        finally:
            if conn is not None:
                conn.close()
    return rows


def _write_one_saver(
    saver: Any, source: str, rows: list[dict[str, Any]], allowed_threads: set[str]
) -> None:
    serde = saver.serde
    by_thread: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["source"] == source:
            by_thread.setdefault(str(row["thread_id"]), []).append(row)
    for thread_id in sorted(allowed_threads):
        saver.delete_thread(thread_id)
        for row in reversed(by_thread.get(thread_id, [])):
            cp = serde.loads_typed(
                (str(row["checkpoint_type"]), base64.b64decode(row["checkpoint"], validate=True))
            )
            config = row.get("parent_config") or {
                "configurable": {"thread_id": thread_id, "checkpoint_ns": ""}
            }
            saved = saver.put(
                config, cp, json.loads(row["metadata"]), cp.get("channel_versions", {})
            )
            checkpoint_config = saved or row["config"]
            grouped: dict[str, list[tuple[str, Any]]] = {}
            for task, channel, kind, value in row.get("pending_writes") or []:
                grouped.setdefault(str(task), []).append(
                    (
                        str(channel),
                        serde.loads_typed((str(kind), base64.b64decode(value, validate=True))),
                    )
                )
            for task_id, writes in grouped.items():
                saver.put_writes(checkpoint_config, writes, task_id)


def _write_checkpoints(
    memory: Any,
    rows: list[dict[str, Any]],
    allowed_threads: set[str],
    *,
    fallback_path: Path | None,
    memory_enabled: bool,
) -> None:
    sources = {str(row["source"]) for row in rows}
    if sources - {"memory", "fallback"}:
        raise ValueError("invalid checkpoint source")
    if memory_enabled:
        ensure = getattr(memory, "_ensure_checkpointer", None)
        if callable(ensure):
            ensure()
        saver = getattr(memory, "_checkpointer", None)
        if saver is None and "memory" in sources:
            raise ValueError("target runtime has no memory checkpoint saver")
        if saver is not None:
            _write_one_saver(saver, "memory", rows, allowed_threads)
    if fallback_path is not None:
        saver, conn = _fallback_saver(fallback_path, create="fallback" in sources)
        try:
            if saver is None and "fallback" in sources:
                raise ValueError("fallback checkpoint storage is unavailable")
            if saver is not None:
                _write_one_saver(saver, "fallback", rows, allowed_threads)
        finally:
            if conn is not None:
                conn.close()
    elif "fallback" in sources:
        raise ValueError("archive requires an unavailable fallback checkpoint store")


def _validate_archive(
    path: Path,
    *,
    agent_id: str,
    scope: str,
    namespace: str,
    backend: str,
    user_id: int,
    allowed_threads: set[str],
) -> tuple[dict[str, Any], bytes, list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_ARCHIVE:
        raise ValueError("invalid or oversized memory archive")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        allowed_names = {
            "manifest.json",
            "namespace.jsonl",
            "checkpoints.json",
            "documents.json",
            "extended.json",
        }
        if len(infos) != len(allowed_names) or any(
            i.filename not in allowed_names
            or i.file_size > _MAX_MEMBER
            or ((i.external_attr >> 16) & 0o170000) == 0o120000
            for i in infos
        ):
            raise ValueError("archive members or sizes are not allowed")
        if sum(info.file_size for info in infos) > _MAX_ARCHIVE:
            raise ValueError("memory archive expands beyond the permitted size")
        if any(
            Path(i.filename).is_absolute() or ".." in Path(i.filename).parts or i.is_dir()
            for i in infos
        ):
            raise ValueError("unsafe archive path")
        if {i.filename for i in infos} != allowed_names:
            raise ValueError("incomplete memory archive")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            manifest.get("format") != _FORMAT
            or manifest.get("agent_id") != agent_id
            or manifest.get("scope") != scope
            or manifest.get("user_id") != user_id
            or manifest.get("namespace") != namespace
            or manifest.get("backend") != backend
        ):
            raise ValueError("memory archive identity or backend does not match this target")
        data = archive.read("namespace.jsonl")
        checkpoint_data = archive.read("checkpoints.json")
        document_data = archive.read("documents.json")
        extended_data = archive.read("extended.json")
        digests = manifest.get("sha256")
        if not isinstance(digests, dict) or any(
            hashlib.sha256(value).hexdigest() != digests.get(key)
            for key, value in (
                ("namespace", data),
                ("checkpoints", checkpoint_data),
                ("documents", document_data),
                ("extended", extended_data),
            )
        ):
            raise ValueError("memory archive integrity check failed")
        checkpoints = json.loads(checkpoint_data)
        documents = json.loads(document_data)
        extended = json.loads(extended_data)
        if not isinstance(checkpoints, list) or any(
            not isinstance(r, dict)
            or r.get("thread_id") not in allowed_threads
            or not _valid_checkpoint_row(r)
            for r in checkpoints
        ):
            raise ValueError("archive contains an invalid or unauthorized checkpoint")
        if (
            not isinstance(documents, dict)
            or set(documents) != {"USER.md", "MEMORY.md"}
            or any(not isinstance(v, str) for v in documents.values())
        ):
            raise ValueError("invalid manual memory documents")
        if (
            not isinstance(extended, dict)
            or any(
                not isinstance(extended.get(key), list) for key in ("nodes", "episodes", "digests")
            )
            or not isinstance(extended.get("meta"), dict)
        ):
            raise ValueError("invalid extended memory data")
        return manifest, data, checkpoints, documents, extended


def _valid_checkpoint_row(row: dict[str, Any]) -> bool:
    thread_id = row.get("thread_id")
    config = row.get("config")
    parent = row.get("parent_config")
    if (
        row.get("source") not in {"memory", "fallback"}
        or not isinstance(thread_id, str)
        or not thread_id
        or not isinstance(config, dict)
    ):
        return False
    for value in (config, parent):
        if value is None:
            continue
        configurable = value.get("configurable") if isinstance(value, dict) else None
        if not isinstance(configurable, dict) or configurable.get("thread_id") != thread_id:
            return False
        checkpoint_id = configurable.get("checkpoint_id")
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        if checkpoint_id is not None and (
            not isinstance(checkpoint_id, str) or len(checkpoint_id) > 512
        ):
            return False
        if not isinstance(checkpoint_ns, str) or len(checkpoint_ns) > 512:
            return False
    try:
        if not isinstance(row.get("checkpoint_type"), str) or not isinstance(
            row.get("checkpoint"), str
        ):
            return False
        base64.b64decode(row["checkpoint"], validate=True)
        if not isinstance(json.loads(row["metadata"]), dict):
            return False
        for item in row.get("pending_writes") or []:
            if (
                not isinstance(item, list)
                or len(item) != 4
                or not all(isinstance(part, str) for part in item)
            ):
                return False
            base64.b64decode(item[3], validate=True)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    return True


def _validate_checkpoint_payloads(
    memory: Any,
    rows: list[dict[str, Any]],
    *,
    fallback_path: Path | None,
    memory_enabled: bool,
) -> None:
    sources = {row.get("source") for row in rows}
    if "memory" in sources and not memory_enabled:
        raise ValueError("archive contains checkpoints but target memory checkpointing is disabled")
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    memory_serde = None
    if memory_enabled:
        ensure = getattr(memory, "_ensure_checkpointer", None)
        if callable(ensure):
            ensure()
        saver = getattr(memory, "_checkpointer", None)
        memory_serde = getattr(saver, "serde", None)
    fallback_serde = None
    fallback_conn = None
    if any(row.get("source") == "fallback" for row in rows):
        if fallback_path is not None and fallback_path.is_file():
            fallback_saver, fallback_conn = _fallback_saver(fallback_path, create=False)
            fallback_serde = fallback_saver.serde if fallback_saver is not None else None
        else:
            from langgraph.checkpoint.sqlite import SqliteSaver

            fallback_conn = sqlite3.connect(":memory:")
            fallback_saver = SqliteSaver(fallback_conn)
            fallback_saver.setup()
            fallback_serde = fallback_saver.serde
    try:
        for row in rows:
            serde = memory_serde if row["source"] == "memory" else fallback_serde
            serde = serde or JsonPlusSerializer()
            checkpoint = serde.loads_typed(
                (row["checkpoint_type"], base64.b64decode(row["checkpoint"], validate=True))
            )
            if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("id"), str):
                raise ValueError("invalid checkpoint payload")
            config = row["config"].get("configurable", {})
            if config.get("checkpoint_id") not in (None, checkpoint["id"]):
                raise ValueError("checkpoint identity does not match its configuration")
            for _, _, kind, value in row.get("pending_writes") or []:
                serde.loads_typed((kind, base64.b64decode(value, validate=True)))
    finally:
        if fallback_conn is not None:
            fallback_conn.close()


def _validate_extended(extended: dict[str, Any]) -> None:
    if any(
        not isinstance(item, dict)
        for key in ("nodes", "episodes", "digests")
        for item in extended[key]
    ):
        raise ValueError("invalid extended memory records")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in extended["meta"].items()):
        raise ValueError("invalid namespace metadata")


def _json_record(value: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(value)


def _namespace_meta(memory: Any) -> dict[str, str]:
    from harness_memory.storage.backends.postgres import PostgresMemoryBackend
    from harness_memory.storage.backends.sqlite import SqliteMemoryBackend

    backend = memory.backend
    if isinstance(backend, SqliteMemoryBackend):
        rows = backend._conn.execute(f"SELECT key, value FROM {backend._ns}_meta").fetchall()
        return {str(row[0]): str(row[1]) for row in rows}
    if isinstance(backend, PostgresMemoryBackend):
        prefix = f"{backend._ns}:"
        with backend._conn.cursor() as cur:
            cur.execute("SELECT key, value FROM meta WHERE key LIKE %s", (prefix + "%",))
            rows = cur.fetchall()
        return {
            str(row["key"])[len(prefix) :]: str(row["value"])
            for row in rows
            if str(row["key"]).startswith(prefix)
        }
    raise ValueError("unsupported memory backend")


def _extended_snapshot(memory: Any) -> dict[str, Any]:
    return {
        "nodes": [_json_record(row) for row in memory.get_tree()],
        "episodes": [_json_record(row) for row in memory.list_episodes(limit=10**9)],
        "digests": [_json_record(row) for row in memory.list_digests(limit=10**9)],
        "meta": _namespace_meta(memory),
    }


def _restore_extended(memory: Any, snapshot: dict[str, Any]) -> None:
    from datetime import datetime

    from harness_memory.types import DigestRecord, Episode, MemoryNode

    backend = memory.backend
    # Native atom imports rebuild their organizational leaves. Replace that
    # derived tree with the archived tree, including the original node ids.
    for node in backend.get_tree():
        if node.parent_id is None:
            backend.delete_node(node.id, cascade=True)
    _clear_namespace_meta(memory, set(snapshot["meta"]))
    for key, value in snapshot["meta"].items():
        if key not in {"schema_version", "fts_text_version"}:
            memory.set_meta(str(key), str(value))
    nodes = [dict(raw) for raw in snapshot["nodes"]]
    inserted: set[str] = set()
    while nodes:
        pending = []
        for item in nodes:
            if item.get("parent_id") is not None and item["parent_id"] not in inserted:
                pending.append(item)
                continue
            for key in ("created_at", "updated_at"):
                item[key] = datetime.fromisoformat(item[key])
            backend.save_node(MemoryNode(**item))
            inserted.add(str(item["id"]))
        if len(pending) == len(nodes):
            raise ValueError("memory tree contains missing or cyclic parent references")
        nodes = pending
    for raw in snapshot["episodes"]:
        item = dict(raw)
        for key in ("occurred_at", "created_at"):
            item[key] = datetime.fromisoformat(item[key])
        backend.save_episode(Episode(**item))
    for raw in snapshot["digests"]:
        item = dict(raw)
        for key in ("period_start", "period_end", "created_at", "updated_at"):
            item[key] = datetime.fromisoformat(item[key])
        memory.upsert_digest(DigestRecord(**item))


def _clear_namespace_meta(memory: Any, keep: set[str]) -> None:
    from harness_memory.storage.backends.postgres import PostgresMemoryBackend
    from harness_memory.storage.backends.sqlite import SqliteMemoryBackend

    backend = memory.backend
    keep = keep | {"schema_version", "fts_text_version"}
    if isinstance(backend, SqliteMemoryBackend):
        namespace = backend._ns
        names = sorted(keep)
        marks = ",".join("?" for _ in names)
        backend._conn.execute(f"DELETE FROM {namespace}_meta WHERE key NOT IN ({marks})", names)
        backend._commit()
    elif isinstance(backend, PostgresMemoryBackend):
        prefix = f"{backend._ns}:"
        with backend._cursor() as cur:
            cur.execute(
                "DELETE FROM meta WHERE left(key, length(%s)) = %s AND key <> ALL(%s)",
                (prefix, prefix, [prefix + key for key in keep]),
            )
        backend._commit()
    else:
        raise ValueError("unsupported memory backend")


def _filter_unowned_thread_rows(
    data: bytes, allowed_threads: set[str]
) -> tuple[bytes, dict[str, int]]:
    records = [json.loads(line) for line in data.splitlines() if line.strip()]
    if (
        len(records) < 2
        or records[0].get("table") != "__header__"
        or records[-1].get("table") != "__footer__"
    ):
        raise ValueError("invalid namespace export")
    footer = records[-1]
    counts = dict((footer.get("data") or {}).get("counts") or {})
    filtered = []
    for record in records[1:-1]:
        if record.get("table") == "thread_active_entities":
            thread_id = (record.get("data") or {}).get("thread_id")
            if thread_id not in allowed_threads:
                counts["thread_active_entities"] = max(
                    0, int(counts.get("thread_active_entities", 0)) - 1
                )
                continue
        filtered.append(record)
    footer["data"]["counts"] = counts
    return (
        (
            "\n".join(
                json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                for record in [records[0], *filtered, footer]
            )
            + "\n"
        ).encode(),
        counts,
    )


def create_backup(
    memory: Any,
    *,
    root: Path,
    agent_id: str,
    scope: str,
    user_id: int,
    thread_ids: list[str],
    documents: dict[str, str],
    fallback_path: Path | None,
    memory_enabled: bool,
) -> dict[str, Any]:
    from harness_memory.operations.migration.export import export_namespace

    root.mkdir(parents=True, exist_ok=True)
    backup_id = f"{uuid.uuid4().hex}.zip"
    fd, temp_name = tempfile.mkstemp(prefix=".memory-export-", suffix=".jsonl", dir=root)
    os.close(fd)
    temp = Path(temp_name)
    archive_tmp = root / f".{backup_id}.tmp"
    try:
        export_namespace(memory, temp)
        if temp.stat().st_size > _MAX_MEMBER:
            raise ValueError("memory namespace exceeds the archive size limit")
        namespace_data, counts = _filter_unowned_thread_rows(temp.read_bytes(), set(thread_ids))
        extended = _extended_snapshot(memory)
        extended_data = json.dumps(
            extended,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value),
        ).encode()
        checkpoint_rows = _thread_checkpoint_rows(
            memory, thread_ids, fallback_path=fallback_path, memory_enabled=memory_enabled
        )
        checkpoint_data = json.dumps(
            checkpoint_rows, ensure_ascii=False, separators=(",", ":")
        ).encode()
        document_data = json.dumps(documents, ensure_ascii=False, separators=(",", ":")).encode()
        if any(
            len(value) > _MAX_MEMBER
            for value in (namespace_data, extended_data, checkpoint_data, document_data)
        ):
            raise ValueError("memory backup member exceeds the archive size limit")
        manifest = {
            "format": _FORMAT,
            "agent_id": agent_id,
            "scope": scope,
            "user_id": user_id,
            "namespace": str(memory.namespace),
            "backend": type(memory.backend).__name__,
            "created_at": time.time(),
            "total_rows": sum(counts.values())
            + len(extended["nodes"])
            + len(extended["episodes"])
            + len(extended["digests"]),
            "sha256": {
                "namespace": hashlib.sha256(namespace_data).hexdigest(),
                "checkpoints": hashlib.sha256(checkpoint_data).hexdigest(),
                "documents": hashlib.sha256(document_data).hexdigest(),
                "extended": hashlib.sha256(extended_data).hexdigest(),
            },
        }
        with zipfile.ZipFile(archive_tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
            archive.writestr("namespace.jsonl", namespace_data)
            archive.writestr("checkpoints.json", checkpoint_data)
            archive.writestr("documents.json", document_data)
            archive.writestr("extended.json", extended_data)
        if archive_tmp.stat().st_size > _MAX_ARCHIVE:
            raise ValueError("memory backup exceeds the archive size limit")
        os.replace(archive_tmp, root / backup_id)
        return {
            "backup_id": backup_id,
            "created_at": manifest["created_at"],
            "size_bytes": (root / backup_id).stat().st_size,
            "backend": manifest["backend"],
            "total_rows": manifest["total_rows"],
        }
    finally:
        temp.unlink(missing_ok=True)
        archive_tmp.unlink(missing_ok=True)


def _verified_manifest(path: Path, *, agent_id: str, scope: str, user_id: int) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) != 5 or any(info.file_size > _MAX_MEMBER for info in infos):
            raise ValueError("invalid memory archive")
        manifest_info = archive.getinfo("manifest.json")
        if manifest_info.file_size > 64 * 1024:
            raise ValueError("invalid memory archive manifest")
        manifest = json.loads(archive.read(manifest_info))
    if (
        not isinstance(manifest, dict)
        or manifest.get("agent_id") != agent_id
        or manifest.get("scope") != scope
        or manifest.get("user_id") != user_id
    ):
        raise ValueError("memory archive identity does not match this caller")
    return manifest


def list_backups(root: Path, *, agent_id: str, scope: str, user_id: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not root.exists():
        return result
    for path in root.iterdir():
        if not _SAFE_ID.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            manifest = _verified_manifest(path, agent_id=agent_id, scope=scope, user_id=user_id)
            result.append(
                {
                    "backup_id": path.name,
                    "created_at": float(manifest["created_at"]),
                    "size_bytes": path.stat().st_size,
                    "backend": str(manifest["backend"]),
                    "total_rows": int(manifest.get("total_rows", 0)),
                }
            )
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
            continue
    return sorted(result, key=lambda row: row["created_at"], reverse=True)


def backup_path(root: Path, backup_id: str, *, agent_id: str, scope: str, user_id: int) -> Path:
    if not _SAFE_ID.fullmatch(backup_id):
        raise ValueError("backup not found")
    path = root / backup_id
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root.resolve():
        raise ValueError("backup not found")
    _verified_manifest(path, agent_id=agent_id, scope=scope, user_id=user_id)
    return path


def delete_backup(root: Path, backup_id: str, *, agent_id: str, scope: str, user_id: int) -> None:
    path = backup_path(root, backup_id, agent_id=agent_id, scope=scope, user_id=user_id)
    path.unlink()


def restore_backup(
    memory: Any,
    *,
    root: Path,
    backup_id: str,
    agent_id: str,
    scope: str,
    namespace: str,
    backend: str,
    user_id: int,
    allowed_threads: set[str],
    restore_documents: Any,
    fallback_path: Path | None,
    memory_enabled: bool,
) -> dict[str, Any]:
    from harness_memory.operations.migration.import_ import import_namespace

    if not _SAFE_ID.fullmatch(backup_id):
        raise ValueError("invalid backup id")
    path = root / backup_id
    if path.resolve().parent != root.resolve():
        raise ValueError("invalid backup location")
    manifest, namespace_data, checkpoints, documents, extended = _validate_archive(
        path,
        agent_id=agent_id,
        scope=scope,
        namespace=namespace,
        backend=backend,
        user_id=user_id,
        allowed_threads=allowed_threads,
    )
    _validate_checkpoint_payloads(
        memory, checkpoints, fallback_path=fallback_path, memory_enabled=memory_enabled
    )
    _validate_extended(extended)
    import json as _json

    from harness_memory.operations.migration import EXPORT_TABLES, EXPORT_VERSION

    lines = namespace_data.splitlines()
    if len(lines) < 2:
        raise ValueError("empty namespace archive")
    header = _json.loads(lines[0])
    footer = _json.loads(lines[-1])
    if (
        header.get("v") != EXPORT_VERSION
        or header.get("table") != "__header__"
        or header.get("data", {}).get("namespace") != namespace
    ):
        raise ValueError("invalid namespace export header")
    if (
        footer.get("v") != EXPORT_VERSION
        or footer.get("table") != "__footer__"
        or not isinstance(footer.get("data"), dict)
    ):
        raise ValueError("invalid namespace export footer")
    counts: dict[str, int] = {}
    for line in lines[1:-1]:
        record = _json.loads(line)
        table = record.get("table")
        if (
            record.get("v") != EXPORT_VERSION
            or table not in EXPORT_TABLES
            or not isinstance(record.get("data"), dict)
        ):
            raise ValueError("invalid namespace export record")
        counts[table] = counts.get(table, 0) + 1
    footer_counts = footer["data"].get("counts")
    if (
        not isinstance(footer_counts, dict)
        or any(int(footer_counts.get(table, 0)) != count for table, count in counts.items())
        or any(int(count) != counts.get(table, 0) for table, count in footer_counts.items())
    ):
        raise ValueError("namespace export row counts do not match its footer")
    extra_rows = len(extended["nodes"]) + len(extended["episodes"]) + len(extended["digests"])
    if sum(counts.values()) + extra_rows != int(manifest.get("total_rows", -1)):
        raise ValueError("memory archive row count does not match its manifest")
    temp_fd, temp_name = tempfile.mkstemp(prefix=".memory-import-", suffix=".jsonl", dir=root)
    os.close(temp_fd)
    temp = Path(temp_name)
    try:
        temp.write_bytes(namespace_data)
        backend_store = memory.backend
        # Bind SQLite's worker-local connection before entering its transaction.
        _ = backend_store._conn
        with backend_store.transaction():
            _purge_namespace(memory)
            summary = import_namespace(
                memory, temp, on_conflict="raise", expect_namespace=namespace
            )
            if summary.errors or sum(summary.applied.values()) != sum(counts.values()):
                raise ValueError("namespace import was incomplete")
            _restore_extended(memory, extended)
        _write_checkpoints(
            memory,
            checkpoints,
            allowed_threads,
            fallback_path=fallback_path,
            memory_enabled=memory_enabled,
        )
        restore_documents(documents)
        restored_rows = sum(summary.applied.values()) + extra_rows
        return {
            "restored_backup_id": backup_id,
            "restored_rows": restored_rows,
            "checkpoint_threads": len({r["thread_id"] for r in checkpoints}),
        }
    finally:
        temp.unlink(missing_ok=True)


def _purge_namespace(memory: Any) -> None:
    from harness_memory.storage.backends.postgres import PostgresMemoryBackend
    from harness_memory.storage.backends.sqlite import SqliteMemoryBackend

    backend = memory.backend
    if isinstance(backend, PostgresMemoryBackend):
        backend.purge_namespace()
        return
    if not isinstance(backend, SqliteMemoryBackend):
        raise ValueError("unsupported memory backend")
    namespace = backend._ns
    if not re.fullmatch(r"[a-z0-9_]+", namespace):
        raise ValueError("invalid SQLite namespace")
    tables = (
        "journal",
        "digests",
        "episodes",
        "thread_active_entities",
        "entity_pages",
        "aliases",
        "atoms",
        "candidates",
        "raw_events",
        "entities",
        "memory_nodes",
    )
    conn = backend._conn
    for table in tables:
        conn.execute(f"DELETE FROM {namespace}_{table}")
    backend._commit()
