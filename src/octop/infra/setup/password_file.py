"""Setup wizard password file at ``<home>/maitu-login.txt``."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from octop.infra.utils.legacy_names import LEGACY_LOGIN_FILENAME
from octop.infra.utils.paths import LOGIN_FILENAME

WIZARD_FILE_NAME = LOGIN_FILENAME
# LEGACY: pre-white-label password file name. Read-only fallback plus one-time
# rename; never used for newly generated passwords.
_LEGACY_WIZARD_FILE_NAME = LEGACY_LOGIN_FILENAME
_TOKEN_BYTES = 16
_PASSWORD_LABEL_PREFIX = "Password:"


def _password_path(home: Path) -> Path:
    return home / WIZARD_FILE_NAME


def _legacy_password_path(home: Path) -> Path:
    return home / _LEGACY_WIZARD_FILE_NAME


def ensure_password(home: Path) -> str | None:
    """Generate the file if missing; return password or ``None`` if present."""
    home.mkdir(parents=True, exist_ok=True)
    _upgrade_legacy_password_file(home)
    target = _password_path(home)
    pw = secrets.token_urlsafe(_TOKEN_BYTES)
    try:
        fd = os.open(
            str(target),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return None
    try:
        os.write(fd, (pw + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    return pw


def _upgrade_legacy_password_file(home: Path) -> None:
    """Rename a pre-white-label password file in place, keeping its value."""
    legacy = _legacy_password_path(home)
    if not legacy.is_file():
        return
    target = _password_path(home)
    if target.exists():
        return
    try:
        legacy.replace(target)
    except OSError:
        # The read fallback below still resolves the legacy name.
        return


def _parse_password_file(target: Path) -> str | None:
    lines = target.read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(_PASSWORD_LABEL_PREFIX):
            value = stripped[len(_PASSWORD_LABEL_PREFIX) :].strip()
            if value:
                return value
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def read_password(home: Path) -> str | None:
    """Return the wizard password, or ``None`` if the file is absent/empty.

    Reads the current ``maitu-login.txt`` first and falls back to the
    pre-white-label ``octop-login.txt`` name so an upgrade never loses the
    operator's existing password.

    Supports two layouts:
      * Plain — the password is the file's first non-empty line, as written
        by :func:`ensure_password`.
      * Labeled — a ``Password: <value>`` line, used by deployment scripts
        (e.g. the Lighthouse image builder) that rewrite the plain file into
        a human-readable "URL / Password" card after first boot. Checked
        first so a labeled file always wins; falls back to the plain layout
        so existing callers/tests are unaffected.
    """
    for target in (_password_path(home), _legacy_password_path(home)):
        if target.is_file():
            value = _parse_password_file(target)
            if value is not None:
                return value
    return None


def boot_self_heal(home: Path, user_count: int) -> str | None:
    """Reconcile the wizard password file at server boot.

    When setup is still open (``user_count == 0``), ensure a password file exists
    and return the password so the CLI can print it — including when the file
    already exists (e.g. the DB was wiped but the login file was not).

    The file is intentionally kept forever as a permanent record and is never
    deleted, even after setup completes.
    """
    if user_count > 0:
        return None
    created = ensure_password(home)
    return created if created is not None else read_password(home)
