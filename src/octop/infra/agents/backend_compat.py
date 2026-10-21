"""Compatibility fixes for the installed DeepAgents and Harness backends.

Install once during server startup, after the runtime dependencies are imported.
"""

from __future__ import annotations

import locale
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from deepagents.backends.protocol import ExecuteResponse, GrepMatch, GrepResult
from deepagents.backends.utils import compile_grep_include_glob

# Fixed budgets for the pathless fallback: a broad query should stay useful but
# never turn into an unbounded recursive walk or a single giant-line allocation.
_PATHLESS_GREP_MAX_FILES = 2_000
_PATHLESS_GREP_MAX_BYTES = 32 * 1024 * 1024
_PATHLESS_GREP_MAX_LINE_BYTES = 1024 * 1024
_PATHLESS_GREP_MAX_RESULTS = 1_000
_PATHLESS_GREP_TIMEOUT = 10.0
_PATHLESS_GREP_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".next",
        ".nuxt",
        ".cache",
        "dist",
        "build",
        "target",
        "coverage",
    }
)


def _decode_output(raw: bytes, env: dict[str, str] | None) -> str:
    """Decode subprocess output without discarding bytes or assuming UTF-8 locale."""
    candidates: list[str] = []
    explicit = (env or {}).get("PYTHONIOENCODING")
    if explicit:
        candidates.append(explicit.split(":", 1)[0])
    try:
        preferred = locale.getpreferredencoding(False)
    except (locale.Error, LookupError):
        preferred = None
    candidates.append("utf-8")
    if preferred and any(
        token in preferred.lower() for token in ("gb", "936", "big5", "950", "euc")
    ):
        candidates.append(preferred)
    candidates.append("gb18030")
    if preferred:
        candidates.append(preferred)
    candidates.extend(("cp1252", "latin-1"))
    seen: set[str] = set()
    for encoding in candidates:
        if not encoding or encoding.lower() in seen:
            continue
        seen.add(encoding.lower())
        try:
            return raw.decode(encoding, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1")


def _format_binary_result(
    result: subprocess.CompletedProcess[bytes], *, max_output_bytes: int, env: dict[str, str] | None
) -> ExecuteResponse:
    parts: list[str] = []
    if result.stdout:
        parts.append(_decode_output(result.stdout, env))
    if result.stderr:
        parts.extend(
            f"[stderr] {line}" for line in _decode_output(result.stderr, env).strip().split("\n")
        )
    output = "\n".join(parts) if parts else "<no output>"
    truncated = len(output.encode("utf-8")) > max_output_bytes
    if truncated:
        raw = output.encode("utf-8")[:max_output_bytes]
        output = (
            raw.decode("utf-8", errors="ignore")
            + f"\n\n... Output truncated at {max_output_bytes} bytes."
        )
    if result.returncode:
        output = f"{output.rstrip()}\n\nExit code: {result.returncode}"
    return ExecuteResponse(output=output, exit_code=result.returncode, truncated=truncated)


def _bounded_pathless_grep(backend: Any, pattern: str, glob: str | None) -> GrepResult:
    """Literal UTF-8 grep with bounded traversal and root containment."""
    root = Path(backend.cwd).resolve()
    try:
        include = compile_grep_include_glob(glob) if glob else None
    except ValueError as exc:
        return GrepResult(error=f"Invalid glob pattern: {exc}", matches=[])
    deadline = time.monotonic() + _PATHLESS_GREP_TIMEOUT
    matches: list[GrepMatch] = []
    files_seen = bytes_seen = 0
    truncated = False
    errors: list[str] = []

    def walk_error(exc: OSError) -> None:
        errors.append(f"Unable to search part of workspace ({exc.strerror or type(exc).__name__})")

    for current, dirs, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=walk_error
    ):
        if time.monotonic() >= deadline:
            truncated = True
            break
        dirs[:] = [
            d
            for d in dirs
            if d not in _PATHLESS_GREP_EXCLUDED_DIRS and not (Path(current) / d).is_symlink()
        ]
        for filename in filenames:
            if (
                time.monotonic() >= deadline
                or files_seen >= _PATHLESS_GREP_MAX_FILES
                or bytes_seen >= _PATHLESS_GREP_MAX_BYTES
                or len(matches) >= _PATHLESS_GREP_MAX_RESULTS
            ):
                truncated = True
                break
            path = Path(current) / filename
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                if not resolved.is_file():
                    continue
                size = resolved.stat().st_size
            except (OSError, RuntimeError, ValueError):
                continue
            relative = path.relative_to(root).as_posix()
            if include is not None and not include(relative):
                continue
            if size > getattr(backend, "max_file_size_bytes", 10 * 1024 * 1024):
                continue
            files_seen += 1
            try:
                with resolved.open("rb") as handle:
                    line_number = 0
                    while True:
                        if time.monotonic() >= deadline or bytes_seen >= _PATHLESS_GREP_MAX_BYTES:
                            truncated = True
                            break
                        raw_line = handle.readline(_PATHLESS_GREP_MAX_LINE_BYTES + 1)
                        if not raw_line:
                            break
                        bytes_seen += len(raw_line)
                        line_number += 1
                        if len(raw_line) > _PATHLESS_GREP_MAX_LINE_BYTES:
                            # Oversized lines are not searched, but consumed to
                            # newline in bounded chunks so their tail is never a fake line.
                            while raw_line and not raw_line.endswith(b"\n"):
                                if (
                                    bytes_seen >= _PATHLESS_GREP_MAX_BYTES
                                    or time.monotonic() >= deadline
                                ):
                                    truncated = True
                                    break
                                raw_line = handle.readline(_PATHLESS_GREP_MAX_LINE_BYTES + 1)
                                bytes_seen += len(raw_line)
                            if truncated:
                                break
                            continue
                        try:
                            line = raw_line.decode("utf-8", errors="strict").rstrip("\r\n")
                        except UnicodeDecodeError:
                            # Like ripgrep, skip binary/non-UTF-8 files without
                            # returning corrupted text.
                            break
                        if pattern in line:
                            try:
                                display_path = (
                                    backend._to_virtual_path(path)
                                    if backend.virtual_mode
                                    else str(path)
                                )
                            except (OSError, RuntimeError, ValueError):
                                continue
                            matches.append(
                                {"path": display_path, "line": line_number, "text": line}
                            )
                            if len(matches) >= _PATHLESS_GREP_MAX_RESULTS:
                                truncated = True
                                break
                    if truncated:
                        break
            except OSError as exc:
                errors.append(f"Unable to read {relative}: {exc.strerror or type(exc).__name__}")
        if truncated:
            break

    return GrepResult(
        error="\n".join(errors) if errors else None, matches=matches, truncated=truncated
    )


