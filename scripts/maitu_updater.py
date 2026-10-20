"""Host-side MAITU source updater. Run outside the business container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ValueError("release service redirects are not allowed")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object: {path.name}")
    return data


def write_ready_heartbeat(queue_dir: Path, *, mode: str, unit: str | None) -> None:
    """Atomically advertise a live updater; never include tokens or secrets."""
    if mode not in {"docker", "native"}:
        raise ValueError("invalid updater mode")
    atomic_json(
        queue_dir / "updater-ready.json",
        {
            "mode": mode,
            "ready": True,
            "pid": os.getpid(),
            "updated_at": datetime.now(UTC).isoformat(),
            "unit": unit,
        },
    )


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    with path.open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def validate_zip(archive: Path, target: Path, manifest: dict[str, Any]) -> None:
    """Extract only bounded regular source files; never follow archive links."""
    with zipfile.ZipFile(archive) as source:
        seen: set[str] = set()
        expanded = 0
        for info in source.infolist():
            name = info.orig_filename
            path = PurePosixPath(name)
            mode = info.external_attr >> 16
            if (
                not name
                or "\\" in name
                or "\x00" in name
                or ":" in name
                or path.is_absolute()
                or any(p in {"", ".", ".."} for p in name.rstrip("/").split("/"))
                or name.rstrip("/").casefold() in seen
                or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))
                or info.flag_bits & 1
            ):
                raise ValueError("unsafe source archive entry")
            seen.add(name.rstrip("/").casefold())
            expanded += info.file_size
            if expanded > MAX_EXPANDED or len(seen) > 100000:
                raise ValueError("source archive exceeds extraction limits")
        metadata_info = source.getinfo("maitu-release.json")
        project_info = source.getinfo("pyproject.toml")
        if metadata_info.file_size > 1024 * 1024 or project_info.file_size > 1024 * 1024:
            raise ValueError("release metadata exceeds limit")
        metadata = json.loads(source.read(metadata_info))
        project = tomllib.loads(source.read(project_info).decode("utf-8"))
        for field in ("product", "version", "package_type", "platform", "architecture"):
            if metadata.get(field) != manifest[field]:
                raise ValueError(f"release metadata mismatch: {field}")
        if str(Version(project["project"]["version"])) != manifest["version"]:
            raise ValueError("source project version differs from signed release")
        for required in (
            "uv.lock",
            "docker/Dockerfile",
            "dashboard/package.json",
            "dashboard/package-lock.json",
        ):
            if required.casefold() not in seen:
                raise ValueError(f"source archive missing {required}")
        for info in source.infolist():
            destination = target / info.filename
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as input_file, destination.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file, 1024 * 1024)
            destination.chmod(0o755 if (info.external_attr >> 16) & 0o111 else 0o644)


@dataclass(frozen=True)
class Settings:
    center_url: str
    client_token: str
    trusted_keys_file: Path
    queue_dir: Path
    project_dir: Path
    data_dir: Path
    state_dir: Path
    health_url: str
    compose_file: Path
    service: str = "maitu"
    architecture: str = "amd64"
    allow_loopback: bool = False
    postgres_dsn: str | None = None
    health_timeout: int = 180
    extra_compose_files: tuple[Path, ...] = ()

    def validate(self) -> None:
        parsed = urllib.parse.urlsplit(self.center_url)
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or (
                parsed.scheme != "https"
                and not (self.allow_loopback and loopback and parsed.scheme == "http")
            )
        ):
            raise ValueError(
                "center URL must be trusted HTTPS (or explicitly enabled loopback HTTP)"
            )
        if not self.client_token or not self.trusted_keys_file.is_file():
            raise ValueError("client token and release trusted-key file are required")
        try:
            keyring = json.loads(self.trusted_keys_file.read_text(encoding="utf-8"))
            if not isinstance(keyring, dict) or not keyring:
                raise ValueError("release trusted keys must be a non-empty JSON object")
            for kid, pem in keyring.items():
                if not isinstance(kid, str) or not kid or not isinstance(pem, str):
                    raise ValueError("release trusted keys must map IDs to PEM strings")
                key = serialization.load_pem_public_key(pem.encode("utf-8"))
                if not isinstance(key, Ed25519PublicKey):
                    raise ValueError(f"release trusted key {kid!r} is not Ed25519")
        except Exception as exc:
            raise ValueError("release trusted-key file is invalid") from exc
        if self.architecture not in {"amd64", "arm64"}:
            raise ValueError("unsupported architecture")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.service):
            raise ValueError("invalid Compose service")
        if (
            not self.project_dir.is_dir()
            or not self.compose_file.is_file()
            or not self.data_dir.is_dir()
        ):
            raise ValueError("existing project, Compose file and data directory are required")
        if any(not path.is_file() for path in self.extra_compose_files):
            raise ValueError("all configured Compose overlay files must exist")
        for first, second in (
            (self.state_dir, self.data_dir),
            (self.state_dir, self.project_dir),
            (self.queue_dir, self.data_dir),
            (self.queue_dir, self.state_dir),
        ):
            if first == second or first.is_relative_to(second) or second.is_relative_to(first):
                raise ValueError(
                    "updater state, queue, project and data must use separate directories"
                )
        if self.data_dir == Path(self.data_dir.anchor) or self.data_dir.is_symlink():
            raise ValueError("data directory cannot be a filesystem root or link")


_RELEASE_ENV_KEYS = ("MAITU_RELEASE_ROOT", "MAITU_RELEASE_VENV")
_RELEASE_ENV_VALUE_RE = re.compile(r"[A-Za-z0-9._@+~,:=\\/-]+")
_UNIT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]*\.service")
_EXTRA_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_MAX_RELEASE_ENV_BYTES = 8192
_BUILD_SECRET_ENV_NAMES = frozenset({"MAITU_UPDATE_CLIENT_TOKEN", "MAITU_UPDATE_POSTGRES_DSN"})


@dataclass(frozen=True)
class ReleaseEnvironment:
    """Paths read from / written to the atomic release environment file."""

    root: Path
    venv: Path


def _validate_center_url(center_url: str, allow_loopback: bool) -> None:
    parsed = urllib.parse.urlsplit(center_url)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (allow_loopback and loopback and parsed.scheme == "http")
        )
    ):
        raise ValueError("center URL must be trusted HTTPS (or explicitly enabled loopback HTTP)")


def _validate_trusted_keys(path: Path) -> None:
    if not path.is_file():
        raise ValueError("release trusted-key file is required")
    try:
        keyring = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(keyring, dict) or not keyring:
            raise ValueError("release trusted keys must be a non-empty JSON object")
        for kid, pem in keyring.items():
            if not isinstance(kid, str) or not kid or not isinstance(pem, str):
                raise ValueError("release trusted keys must map IDs to PEM strings")
            key = serialization.load_pem_public_key(pem.encode("utf-8"))
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError(f"release trusted key {kid!r} is not Ed25519")
    except Exception as exc:
        raise ValueError("release trusted-key file is invalid") from exc


def _validate_release_env_value(value: str) -> Path:
    if not value or value != value.strip() or not _RELEASE_ENV_VALUE_RE.fullmatch(value):
        raise ValueError("release environment values must be safe absolute paths")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("release environment values must be safe absolute paths")
    return path


def parse_release_env(text: str) -> ReleaseEnvironment:
    """Parse the release env file without shell expansion or quoting rules."""
    if len(text.encode("utf-8")) > _MAX_RELEASE_ENV_BYTES:
        raise ValueError("release environment file is too large")
    values: dict[str, Path] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in _RELEASE_ENV_KEYS:
            raise ValueError("release environment file contains unsupported entries")
        if key in values:
            raise ValueError("release environment file repeats a key")
        values[key] = _validate_release_env_value(value.strip())
    if set(values) != set(_RELEASE_ENV_KEYS):
        raise ValueError(
            "release environment file must define MAITU_RELEASE_ROOT and MAITU_RELEASE_VENV"
        )
    root = values["MAITU_RELEASE_ROOT"]
    venv = values["MAITU_RELEASE_VENV"]
    if root == venv or root.is_relative_to(venv):
        raise ValueError("release root and release venv must be separate paths")
    return ReleaseEnvironment(root=root, venv=venv)


def _validate_release_env_location(path: Path, *, for_write: bool = False) -> None:
    if not path.is_absolute():
        raise ValueError("release environment file path must be absolute")
    if path.is_symlink():
        raise ValueError("release environment file must not be a symlink")
    if path.exists() and not path.is_file():
        raise ValueError("release environment file must be a regular file")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("release environment directory must exist and not be a symlink")
    if os.name == "posix":
        if path.exists():
            file_mode = stat.S_IMODE(path.stat().st_mode)
            if file_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise ValueError("release environment file must not be group/world-writable")
        directory_mode = stat.S_IMODE(parent.stat().st_mode)
        if directory_mode & stat.S_IWOTH:
            raise ValueError("release environment directory must not be world-writable")
    if for_write and not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError("release environment directory is not writable")


def read_release_env(path: Path) -> ReleaseEnvironment:
    """Read and strictly validate the current release env file."""
    _validate_release_env_location(path)
    if not path.is_file():
        raise ValueError("release environment file is missing")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError("release environment file is unreadable") from None
    return parse_release_env(text)


def render_release_env(environment: ReleaseEnvironment) -> str:
    root = _validate_release_env_value(str(environment.root))
    venv = _validate_release_env_value(str(environment.venv))
    return f"MAITU_RELEASE_ROOT={root}\nMAITU_RELEASE_VENV={venv}\n"


def write_release_env(path: Path, environment: ReleaseEnvironment) -> None:
    """Atomically replace the release env file; never shell-evaluate its values."""
    _validate_release_env_location(path, for_write=True)
    text = render_release_env(environment)
    parse_release_env(text)  # self-check before replacing the live file
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    if os.name == "posix":
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass


def release_source_version(root: Path) -> str:
    """Version declared by an installed release checkout."""
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        version = project["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError, UnicodeError):
        raise ValueError("existing release source has no readable project version") from None
    if not isinstance(version, str):
        raise ValueError("existing release source has no readable project version")
    try:
        return str(Version(version))
    except InvalidVersion:
        raise ValueError("existing release source has an invalid project version") from None


def _validate_native_command(value: str, label: str) -> None:
    raw = value.strip()
    if not raw or raw.startswith("-") or any(char in raw for char in "\x00\r\n"):
        raise ValueError(f"native {label} command is invalid")


def _resolve_native_command(value: str, label: str) -> str:
    _validate_native_command(value, label)
    raw = value.strip()
    if os.path.isabs(raw):
        if not Path(raw).is_file():
            raise ValueError(f"native {label} executable does not exist")
        return raw
    resolved = shutil.which(raw)
    if resolved is None:
        raise ValueError(f"native {label} executable is not available on PATH")
    return resolved


def _build_environment(extra: dict[str, str]) -> dict[str, str]:
    """Inherit the host build environment without updater credentials."""
    environment = {
        key: value for key, value in os.environ.items() if key not in _BUILD_SECRET_ENV_NAMES
    }
    environment.update(extra)
    return environment


@dataclass(frozen=True)
class NativeSettings:
    """Host paths and commands for the native systemd updater."""

    center_url: str
    client_token: str
    trusted_keys_file: Path
    queue_dir: Path
    state_dir: Path
    data_dir: Path
    health_url: str
    release_env_file: Path
    systemd_unit: str
    updater_root: Path
    architecture: str = "amd64"
    allow_loopback: bool = False
    extras: tuple[str, ...] = ("office", "knowledge-ocr")
    health_timeout: int = 180
    postgres_dsn: str | None = None
    uv: str = "uv"
    npm: str = "npm"

    def validate(self) -> None:
        _validate_center_url(self.center_url, self.allow_loopback)
        if not self.client_token:
            raise ValueError("client token is required")
        _validate_trusted_keys(self.trusted_keys_file)
        if self.architecture not in {"amd64", "arm64"}:
            raise ValueError("unsupported architecture")
        if not _UNIT_NAME_RE.fullmatch(self.systemd_unit):
            raise ValueError("invalid systemd unit name")
        if self.health_timeout <= 0:
            raise ValueError("health timeout must be positive")
        for extra in self.extras:
            if not _EXTRA_NAME_RE.fullmatch(extra):
                raise ValueError("invalid native extra name")
        _validate_native_command(self.uv, "uv")
        _validate_native_command(self.npm, "npm")
        for path in (self.queue_dir, self.state_dir, self.data_dir, self.updater_root):
            if not path.is_absolute():
                raise ValueError("native updater paths must be absolute")
        if (
            not self.data_dir.is_dir()
            or self.data_dir.is_symlink()
            or self.data_dir == Path(self.data_dir.anchor)
        ):
            raise ValueError("existing data directory is required")
        if not self.updater_root.is_dir() or self.updater_root.is_symlink():
            raise ValueError("updater root must be an existing directory")
        for first, second in (
            (self.state_dir, self.data_dir),
            (self.queue_dir, self.data_dir),
            (self.queue_dir, self.state_dir),
            (self.updater_root, self.data_dir),
            (self.updater_root, self.state_dir),
            (self.updater_root, self.queue_dir),
        ):
            if first == second or first.is_relative_to(second) or second.is_relative_to(first):
                raise ValueError(
                    "native state, queue, data and updater root must use separate directories"
                )
        _validate_release_env_location(self.release_env_file)
        if not self.release_env_file.is_file():
            raise ValueError("existing release environment file is required")
        for protected in (self.data_dir, self.updater_root):
            if self.release_env_file.is_relative_to(protected):
                raise ValueError("release environment file must be separate from runtime paths")
        read_release_env(self.release_env_file)


class _ReleaseSource:
    """Signed-release HTTP client shared by the Docker and native updaters."""

    def __init__(self, settings: Any):
        self.settings = settings
        self.opener = urllib.request.build_opener(NoRedirect())

    def http(self, suffix: str) -> Any:
        request = urllib.request.Request(
            self.settings.center_url.rstrip("/") + suffix,
            headers={"Authorization": f"Bearer {self.settings.client_token}"},
        )
        return self.opener.open(request, timeout=30)

    def manifest(self) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {"platform": "linux", "architecture": self.settings.architecture}
        )
        with self.http("/api/v1/releases/latest?" + query) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("release metadata response too large")
        token = json.loads(raw).get("manifest")
        if not isinstance(token, str):
            raise ValueError("no published compatible release")
        header = jwt.get_unverified_header(token)
        if (
            not isinstance(header, dict)
            or set(header) != {"alg", "kid", "typ"}
            or header.get("alg") != "EdDSA"
            or header.get("typ") != "JWT"
            or not isinstance(header.get("kid"), str)
        ):
            raise ValueError("invalid release signing-key header")
        keyring = json.loads(self.settings.trusted_keys_file.read_text(encoding="utf-8"))
        pem = keyring.get(header["kid"]) if isinstance(keyring, dict) else None
        if not isinstance(pem, str):
            raise ValueError("unrecognized signing key")
        public_key = serialization.load_pem_public_key(pem.encode("utf-8"))
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("release signing key is not Ed25519")
        manifest: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=["EdDSA"],
            audience="maitu-updater",
            issuer="maitu-license-center",
            options={"require": ["iat", "iss", "aud", "release_id", "version", "sha256", "size"]},
        )
        expected = {
            "product": "maitu",
            "protocol": 1,
            "package_type": "source",
            "platform": "linux",
            "architecture": self.settings.architecture,
        }
        if any(manifest.get(field) != value for field, value in expected.items()):
            raise ValueError("release is incompatible with this installation")
        if not re.fullmatch(r"[a-f0-9]{32}", str(manifest["release_id"])) or not re.fullmatch(
            r"[a-f0-9]{64}", str(manifest["sha256"])
        ):
            raise ValueError("invalid signed release identity or digest")
        if type(manifest["size"]) is not int or not 0 < manifest["size"] <= MAX_ARCHIVE:
            raise ValueError("invalid signed release size")
        if (
            str(Version(manifest["version"])) != manifest["version"]
            or manifest.get("filename") != f"maitu-{manifest['version']}.zip"
        ):
            raise ValueError("invalid signed release version or filename")
        return manifest

    def download(self, manifest: dict[str, Any], archive: Path) -> None:
        digest = hashlib.sha256()
        size = 0
        with (
            self.http(f"/api/v1/releases/{manifest['release_id']}/download") as response,
            archive.open("xb") as stream,
        ):
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > manifest["size"]:
                    raise ValueError("release download exceeds declared size")
                digest.update(chunk)
                stream.write(chunk)
        if size != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
            raise ValueError("release archive checksum or size mismatch")

    def health(self, version: str, *, require_version: bool = True) -> None:
        deadline = time.monotonic() + self.settings.health_timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.settings.health_url, timeout=5) as response:
                    result = json.load(response)
                if result.get("ok") is True and (
                    not require_version or result.get("version") == version
                ):
                    return
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(2)
        raise RuntimeError("new service did not report healthy with the expected installed version")


class NativeUpdater(_ReleaseSource):
    """Transactional updater for native source / systemd installations."""

    def __init__(self, settings: NativeSettings):
        super().__init__(settings)
        settings.validate()
        settings.queue_dir.mkdir(parents=True, exist_ok=True)
        # The app user must traverse this directory to read a release tree, but
        # must not be able to list backups or transaction state.
        settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o711)
        if os.name == "posix":
            settings.state_dir.chmod(0o711)
        releases = settings.state_dir / "releases"
        releases.mkdir(parents=True, exist_ok=True, mode=0o755)
        if os.name == "posix":
            releases.chmod(0o755)
        self.settings: NativeSettings = settings
        self.request: dict[str, Any] = {}
        self.journal_path = settings.state_dir / "native-transaction.json"
        self.write_ready()

    def write_ready(self) -> None:
        """Advertise a live native updater for the platform readiness check."""
        write_ready_heartbeat(
            self.settings.queue_dir, mode="native", unit=self.settings.systemd_unit
        )

    def state(self, phase: str, error: str | None = None) -> None:
        atomic_json(
            self.settings.queue_dir / "upgrade-state.json",
            {
                "phase": phase,
                "target_version": self.request.get("version"),
                "error": error,
                "request_id": self.request.get("request_id"),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )

    def systemctl(self, action: str) -> None:
        if action not in {"start", "stop"}:
            raise ValueError("unsupported systemd action")
        subprocess.run(
            ["systemctl", action, self.settings.systemd_unit],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def verify_old_release(self) -> tuple[ReleaseEnvironment, str]:
        """Read the staged old release and require health to report its version."""
        environment = read_release_env(self.settings.release_env_file)
        if not environment.root.is_dir() or not (environment.root / "pyproject.toml").is_file():
            raise ValueError("existing release source directory is invalid")
        if not environment.venv.is_dir() or not (environment.venv / "pyvenv.cfg").is_file():
            raise ValueError("existing release virtual environment is invalid")
        version = release_source_version(environment.root)
        try:
            self.health(version)
        except RuntimeError as exc:
            raise ValueError(
                "running service health does not report the installed release version"
            ) from exc
        return environment, version

    def build_release(self, manifest: dict[str, Any], release: Path) -> ReleaseEnvironment:
        """Install a fresh release venv and build its dashboard; never reuse old trees."""
        project = tomllib.loads((release / "pyproject.toml").read_text(encoding="utf-8"))
        available = project["project"].get("optional-dependencies", {})
        missing = [extra for extra in self.settings.extras if extra not in available]
        if missing:
            raise ValueError(f"release does not provide native extras: {', '.join(missing)}")
        venv = release / ".venv"
        if venv.exists() or venv.is_symlink():
            raise ValueError("release archive must not contain a virtual environment")
        uv = _resolve_native_command(self.settings.uv, "uv")
        npm = _resolve_native_command(self.settings.npm, "npm")
        environment = _build_environment({"UV_PROJECT_ENVIRONMENT": str(venv)})
        arguments = [uv, "sync", "--frozen", "--no-dev", "--python", sys.executable]
        for extra in self.settings.extras:
            arguments += ["--extra", extra]
        subprocess.run(
            arguments,
            cwd=release,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        dashboard = release / "dashboard"
        for build_arguments in ([npm, "ci"], [npm, "run", "build"]):
            subprocess.run(
                build_arguments,
                cwd=dashboard,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=3600,
            )
        if not venv.is_dir() or not any(venv.iterdir()):
            raise ValueError("independent release virtual environment was not created")
        return ReleaseEnvironment(root=release, venv=venv)

    def backup(self, transaction: dict[str, Any]) -> None:
        destination = Path(transaction["backup"])
        with tarfile.open(destination, "x:gz") as archive:
            archive.add(self.settings.data_dir, arcname="data", recursive=True)
        if os.name == "posix":
            destination.chmod(0o600)
        if self.settings.postgres_dsn:
            dump = Path(transaction["pg_dump"])
            environment = {**os.environ, "PGDATABASE": self.settings.postgres_dsn}
            subprocess.run(
                ["pg_dump", "--format=custom", "--file", str(dump)],
                env=environment,
                check=True,
                capture_output=True,
                timeout=1800,
            )
            if os.name == "posix":
                dump.chmod(0o600)
        transaction["backup_complete"] = True
        atomic_json(self.journal_path, transaction)

    def switch_release_env(self, environment: ReleaseEnvironment) -> None:
        write_release_env(self.settings.release_env_file, environment)

    def recover(self, transaction: dict[str, Any]) -> None:
        """Restore the old release env, data backup and service after a failure."""
        self.systemctl("stop")
        if transaction.get("backup_complete"):
            data_dir = self.settings.data_dir
            if data_dir.exists():
                failed = self.settings.state_dir / f"failed-data-{uuid.uuid4().hex}"
                shutil.move(str(data_dir), str(failed))
            recovery = self.settings.state_dir / f"recovery-{uuid.uuid4().hex}"
            recovery.mkdir()
            with tarfile.open(transaction["backup"], "r:gz") as archive:
                # Backup produced locally after quiescing the service, never from a release ZIP.
                archive.extractall(recovery, filter="data")
            shutil.move(str(recovery / "data"), str(data_dir))
            recovery.rmdir()
            if self.settings.postgres_dsn:
                environment = {**os.environ, "PGDATABASE": self.settings.postgres_dsn}
                subprocess.run(
                    [
                        "pg_restore",
                        "--clean",
                        "--if-exists",
                        "--exit-on-error",
                        "--dbname",
                        "",
                        transaction["pg_dump"],
                    ],
                    env=environment,
                    check=True,
                    capture_output=True,
                    timeout=1800,
                )
        old = transaction.get("old_env")
        if (
            not isinstance(old, dict)
            or not isinstance(old.get("root"), str)
            or not isinstance(old.get("venv"), str)
        ):
            raise ValueError("recovery journal has no valid old release environment")
        write_release_env(
            self.settings.release_env_file,
            ReleaseEnvironment(root=Path(old["root"]), venv=Path(old["venv"])),
        )
        self.systemctl("start")
        self.health(transaction["old_version"])
        self.journal_path.unlink()

    def installation(self, manifest: dict[str, Any], release: Path) -> None:
        old_environment, old_version = self.verify_old_release()
        target_version = manifest["version"]
        if Version(target_version) <= Version(old_version):
            raise ValueError("target version is not newer than the running installation")
        self.state("building")
        new_environment = self.build_release(manifest, release)
        request_id = str(self.request.get("request_id"))
        transaction: dict[str, Any] = {
            "request": self.request,
            "old_env": {"root": str(old_environment.root), "venv": str(old_environment.venv)},
            "old_version": old_version,
            "target_version": target_version,
            "backup": str(self.settings.state_dir / f"native-backup-{request_id}.tar.gz"),
            "pg_dump": str(self.settings.state_dir / f"native-backup-{request_id}.pgdump"),
            "backup_complete": False,
        }
        atomic_json(self.journal_path, transaction)
        try:
            self.state("stopping")
            self.systemctl("stop")
            self.state("backing_up")
            self.backup(transaction)
            self.state("installing")
            self.switch_release_env(new_environment)
            self.state("restarting")
            self.systemctl("start")
            self.state("verifying")
            self.health(target_version)
        except Exception:
            self.recover(transaction)
            raise
        self.journal_path.unlink()

    def _validate_request(self) -> None:
        if not re.fullmatch(r"[a-f0-9-]{32,36}", str(self.request.get("request_id", ""))):
            raise ValueError("invalid upgrade request ID")
        if not isinstance(self.request.get("version"), str) or not isinstance(
            self.request.get("release_id"), str
        ):
            raise ValueError("invalid upgrade request")

    def process(self) -> bool:
        with exclusive_lock(self.settings.state_dir / "updater.lock"):
            self.write_ready()
            if self.journal_path.exists():
                transaction = load_json(self.journal_path)
                request = transaction.get("request")
                self.request = request if isinstance(request, dict) else {}
                try:
                    self.recover(transaction)
                    self.state(
                        "failed",
                        "Interrupted native upgrade restored the previous release and data",
                    )
                except Exception as exc:
                    self.state(
                        "failed",
                        "Interrupted native upgrade recovery failed: "
                        f"{type(exc).__name__}; operator recovery required",
                    )
                    return True
                request_file = self.settings.queue_dir / "upgrade-request.json"
                if request_file.exists() and load_json(request_file).get(
                    "request_id"
                ) == self.request.get("request_id"):
                    request_file.unlink()
                return True
            request_file = self.settings.queue_dir / "upgrade-request.json"
            if not request_file.exists():
                return False
            try:
                self.request = load_json(request_file)
                self._validate_request()
                self.state("downloading")
                manifest = self.manifest()
                if any(
                    self.request.get(field) != manifest[field]
                    for field in ("release_id", "version")
                ):
                    raise ValueError(
                        "requested release is no longer the published compatible latest version"
                    )
                releases = self.settings.state_dir / "releases"
                releases.mkdir(parents=True, exist_ok=True)
                release = releases / uuid.uuid4().hex
                release.mkdir()
                archive = releases / f"{release.name}.zip"
                self.download(manifest, archive)
                self.state("validating")
                validate_zip(archive, release, manifest)
                self.installation(manifest, release)
                self.state("succeeded")
            except Exception as exc:
                # Never expose subprocess output, bearer tokens or connection strings.
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else f"{type(exc).__name__}: native upgrade failed; inspect host service logs"
                )
                if self.journal_path.exists():
                    message += "; recovery journal retained, operator recovery required"
                self.state("failed", message)
            if not self.journal_path.exists() and request_file.exists():
                request_file.unlink()
            return True


class Updater:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.validate()
        settings.queue_dir.mkdir(parents=True, exist_ok=True)
        settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_ready_heartbeat(settings.queue_dir, mode="docker", unit=None)
        self.opener = urllib.request.build_opener(NoRedirect())
        self.request: dict[str, Any] = {}
        self.journal_path = settings.state_dir / "transaction.json"
        self.override = settings.state_dir / "live-image.json"

    def state(self, phase: str, error: str | None = None) -> None:
        atomic_json(
            self.settings.queue_dir / "upgrade-state.json",
            {
                "phase": phase,
                "target_version": self.request.get("version"),
                "error": error,
                "request_id": self.request.get("request_id"),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )

    def http(self, suffix: str) -> Any:
        request = urllib.request.Request(
            self.settings.center_url.rstrip("/") + suffix,
            headers={"Authorization": f"Bearer {self.settings.client_token}"},
        )
        return self.opener.open(request, timeout=30)

    def manifest(self) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {"platform": "linux", "architecture": self.settings.architecture}
        )
        with self.http("/api/v1/releases/latest?" + query) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("release metadata response too large")
        token = json.loads(raw).get("manifest")
        if not isinstance(token, str):
            raise ValueError("no published compatible release")
        header = jwt.get_unverified_header(token)
        if (
            not isinstance(header, dict)
            or set(header) != {"alg", "kid", "typ"}
            or header.get("alg") != "EdDSA"
            or header.get("typ") != "JWT"
            or not isinstance(header.get("kid"), str)
        ):
            raise ValueError("invalid release signing-key header")
        keyring = json.loads(self.settings.trusted_keys_file.read_text(encoding="utf-8"))
        pem = keyring.get(header["kid"]) if isinstance(keyring, dict) else None
        if not isinstance(pem, str):
            raise ValueError("unrecognized signing key")
        public_key = serialization.load_pem_public_key(pem.encode("utf-8"))
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("release signing key is not Ed25519")
        manifest: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=["EdDSA"],
            audience="maitu-updater",
            issuer="maitu-license-center",
            options={"require": ["iat", "iss", "aud", "release_id", "version", "sha256", "size"]},
        )
        expected = {
            "product": "maitu",
            "protocol": 1,
            "package_type": "source",
            "platform": "linux",
            "architecture": self.settings.architecture,
        }
        if any(manifest.get(field) != value for field, value in expected.items()):
            raise ValueError("release is incompatible with this installation")
        if not re.fullmatch(r"[a-f0-9]{32}", str(manifest["release_id"])) or not re.fullmatch(
            r"[a-f0-9]{64}", str(manifest["sha256"])
        ):
            raise ValueError("invalid signed release identity or digest")
        if type(manifest["size"]) is not int or not 0 < manifest["size"] <= MAX_ARCHIVE:
            raise ValueError("invalid signed release size")
        if (
            str(Version(manifest["version"])) != manifest["version"]
            or manifest.get("filename") != f"maitu-{manifest['version']}.zip"
        ):
            raise ValueError("invalid signed release version or filename")
        return manifest

    def command(self, arguments: list[str], *, capture: bool = True, timeout: int = 1800) -> str:
        completed = subprocess.run(
            arguments,
            cwd=self.settings.project_dir,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            timeout=timeout,
        )
        return completed.stdout.strip() if capture else ""

    def compose(self, *arguments: str) -> str:
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(self.settings.project_dir),
            "-f",
            str(self.settings.compose_file),
        ]
        for path in self.settings.extra_compose_files:
            command += ["-f", str(path)]
        if self.override.exists():
            command += ["-f", str(self.override)]
        return self.command([*command, *arguments])

    def set_image(self, image: str) -> None:
        atomic_json(self.override, {"services": {self.settings.service: {"image": image}}})

    def download(self, manifest: dict[str, Any], archive: Path) -> None:
        digest = hashlib.sha256()
        size = 0
        with (
            self.http(f"/api/v1/releases/{manifest['release_id']}/download") as response,
            archive.open("xb") as stream,
        ):
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > manifest["size"]:
                    raise ValueError("release download exceeds declared size")
                digest.update(chunk)
                stream.write(chunk)
        if size != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
            raise ValueError("release archive checksum or size mismatch")

    def health(self, version: str, *, require_version: bool = True) -> None:
        deadline = time.monotonic() + self.settings.health_timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.settings.health_url, timeout=5) as response:
                    result = json.load(response)
                if result.get("ok") is True and (
                    not require_version or result.get("version") == version
                ):
                    return
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(2)
        raise RuntimeError("new service did not report healthy with the expected installed version")

    def backup(self, transaction: dict[str, Any]) -> None:
        destination = Path(transaction["backup"])
        with tarfile.open(destination, "x:gz") as archive:
            archive.add(self.settings.data_dir, arcname="data", recursive=True)
        if self.settings.postgres_dsn:
            dump = Path(transaction["pg_dump"])
            # Keep credentials out of argv and persistent logs.
            environment = {**os.environ, "PGDATABASE": self.settings.postgres_dsn}
            subprocess.run(
                ["pg_dump", "--format=custom", "--file", str(dump)],
                env=environment,
                check=True,
                capture_output=True,
                timeout=1800,
            )
        transaction["backup_complete"] = True
        atomic_json(self.journal_path, transaction)

    def recover(self, transaction: dict[str, Any]) -> None:
        """Restore both code and data after failure/interruption; preserve failed data."""
        self.compose("stop", self.settings.service)
        if transaction.get("backup_complete"):
            if self.settings.data_dir.exists():
                failed = self.settings.state_dir / f"failed-data-{uuid.uuid4().hex}"
                shutil.move(str(self.settings.data_dir), str(failed))
            recovery = self.settings.state_dir / f"recovery-{uuid.uuid4().hex}"
            recovery.mkdir()
            with tarfile.open(transaction["backup"], "r:gz") as archive:
                # Backup produced locally after quiescing the service, never from uploaded ZIP.
                archive.extractall(recovery, filter="data")
            shutil.move(str(recovery / "data"), str(self.settings.data_dir))
            if self.settings.postgres_dsn:
                environment = {**os.environ, "PGDATABASE": self.settings.postgres_dsn}
                subprocess.run(
                    [
                        "pg_restore",
                        "--clean",
                        "--if-exists",
                        "--exit-on-error",
                        "--dbname",
                        "",
                        transaction["pg_dump"],
                    ],
                    env=environment,
                    check=True,
                    capture_output=True,
                    timeout=1800,
                )
        self.set_image(transaction["old_image"])
        self.compose(
            "up", "-d", "--no-build", "--force-recreate", "--no-deps", self.settings.service
        )
        self.health(
            transaction["old_version"],
            require_version=transaction.get("old_health_has_version", True),
        )
        container = self.compose("ps", "-q", self.settings.service)
        if (
            self.command(["docker", "inspect", "--format", "{{.Image}}", container])
            != transaction["old_image"]
        ):
            raise RuntimeError("recovery did not restore the previous immutable image")
        self.journal_path.unlink()

    def installation(self, manifest: dict[str, Any], release: Path) -> None:
        config = json.loads(self.compose("config", "--format", "json"))
        service = config["services"][self.settings.service]
        mounts = service.get("volumes", [])
        data_mount = next((v for v in mounts if v.get("target") == "/data/.octop"), None)
        if (
            not data_mount
            or data_mount.get("type") != "bind"
            or Path(data_mount["source"]).resolve() != self.settings.data_dir
        ):
            raise ValueError("configured data path must match the existing /data/.octop bind mount")
        if self.settings.postgres_dsn and (
            not shutil.which("pg_dump") or not shutil.which("pg_restore")
        ):
            raise ValueError("PostgreSQL backup and restore tools are required before upgrade")
        container = self.compose("ps", "-q", self.settings.service)
        if not container or "\n" in container:
            raise ValueError("exactly one running service container is required")
        driver = self.command(
            [
                "docker",
                "exec",
                container,
                "python",
                "-c",
                "from octop.config import load_config; "
                "from octop.infra.utils.paths import PathLayout; "
                "from octop.infra.utils.env_file import apply_env_file, env_file_path; "
                "p=PathLayout.from_env(); apply_env_file(env_file_path(p.root)); "
                "print(load_config(p.config).database.driver)",
            ]
        )
        if driver not in {"sqlite", "postgresql"}:
            raise ValueError("running database driver could not be determined safely")
        if driver == "postgresql" and not self.settings.postgres_dsn:
            raise ValueError("PostgreSQL installations require host backup credentials")
        old_image = self.command(["docker", "inspect", "--format", "{{.Image}}", container])
        old_version = str(
            Version(
                tomllib.loads((self.settings.project_dir / "pyproject.toml").read_text("utf-8"))[
                    "project"
                ]["version"]
            )
        )
        try:
            with urllib.request.urlopen(self.settings.health_url, timeout=5) as response:
                old_health = json.load(response)
            if old_health.get("version"):
                old_version = str(Version(old_health["version"]))
        except (OSError, ValueError, urllib.error.URLError):
            raise ValueError("existing service health must be reachable before upgrade") from None
        if Version(manifest["version"]) <= Version(old_version):
            raise ValueError("target version is not newer than the running installation")
        image = f"maitu-update:{manifest['release_id']}"
        self.state("building")
        self.command(
            [
                "docker",
                "build",
                "--platform",
                f"linux/{self.settings.architecture}",
                "-f",
                str(release / "docker" / "Dockerfile"),
                "-t",
                image,
                str(release),
            ],
            timeout=3600,
        )
        transaction = {
            "request": self.request,
            "old_image": old_image,
            "old_version": old_version,
            "backup": str(self.settings.state_dir / f"backup-{self.request['request_id']}.tar.gz"),
            "pg_dump": str(self.settings.state_dir / f"backup-{self.request['request_id']}.pgdump"),
            "backup_complete": False,
            "old_health_has_version": bool(old_health.get("version")),
        }
        atomic_json(self.journal_path, transaction)
        try:
            self.state("stopping")
            self.compose("stop", self.settings.service)
            self.state("backing_up")
            self.backup(transaction)
            self.state("installing")
            self.set_image(image)
            self.state("restarting")
            self.compose(
                "up", "-d", "--no-build", "--force-recreate", "--no-deps", self.settings.service
            )
            self.state("verifying")
            self.health(manifest["version"])
        except Exception:
            self.recover(transaction)
            raise
        self.journal_path.unlink()

    def process(self) -> bool:
        with exclusive_lock(self.settings.state_dir / "updater.lock"):
            write_ready_heartbeat(self.settings.queue_dir, mode="docker", unit=None)
            if self.journal_path.exists():
                transaction = load_json(self.journal_path)
                self.request = transaction["request"]
                try:
                    self.recover(transaction)
                    self.state(
                        "failed", "Interrupted upgrade restored the previous version and data"
                    )
                except Exception as exc:
                    self.state(
                        "failed",
                        f"Interrupted upgrade recovery failed: {type(exc).__name__}; operator recovery required",
                    )
                    return True
                request_file = self.settings.queue_dir / "upgrade-request.json"
                if request_file.exists() and load_json(request_file).get(
                    "request_id"
                ) == self.request.get("request_id"):
                    request_file.unlink()
                return True
            request_file = self.settings.queue_dir / "upgrade-request.json"
            if not request_file.exists():
                return False
            try:
                self.request = load_json(request_file)
                if not re.fullmatch(r"[a-f0-9-]{32,36}", str(self.request.get("request_id", ""))):
                    raise ValueError("invalid upgrade request ID")
                self.state("downloading")
                manifest = self.manifest()
                if any(
                    self.request.get(field) != manifest[field]
                    for field in ("release_id", "version")
                ):
                    raise ValueError(
                        "requested release is no longer the published compatible latest version"
                    )
                release = self.settings.state_dir / "releases" / uuid.uuid4().hex
                release.mkdir(parents=True)
                archive = release.parent / f"{release.name}.zip"
                self.download(manifest, archive)
                self.state("validating")
                validate_zip(archive, release, manifest)
                self.installation(manifest, release)
                self.state("succeeded")
            except Exception as exc:
                # Never expose subprocess stderr, connection strings or bearer credentials.
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else f"{type(exc).__name__}: upgrade failed; inspect host service logs"
                )
                if self.journal_path.exists():
                    message += "; recovery journal retained, operator recovery required"
                self.state("failed", message)
            if not self.journal_path.exists() and request_file.exists():
                request_file.unlink()
            return True


def settings_from_environment() -> Settings:
    def required(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ValueError(f"missing {name}")
        return value

    project = Path(required("MAITU_UPDATE_PROJECT_DIR")).resolve()
    return Settings(
        center_url=required("MAITU_UPDATE_CENTER_URL"),
        client_token=required("MAITU_UPDATE_CLIENT_TOKEN"),
        trusted_keys_file=Path(required("MAITU_UPDATE_TRUSTED_KEYS_FILE")).resolve(),
        queue_dir=Path(required("MAITU_UPDATE_QUEUE_DIR")).resolve(),
        project_dir=project,
        data_dir=Path(required("MAITU_UPDATE_DATA_DIR")).resolve(),
        state_dir=Path(required("MAITU_UPDATE_STATE_DIR")).resolve(),
        health_url=required("MAITU_UPDATE_HEALTH_URL"),
        compose_file=Path(
            os.environ.get("MAITU_UPDATE_COMPOSE_FILE", str(project / "docker/docker-compose.yml"))
        ).resolve(),
        extra_compose_files=tuple(
            Path(path).resolve()
            for path in os.environ.get("MAITU_UPDATE_COMPOSE_OVERLAYS", "").split(os.pathsep)
            if path
        ),
        service=os.environ.get("MAITU_UPDATE_COMPOSE_SERVICE", "maitu"),
        architecture=os.environ.get(
            "MAITU_UPDATE_ARCHITECTURE",
            {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64"}.get(platform.machine(), ""),
        ),
        allow_loopback=os.environ.get("MAITU_UPDATE_ALLOW_LOOPBACK", "").lower() in {"1", "true"},
        postgres_dsn=os.environ.get("MAITU_UPDATE_POSTGRES_DSN") or None,
    )


def native_settings_from_environment() -> NativeSettings:
    """Native systemd updater settings from the host environment."""

    def required(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ValueError(f"missing {name}")
        return value

    extras_raw = os.environ.get("MAITU_UPDATE_NATIVE_EXTRAS")
    if extras_raw is None or not extras_raw.strip():
        extras: tuple[str, ...] = ("office", "knowledge-ocr")
    else:
        extras = tuple(
            dict.fromkeys(part.strip() for part in extras_raw.split(",") if part.strip())
        )
    updater_root = Path(
        os.environ.get("MAITU_UPDATE_UPDATER_ROOT", str(Path(__file__).resolve().parent))
    ).resolve()
    return NativeSettings(
        center_url=required("MAITU_UPDATE_CENTER_URL"),
        client_token=required("MAITU_UPDATE_CLIENT_TOKEN"),
        trusted_keys_file=Path(required("MAITU_UPDATE_TRUSTED_KEYS_FILE")).resolve(),
        queue_dir=Path(required("MAITU_UPDATE_QUEUE_DIR")).resolve(),
        state_dir=Path(required("MAITU_UPDATE_STATE_DIR")).resolve(),
        data_dir=Path(required("MAITU_UPDATE_DATA_DIR")).resolve(),
        health_url=required("MAITU_UPDATE_HEALTH_URL"),
        release_env_file=Path(required("MAITU_UPDATE_RELEASE_ENV_FILE")).resolve(),
        systemd_unit=required("MAITU_UPDATE_SYSTEMD_UNIT"),
        updater_root=updater_root,
        architecture=os.environ.get(
            "MAITU_UPDATE_ARCHITECTURE",
            {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64"}.get(platform.machine(), ""),
        ),
        allow_loopback=os.environ.get("MAITU_UPDATE_ALLOW_LOOPBACK", "").lower() in {"1", "true"},
        extras=extras,
        postgres_dsn=os.environ.get("MAITU_UPDATE_POSTGRES_DSN") or None,
        uv=os.environ.get("MAITU_UPDATE_NATIVE_UV", "uv").strip() or "uv",
        npm=os.environ.get("MAITU_UPDATE_NATIVE_NPM", "npm").strip() or "npm",
    )


def prepare_native(
    archive: Path,
    destination: Path,
    environment: Path,
    home: Path,
    sha256: str,
    extras: list[str],
) -> None:
    """Prepare a new source directory; never start a service or replace user state."""
    archive = archive.resolve(strict=True)
    environment = environment.resolve(strict=True)
    home = home.resolve(strict=True)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        raise ValueError("a SHA-256 digest from the trusted release publisher is required")
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError("source archive exceeds download limit")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != sha256.lower():
            raise ValueError("source archive checksum mismatch")
    if destination.exists() or destination.is_symlink():
        raise ValueError(
            "destination must be a new directory; existing source is never overwritten"
        )
    destination = destination.resolve()
    for protected in (home, environment):
        if (
            destination == protected
            or destination.is_relative_to(protected)
            or protected.is_relative_to(destination)
        ):
            raise ValueError(
                "new source must be separate from OCTOP_HOME and the existing environment"
            )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not home.is_dir() or not (environment / "pyvenv.cfg").is_file() or not python.is_file():
        raise ValueError("existing OCTOP_HOME and Python virtual environment are required")
    if not shutil.which("uv") or not shutil.which("npm"):
        raise ValueError("uv and Node.js/npm are required")
    with zipfile.ZipFile(archive) as source:
        if source.getinfo("maitu-release.json").file_size > 1024 * 1024:
            raise ValueError("release metadata exceeds limit")
        manifest = json.loads(source.read("maitu-release.json"))
    if (
        manifest.get("product") != "maitu"
        or manifest.get("package_type") != "source"
        or manifest.get("platform") != "linux"
        or manifest.get("architecture")
        != {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    ):
        raise ValueError("native source release is incompatible with this host")
    destination.mkdir(parents=True)
    validate_zip(archive, destination, manifest)
    project = tomllib.loads((destination / "pyproject.toml").read_text("utf-8"))
    selected = list(dict.fromkeys(["office", "knowledge-ocr", *extras]))
    available = project["project"].get("optional-dependencies", {})
    if any(extra not in available for extra in selected):
        raise ValueError("requested extra is not available in this release")
    # Inexact sync retains previously installed optional packages; never recreate .venv.
    arguments = ["uv", "sync", "--inexact", "--no-dev", "--frozen", "--python", str(python)]
    for extra in selected:
        arguments += ["--extra", extra]
    env = {
        **os.environ,
        "OCTOP_HOME": str(home),
        "UV_PROJECT_ENVIRONMENT": str(environment),
    }
    subprocess.run(arguments, cwd=destination, env=env, check=True, timeout=1800)
    npm = shutil.which("npm")
    assert npm is not None
    for arguments in ([npm, "ci"], [npm, "run", "build"]):
        subprocess.run(arguments, cwd=destination / "dashboard", env=env, check=True, timeout=1800)
    print(f"Prepared native source: {destination}")
    print(f"Existing environment retained: {environment}")
    print(f"OCTOP_HOME unchanged: {home}")
    print(
        "Service remains stopped. Update its working directory/ExecStart manually, then check health."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one queued request")
    parser.add_argument(
        "--native",
        action="store_true",
        help="Run the native source/systemd updater (also selected by MAITU_UPDATE_MODE=native)",
    )
    parser.add_argument(
        "--native-prepare",
        action="store_true",
        help="Prepare a trusted source ZIP for a stopped native service",
    )
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--environment", type=Path, help="Existing service virtual environment")
    parser.add_argument("--home", type=Path, help="Existing absolute OCTOP_HOME")
    parser.add_argument("--sha256", help="Digest obtained independently from the trusted publisher")
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument(
        "--service-stopped",
        action="store_true",
        help="Confirm the service is stopped and data backed up",
    )
    args = parser.parse_args()
    if args.native_prepare:
        if not all(
            (
                args.archive,
                args.destination,
                args.environment,
                args.home,
                args.sha256,
                args.service_stopped,
            )
        ):
            parser.error(
                "native preparation requires --archive, --destination, --environment, --home, --sha256 and --service-stopped"
            )
        if not all(
            path.is_absolute()
            for path in (args.archive, args.destination, args.environment, args.home)
        ):
            parser.error("native paths must be absolute")
        prepare_native(
            args.archive, args.destination, args.environment, args.home, args.sha256, args.extra
        )
        return
    native_mode = args.native or os.environ.get("MAITU_UPDATE_MODE", "").strip().lower() == "native"
    updater: Updater | NativeUpdater
    if native_mode:
        updater = NativeUpdater(native_settings_from_environment())
    else:
        updater = Updater(settings_from_environment())
    while True:
        updater.process()
        if args.once:
            break
        time.sleep(5)


if __name__ == "__main__":
    main()
