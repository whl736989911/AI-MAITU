"""Standard-library operations for local, operator-controlled Docker deployments."""

from __future__ import annotations

import getpass
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MARKER = "# Managed by maitu deploy.sh"


class DeploymentError(RuntimeError):
    """An actionable deployment failure; callers must preserve existing data."""


def run(
    args: list[str],
    *,
    capture: bool = False,
    input_text: str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if env is not None:
        environment.update(env)
    try:
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=capture,
            env=environment,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError(
            f"执行 {Path(args[0]).name} 失败：{type(exc).__name__}。请检查程序和服务状态。"
        ) from exc
    if check and result.returncode:
        # Captured output can contain credentials from existing deployment configuration.
        # Do not echo it or secret-bearing argv. The operator can inspect the service locally.
        raise DeploymentError(
            f"{Path(args[0]).name} 执行失败（退出码 {result.returncode}），原数据保留。请检查该服务的本地诊断。"
        )
    return result


def ask(label: str, default: str | None = None, *, secret: bool = False) -> str:
    suffix = f" [{default}]" if default is not None and not secret else ""
    try:
        if secret:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                answer = getpass.getpass(f"{label}：").strip()
        else:
            answer = input(f"{label}{suffix}：").strip()
    except getpass.GetPassWarning as exc:
        raise DeploymentError(
            "秘密输入需要可隐藏输入的终端，请在 SSH 交互终端运行，不要通过日志或管道提交密码。"
        ) from exc
    except (EOFError, KeyboardInterrupt) as exc:
        raise DeploymentError("输入已取消；不会自动采用默认值继续部署。") from exc
    if answer:
        return answer
    if default is not None:
        return default
    raise DeploymentError(f"{label}不能为空。")


def confirm(label: str, *, default: bool = False) -> bool:
    answer = ask(f"{label}（{'Y/n' if default else 'y/N'}）", "y" if default else "n")
    if answer.lower() not in {"y", "yes", "n", "no"}:
        raise DeploymentError("请输入 y 或 n。")
    return answer.lower() in {"y", "yes"}


def require_host() -> None:
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise DeploymentError("部署须在 Ubuntu 主机以 sudo 运行；--help 和只读检查不需要安装服务。")
    release = Path("/etc/os-release")
    values: dict[str, str] = {}
    if release.is_file():
        for line in release.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value.strip('"')
    if values.get("ID") != "ubuntu" or values.get("VERSION_ID") not in {"24.04", "22.04"}:
        raise DeploymentError(
            "当前自动部署支持 Ubuntu 24.04／22.04；其他系统请使用现有手工部署说明。"
        )


def ensure_tools(packages: list[str]) -> None:
    mapping = {
        "docker": "docker.io",
        "nginx": "nginx",
        "openssl": "openssl",
        "gpg": "gnupg",
        "curl": "curl",
    }
    missing = [mapping.get(name, name) for name in packages if shutil.which(name) is None]
    if "docker" in packages:
        version = (
            run(["docker", "compose", "version"], capture=True, check=False)
            if shutil.which("docker")
            else None
        )
        if version is None or version.returncode:
            missing.extend(["docker.io", "docker-compose-v2"])
        builder = (
            run(["docker", "buildx", "version"], capture=True, check=False)
            if shutil.which("docker")
            else None
        )
        if builder is None or builder.returncode:
            missing.append("docker-buildx")
    if missing:
        selected = list(dict.fromkeys(missing))
        if not confirm("需要安装系统依赖：" + ", ".join(selected)):
            raise DeploymentError("未安装依赖；现有部署未修改。")
        run(["apt-get", "update"], timeout=600)
        run(
            ["apt-get", "install", "-y", *selected],
            env={"DEBIAN_FRONTEND": "noninteractive"},
            timeout=900,
        )
    if "docker" in packages:
        run(["docker", "compose", "version"], capture=True)
        result = run(["docker", "info"], capture=True, check=False, timeout=30)
        if result.returncode:
            if not confirm("Docker 服务尚不可用，是否启用并启动 Docker？"):
                raise DeploymentError("Docker 不可用；请先启动 Docker。")
            run(["systemctl", "enable", "--now", "docker"])
        endpoint = os.environ.get("DOCKER_HOST")
        if not endpoint:
            endpoint = run(
                ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                capture=True,
            ).stdout.strip()
        if not endpoint.startswith("unix://"):
            raise DeploymentError(
                "本地部署向导需要当前主机的 Docker Unix socket；远程 Docker 会使数据挂载和本地反代错位，拒绝继续。"
            )


def _safe_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise DeploymentError("部署路径不能包含父目录遍历。")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise DeploymentError(f"拒绝通过符号链接写入：{component}")
    return path


def ensure_directory(
    path: Path, *, mode: int = 0o700, uid: int | None = None, gid: int | None = None
) -> None:
    path = _safe_path(path)
    if path == Path(path.anchor) or path.as_posix() in {
        "/etc",
        "/var",
        "/srv",
        "/home",
        "/usr",
        "/opt",
    }:
        raise DeploymentError("请选择独立的项目目录，不能改变系统根目录的权限。")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise DeploymentError(f"不是目录：{path}")
    path.chmod(mode)
    if uid is not None and hasattr(os, "chown"):
        os.chown(path, uid, -1 if gid is None else gid)


def atomic_write(path: Path, content: str, *, mode: int = 0o600, backup: bool = True) -> None:
    path = _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    if path.exists():
        if not path.is_file():
            raise DeploymentError(f"拒绝覆盖非普通配置文件：{path}")
        if path.read_bytes() == encoded:
            return
        if backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            saved = path.with_name(path.name + ".backup-" + stamp + "-" + secrets.token_hex(4))
            with saved.open("xb") as stream:
                stream.write(path.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            saved.chmod(mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def configuration_transaction(paths: list[Path]) -> Iterator[None]:
    """Restore the previous configuration when validation fails before startup."""
    previous: dict[Path, tuple[str, int] | None] = {}
    for candidate in paths:
        path = _safe_path(candidate)
        previous[path] = (
            (path.read_text(encoding="utf-8"), path.stat().st_mode & 0o777)
            if path.exists()
            else None
        )
    try:
        yield
    except BaseException:
        for path, saved in previous.items():
            if saved is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, saved[0], mode=saved[1], backup=False)
        raise


def load_manifest(config_dir: Path, role: str) -> dict[str, Any] | None:
    path = _safe_path(config_dir / f"{role}.json")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise DeploymentError(f"无法读取部署记录 {path}，不会将损坏记录当作全新部署。") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("role") != role:
        raise DeploymentError(f"不兼容的部署记录：{path}")
    return data


def save_manifest(config_dir: Path, role: str, data: dict[str, Any]) -> None:
    ensure_directory(config_dir)
    if data.get("version") != 1 or data.get("role") != role:
        raise DeploymentError("部署记录必须标明 version=1 和正确 role。")
    atomic_write(config_dir / f"{role}.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def compose(
    config: dict[str, Any],
    *args: str,
    capture: bool = False,
    input_text: str | None = None,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    files = config.get("compose_files")
    if not isinstance(files, list) or not files or any(not isinstance(item, str) for item in files):
        raise DeploymentError("部署记录缺少 Compose 文件。")
    command = [
        "docker",
        "compose",
        "--project-name",
        str(config["project_name"]),
        "--env-file",
        str(config["env_file"]),
    ]
    for file in files:
        command.extend(["-f", file])
    command.extend(args)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OCTOP_", "LICENSE_CENTER_"))
    }
    try:
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=capture,
            check=False,
            env=environment,
            timeout=timeout,
            cwd=config.get("source_root", str(ROOT)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError(f"Compose 执行失败：{type(exc).__name__}。原数据保留。") from exc
    if check and result.returncode:
        raise DeploymentError(
            f"Compose 操作失败（退出码 {result.returncode}）；请使用 check 检查，不要清空数据重装。"
        )
    return result


def check_url(value: str) -> str:
    if not value or value != value.strip():
        raise DeploymentError("中心地址不能包含首尾空白。")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise DeploymentError("中心地址或端口格式错误。") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or "?" in value
        or "#" in value
        or parsed.path not in {"", "/"}
        or any(char.isspace() for char in value)
    ):
        raise DeploymentError("请输入 HTTPS 根地址；不能包含账号、查询参数或 /api 等路径。")
    if port is not None and not 1 <= port <= 65535:
        raise DeploymentError("中心端口必须位于 1 到 65535。")
    hostname = parsed.hostname
    if ":" in hostname:
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError as exc:
            raise DeploymentError("中心 IPv6 地址无效。") from exc
        hostname = "[" + hostname.lower() + "]"
    else:
        try:
            hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise DeploymentError("中心主机名无效。") from exc
        if len(hostname) > 253 or any(
            not label
            or len(label) > 63
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
            for label in hostname.rstrip(".").split(".")
        ):
            raise DeploymentError("中心主机名无效。")
    return "https://" + hostname + (f":{port}" if port is not None else "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def wait_http(url: str, *, ca_file: Path | None = None, timeout: int = 60) -> dict[str, Any]:
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), _NoRedirect()
    )
    deadline = time.monotonic() + timeout
    last_error = "not_ready"
    while True:
        try:
            with opener.open(url, timeout=min(5, max(1, timeout))) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise DeploymentError("健康检查响应过大。")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise DeploymentError("健康接口未返回 JSON 对象；请检查反向代理路径。")
            return data
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = type(exc).__name__
        if time.monotonic() >= deadline:
            raise DeploymentError(
                f"健康检查失败：{last_error}。请检查服务、TLS 和实际访问网络；未跳过证书验证。"
            )
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def env_text(values: dict[str, str]) -> str:
    lines = [MARKER]
    for key, value in values.items():
        if not key.replace("_", "").isalnum() or not key[0].isalpha():
            raise DeploymentError("环境变量名无效。")
        if "\n" in value or "\r" in value or "\x00" in value:
            raise DeploymentError(f"环境项 {key} 含非法换行。")
        # Compose single quotes prevent interpolation of $ and preserve literal paths.
        lines.append(key + "='" + value.replace("'", "\\'") + "'")
    return "\n".join(lines) + "\n"


def check_time() -> None:
    result = run(
        ["timedatectl", "show", "--property=NTPSynchronized", "--value"], capture=True, check=False
    )
    if result.returncode or result.stdout.strip() != "yes":
        if not confirm("系统未确认时间同步，是否启用 NTP？"):
            raise DeploymentError("请先修正系统时间，再初始化 MFA 或激活。")
        run(["timedatectl", "set-ntp", "true"])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = run(
                ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
                capture=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "yes":
                return
            time.sleep(1)
        raise DeploymentError(
            "已启用 NTP，但时间尚未同步；请排查网络和时间服务后重试，未继续初始化 MFA 或激活。"
        )


def validate_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise DeploymentError("端口必须是整数。") from exc
    if not 1 <= port <= 65535:
        raise DeploymentError("端口必须位于 1 到 65535。")
    return port


def port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((host, port))
        except OSError:
            return False
    return True


@contextmanager
def deployment_lock(config_dir: Path) -> Iterator[None]:
    """Do not let two setup terminals replace the same deployment configuration."""
    import fcntl

    ensure_directory(config_dir)
    path = _safe_path(config_dir / ".deploy.lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentError(
                "另一个部署向导正在使用此配置目录；请等待它完成，不要并发修改。"
            ) from exc
        yield
    finally:
        os.close(descriptor)