def install_backend_compatibility_patches() -> None:
    """Patch the exact installed backend classes used by Octop's agents.

    Safe to call repeatedly; marker attributes make installation idempotent.
    """
    import deepagents.backends.filesystem as filesystem_module
    from deepagents.backends.filesystem import FilesystemBackend
    from harness_agent.backends.bwrap_shell import BubbledLocalShellBackend
    from harness_agent.backends.local_shell import HarnessLocalShellBackend

    if not getattr(FilesystemBackend, "_octop_utf8_rg_installed", False):
        subprocess_api = SimpleNamespace(**vars(subprocess))
        original_popen = subprocess.Popen

        def utf8_popen(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("text") or kwargs.get("universal_newlines"):
                kwargs.setdefault("encoding", "utf-8")
                kwargs.setdefault("errors", "strict")
            return original_popen(*args, **kwargs)

        subprocess_api.Popen = utf8_popen
        cast(Any, filesystem_module).subprocess = subprocess_api
        cast(Any, FilesystemBackend)._octop_utf8_rg_installed = True
    if not getattr(FilesystemBackend, "_octop_bounded_grep_installed", False):
        original_grep = FilesystemBackend.grep

        def grep(
            self: Any,
            pattern: str,
            path: str | None = None,
            glob: str | None = None,
            *,
            max_count: int | None = None,
            context_lines: int = 0,
        ) -> GrepResult:
            if path is None and not filesystem_module._resolve_ripgrep_path():
                result = _bounded_pathless_grep(self, pattern, glob)
                if (
                    max_count is not None
                    and result.matches is not None
                    and len(result.matches) > max_count
                ):
                    result.matches = result.matches[:max_count]
                    result.truncated = True
                if context_lines and result.matches:
                    result.error = self._apply_grep_context(
                        result.matches,
                        context_lines,
                        result.error,
                        pattern,
                        newline=None,
                    )
                return result
            return original_grep(
                self, pattern, path, glob, max_count=max_count, context_lines=context_lines
            )

        original_python_search = FilesystemBackend._python_search

        def python_search(
            self: Any,
            pattern: str,
            base_full: Path,
            include_glob: str | None,
            *,
            max_count: int | None = None,
            timeout: int = 15,
        ) -> tuple[dict[str, list[tuple[int, str]]], bool, str | None]:
            if base_full.resolve() == Path(self.cwd).resolve():
                result = _bounded_pathless_grep(self, pattern, include_glob)
                items: dict[str, list[tuple[int, str]]] = {}
                for match in result.matches or ():
                    items.setdefault(match["path"], []).append((match["line"], match["text"]))
                truncated = result.truncated
                if max_count is not None:
                    count = 0
                    capped: dict[str, list[tuple[int, str]]] = {}
                    for file_path, file_matches in items.items():
                        remaining = max_count - count
                        if remaining <= 0:
                            truncated = True
                            break
                        capped[file_path] = file_matches[:remaining]
                        count += len(capped[file_path])
                        if count < sum(len(values) for values in items.values()):
                            truncated = True
                    items = capped
                return items, truncated, result.error
            return original_python_search(
                self, pattern, base_full, include_glob, max_count=max_count, timeout=timeout
            )

        cast(Any, FilesystemBackend)._python_search = python_search
        cast(Any, FilesystemBackend).grep = grep
        cast(Any, FilesystemBackend)._octop_bounded_grep_installed = True

    if not getattr(HarnessLocalShellBackend, "_octop_utf8_output_installed", False):

        def execute_on_host(self: Any, command: str, *, timeout: int | None) -> ExecuteResponse:
            effective_timeout = timeout if timeout is not None else self._default_timeout
            if effective_timeout <= 0:
                raise ValueError(f"timeout must be positive, got {effective_timeout}")
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    shell=True,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    timeout=effective_timeout,
                    env=self._env,
                    cwd=str(self._host_execute_cwd()),
                )
                return _format_binary_result(
                    result, max_output_bytes=self._max_output_bytes, env=self._env
                )
            except subprocess.TimeoutExpired:
                return ExecuteResponse(
                    output=f"Error: Command timed out after {effective_timeout} seconds.",
                    exit_code=124,
                    truncated=False,
                )
            except Exception as exc:
                return ExecuteResponse(
                    output=f"Error executing command ({type(exc).__name__}): {exc}",
                    exit_code=1,
                    truncated=False,
                )

        cast(Any, HarnessLocalShellBackend)._execute_on_host = execute_on_host
        cast(Any, HarnessLocalShellBackend)._octop_utf8_output_installed = True

    if not getattr(BubbledLocalShellBackend, "_octop_utf8_output_installed", False):
        from harness_agent.backends.bwrap_shell import build_bwrap_argv

        def execute_bwrap(
            self: Any, command: str, *, timeout: int | None = None
        ) -> ExecuteResponse:
            if not command or not isinstance(command, str):
                return cast(
                    ExecuteResponse,
                    super(BubbledLocalShellBackend, self).execute(command, timeout=timeout),
                )
            self._refresh_execute_env()
            bwrap = self._bwrap_path
            if bwrap is None:
                return cast(
                    ExecuteResponse,
                    super(BubbledLocalShellBackend, self).execute(command, timeout=timeout),
                )
            effective_timeout = timeout if timeout is not None else self._default_timeout
            if effective_timeout <= 0:
                raise ValueError(f"timeout must be positive, got {effective_timeout}")
            argv = build_bwrap_argv(
                bwrap=bwrap,
                root_dir=self.cwd,
                command=command,
                work_dir=self._virtual_workspace_cwd(),
                extra_binds=self._skill_extra_binds(),
            )
            try:
                result = subprocess.run(
                    argv,
                    check=False,
                    shell=False,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    timeout=effective_timeout,
                    env=self._env,
                )
                response = _format_binary_result(
                    result, max_output_bytes=self._max_output_bytes, env=self._env
                )
                return cast(ExecuteResponse, self._present_execute_response(response))
            except FileNotFoundError:
                self._bwrap_path = None
                return cast(
                    ExecuteResponse,
                    super(BubbledLocalShellBackend, self).execute(command, timeout=timeout),
                )
            except subprocess.TimeoutExpired:
                return ExecuteResponse(
                    output=f"Error: Command timed out after {effective_timeout} seconds.",
                    exit_code=124,
                    truncated=False,
                )
            except Exception as exc:
                return ExecuteResponse(
                    output=f"Error executing command ({type(exc).__name__}): {exc}",
                    exit_code=1,
                    truncated=False,
                )

        cast(Any, BubbledLocalShellBackend).execute = execute_bwrap
        cast(Any, BubbledLocalShellBackend)._octop_utf8_output_installed = True
