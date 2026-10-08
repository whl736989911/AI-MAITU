"""Adapt deepagents-backends 0.2 remote backends to harness-agent's protocol."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, cast

_REMOTE_SPEC_TYPES = frozenset({"s3", "postgres", "cos", "oss", "obs"})
_BACKEND_IO_TIMEOUT = 30.0


def is_adapted_backend(backend: Any) -> bool:
    return isinstance(backend, _LegacyProtocolBackend)


def is_remote_backend_spec(spec: Any) -> bool:
    return isinstance(spec, dict) and str(spec.get("type") or "").lower() in _REMOTE_SPEC_TYPES


def adapt_backend_protocol(backend: Any) -> Any:
    """Wrap legacy remotes whose ls or read surface predates current harness-agent."""
    if isinstance(backend, _LegacyProtocolBackend) or not _needs_adapt(backend):
        return backend
    wrapped = _LegacyProtocolBackend(backend)
    _patch_s3_list_v1(backend)
    return wrapped


def _read_returns_str(backend: Any) -> bool:
    read = getattr(type(backend), "read", None)
    if not callable(read):
        return False
    return getattr(read, "__annotations__", {}).get("return") in (str, "str")


def _needs_adapt(backend: Any) -> bool:
    try:
        from deepagents.backends.protocol import BackendProtocol
    except ImportError:
        return _read_returns_str(backend)
    if getattr(type(backend), "ls", None) is BackendProtocol.ls:
        return True
    return _read_returns_str(backend)


async def _s3_als_info_v1(backend: Any, path: str) -> list[Any]:
    prefix = path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    results: list[Any] = []
    async with backend._client() as client:
        paginator = client.get_paginator("list_objects")
        async for page in paginator.paginate(
            Bucket=backend._bucket,
            Prefix=backend._s3_key(prefix),
            Delimiter="/",
        ):
            for obj in page.get("Contents", []):
                results.append(
                    {
                        "path": backend._virtual_path(obj["Key"]),
                        "is_dir": False,
                        "size": obj.get("Size", 0),
                        "modified_at": obj["LastModified"].isoformat()
                        if "LastModified" in obj
                        else None,
                    }
                )
            results.extend(
                {"path": backend._virtual_path(item["Prefix"]), "is_dir": True}
                for item in page.get("CommonPrefixes", [])
            )
    results.sort(key=lambda entry: str(entry.get("path") or ""))
    return results


def _patch_s3_list_v1(backend: Any) -> None:
    if not callable(getattr(backend, "_s3_key", None)):
        return
    original = backend.als_info

    async def als_info(path: str) -> Any:
        if getattr(backend, "_octop_s3_list_v1", False):
            return await _s3_als_info_v1(backend, path)
        try:
            return await original(path)
        except Exception as exc:
            error = getattr(exc, "response", None)
            code = (error.get("Error") or {}).get("Code") if isinstance(error, dict) else None
            if code != "NotImplemented":
                raise
            backend._octop_s3_list_v1 = True
            return await _s3_als_info_v1(backend, path)

    backend.als_info = als_info


class _LegacyProtocolBackend:
    """Expose current structured results while keeping remote I/O on one loop."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._launcher_loop: asyncio.AbstractEventLoop | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._runner: asyncio.Future[None] | None = None
        self._runner_done = threading.Event()
        self._ready = threading.Event()
        self._state_lock = threading.Lock()
        self._closing = False
        self._closed = False

    def _worker_loop_locked(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="octop-backend-loop")
        self._launcher_loop = asyncio.new_event_loop()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            try:
                loop.run_forever()
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
                self._runner_done.set()

        self._runner = self._launcher_loop.run_in_executor(self._executor, run)
        if not self._ready.wait(5) or self._loop is None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
            self._runner = None
            self._launcher_loop.close()
            self._launcher_loop = None
            raise RuntimeError("backend worker loop failed to start")
        return self._loop

    def _run(self, value: Any) -> Any:
        if not inspect.isawaitable(value):
            return value
        with self._state_lock:
            if self._closing or self._closed:
                if inspect.iscoroutine(value):
                    value.close()
                raise RuntimeError("backend is closed")
            loop = self._worker_loop_locked()
            future: Future[Any] = asyncio.run_coroutine_threadsafe(
                cast(Coroutine[Any, Any, Any], value), loop
            )
        try:
            return future.result(timeout=_BACKEND_IO_TIMEOUT)
        except TimeoutError:
            future.cancel()
            raise TimeoutError(f"backend I/O timed out after {_BACKEND_IO_TIMEOUT:.0f}s") from None

    def _call(self, sync_name: str, async_name: str, *args: Any, **kwargs: Any) -> Any:
        async_fn = getattr(self._inner, async_name, None)
        if callable(async_fn):
            return self._run(async_fn(*args, **kwargs))
        sync_fn = getattr(self._inner, sync_name, None)
        if not callable(sync_fn):
            raise AttributeError(sync_name)
        return self._run(sync_fn(*args, **kwargs))

    def ls_info(self, path: str) -> Any:
        return self._call("ls_info", "als_info", path)

    async def als_info(self, path: str) -> Any:
        return await asyncio.to_thread(self.ls_info, path)

    def ls(self, path: str) -> Any:
        from deepagents.backends.protocol import LsResult

        return LsResult(entries=list(self.ls_info(path) or []))

    async def als(self, path: str) -> Any:
        from deepagents.backends.protocol import LsResult

        return LsResult(entries=list(await self.als_info(path) or []))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        from deepagents.backends.protocol import ReadResult

        get_data = getattr(self._inner, "_get_file_data", None)
        if callable(get_data):
            data = self._run(get_data(file_path))
            if data is None:
                return ReadResult(error=f"Error: File '{file_path}' not found")
            lines = data.get("content", [])
            if not isinstance(lines, list):
                lines = str(lines).splitlines()
            return ReadResult(
                file_data={
                    "content": "\n".join(lines[max(offset, 0) : max(offset, 0) + limit]),
                    "encoding": "utf-8",
                }
            )
        result = self._call("read", "aread", file_path, offset, limit)
        if isinstance(result, str):
            if result.startswith("Error"):
                return ReadResult(error=result)
            return ReadResult(file_data={"content": result, "encoding": "utf-8"})
        return result

    def write(self, file_path: str, content: str) -> Any:
        from datetime import UTC, datetime

        from deepagents.backends.protocol import WriteResult

        exists = getattr(self._inner, "_exists", None)
        put_data = getattr(self._inner, "_put_file_data", None)
        if callable(exists) and callable(put_data):
            if self._run(exists(file_path)):
                return WriteResult(error=f"Cannot write to {file_path} because it already exists.")
            now = datetime.now(UTC).isoformat()
            data = {"content": content.splitlines(), "created_at": now, "modified_at": now}
            try:
                self._run(put_data(file_path, data, update_modified=False))
            except TypeError:
                self._run(put_data(file_path, data))
            return WriteResult(path=file_path)
        return self._call("write", "awrite", file_path, content)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> Any:
        get_data = getattr(self._inner, "_get_file_data", None)
        put_data = getattr(self._inner, "_put_file_data", None)
        if callable(get_data) and callable(put_data):
            from deepagents.backends.protocol import EditResult
            from deepagents.backends.utils import perform_string_replacement

            data = self._run(get_data(file_path))
            if data is None:
                return EditResult(error=f"Error: File '{file_path}' not found")
            text = "\n".join(data.get("content", []))
            changed = perform_string_replacement(text, old_string, new_string, replace_all)
            if isinstance(changed, str):
                return EditResult(error=changed)
            updated, count = changed
            data["content"] = updated.splitlines()
            try:
                self._run(put_data(file_path, data, update_modified=True))
            except TypeError:
                self._run(put_data(file_path, data))
            return EditResult(path=file_path, occurrences=int(count))

        return self._call("edit", "aedit", file_path, old_string, new_string, replace_all)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    async def awrite(self, file_path: str, content: str) -> Any:
        return await asyncio.to_thread(self.write, file_path, content)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Any:
        return await asyncio.to_thread(self.edit, file_path, old_string, new_string, replace_all)

    async def agrep(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.grep, *args, **kwargs)

    async def aglob(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.glob, *args, **kwargs)

    async def aupload_files(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.upload_files, *args, **kwargs)

    def grep(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("grep", "agrep", *args, **kwargs)

    def glob(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("glob", "aglob", *args, **kwargs)

    def upload_files(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("upload_files", "aupload_files", *args, **kwargs)

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)

    def download_files(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("download_files", "adownload_files", *args, **kwargs)

    async def adownload_files(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.download_files, *args, **kwargs)

    def close(self) -> None:
        close_future: Future[Any] | None = None
        close_error: BaseException | None = None
        with self._state_lock:
            if self._closing or self._closed:
                return
            self._closing = True
            close = getattr(self._inner, "close", None)
            if callable(close):
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        loop = self._worker_loop_locked()
                        close_future = asyncio.run_coroutine_threadsafe(
                            cast(Coroutine[Any, Any, Any], result), loop
                        )
                except BaseException as exc:
                    close_error = exc

        try:
            if close_future is not None:
                close_future.result(timeout=_BACKEND_IO_TIMEOUT)
        except BaseException as exc:
            if isinstance(exc, TimeoutError) and close_future is not None:
                close_future.cancel()
            close_error = close_error or exc

        with self._state_lock:
            closing_loop = self._loop
            runner = self._runner
            launcher_loop = self._launcher_loop
            executor = self._executor
            if closing_loop is not None:
                closing_loop.call_soon_threadsafe(closing_loop.stop)
        if runner is not None:
            if not self._runner_done.wait(timeout=_BACKEND_IO_TIMEOUT):
                close_error = close_error or TimeoutError("backend worker loop failed to stop")
            elif launcher_loop is not None:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    try:
                        launcher_loop.run_until_complete(runner)
                    except BaseException as exc:
                        close_error = close_error or exc
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if launcher_loop is not None and not launcher_loop.is_closed():
            launcher_loop.close()
        with self._state_lock:
            self._closed = True
            self._closing = False
        if close_error is not None:
            raise close_error

    def mkdir_path(self, path: str) -> None:
        mkdir = getattr(self._inner, "mkdir_path", None)
        if callable(mkdir):
            mkdir(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
