"""CLI state — pinned default user/agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from octop.infra.errors import corrupt_config_error
from octop.infra.utils.json_file import (
    JsonFileCorruptError,
    read_json_object,
    write_json_atomic,
)
from octop.infra.utils.paths import PathLayout


def default_state_path() -> Path:
    """Default location for the CLI state file (``~/maitu/cli_state.json``)."""
    return PathLayout.from_env().root / "cli_state.json"


@dataclass
class CLIState:
    default_user: str | None = None
    default_agent: str | None = None


def load(path: Path) -> CLIState:
    """Read pinned defaults; missing files yield empty state and corruption is typed."""
    try:
        raw = read_json_object(path)
    except JsonFileCorruptError as exc:
        raise corrupt_config_error(exc.path, exc.detail) from exc
    if raw is None:
        return CLIState()
    base: dict[str, Any] = asdict(CLIState())
    base.update(raw)
    allowed = {f.name for f in fields(CLIState)}
    return CLIState(**{k: v for k, v in base.items() if k in allowed})


def save(path: Path, state: CLIState) -> None:
    """Persist state atomically so an interrupted write cannot truncate it."""
    write_json_atomic(path, asdict(state))
