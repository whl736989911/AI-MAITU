"""Interactive, repeat-safe customer platform deployment."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import bundle
from .common import (
    ROOT,
    DeploymentError,
    ask,
    atomic_write,
    check_time,
    check_url,
    compose,
    configuration_transaction,
    confirm,
    ensure_directory,
    ensure_tools,
    env_text,
    load_manifest,
    port_available,
    require_host,
    run,
    save_manifest,
    validate_port,
    wait_http,
)
from .probe import inspect_platform


def _safe_existing_file(path: Path, label: str) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise DeploymentError(f"{label}路径不能包含父目录遍历。")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise DeploymentError(f"{label}路径包含符号链接：{component}")
    if not path.is_file():
        raise DeploymentError(f"{label}文件不存在：{path}")
    return path


def _safe_directory(path: Path, label: str) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise DeploymentError(f"{label}路径不能包含父目录遍历。")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise DeploymentError(f"{label}路径包含符号链接：{component}")
    if path in {
        Path(path.anchor),
        Path("/etc"),
        Path("/var"),
        Path("/home"),
        Path("/usr"),
        Path("/opt"),
    }:
        raise DeploymentError(f"拒绝将危险系统路径用作{label}：{path}")
    if path.exists() and not path.is_dir():
        raise DeploymentError(f"{label}不是目录：{path}")
    return path


def _service_state_directory(path: Path, label: str) -> Path:
    path = _safe_directory(path, label)
    if os.name != "posix":
        ensure_directory(path, mode=0o700)
        return path
    if not path.exists():
        ensure_directory(path, mode=0o700, uid=1000, gid=1000)
        return path

    entries = [path, *path.rglob("*")]
    for entry in entries:
        if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
            raise DeploymentError(f"{label}目录包含链接或特殊文件，拒绝修改：{entry}")
    needs_adoption = any(entry.stat().st_uid != 1000 for entry in entries)
    if needs_adoption:
        if not confirm(
            f"{label}已有内容；保留所有内容并将目录树所有权调整为容器 UID/GID 1000？",
            default=False,
        ):
            raise DeploymentError(f"{label}现有内容不属于 UID 1000；未更改数据。")
        for entry in reversed(entries):
            os.chown(entry, 1000, 1000)
            if entry.is_dir():
                entry.chmod(0o700)
            else:
                entry.chmod((entry.stat().st_mode & 0o777) | 0o600)
    else:
        path.chmod(0o700)
    return path


def _require_disjoint_state_paths(paths: tuple[Path, ...], labels: tuple[str, ...]) -> None:
    for index, first in enumerate(paths):
        for other_index in range(index + 1, len(paths)):
            second = paths[other_index]
            if first == second or first.is_relative_to(second) or second.is_relative_to(first):
                raise DeploymentError(
                    f"{labels[index]} 与 {labels[other_index]} 不能相同或互相嵌套。"
                )


def _compose_status(config: dict[str, Any]) -> dict[str, Any]:
    result = compose(
        config,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        "octop",
        "maitu",
        "license",
        "status",
        capture=True,
    )
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise DeploymentError("无法读取容器内授权状态；没有输出秘密或更改实例身份。") from exc
    if not isinstance(value, dict) or not isinstance(value.get("state"), str):
        raise DeploymentError("容器内授权状态格式无效；原数据保留。")
    return value


def _run_license(config: dict[str, Any], operation: str, *, code: str | None = None) -> bool:
    args = [
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        "octop",
        "maitu",
        "license",
        operation,
    ]
    result = compose(
        config,
        *args,
        capture=True,
        input_text=(code + "\n") if code is not None else None,
        check=False,
    )
    return result.returncode == 0


def _public_yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _trust_bundle(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    bundle_data = bundle.load_bundle(path)
    fingerprints = bundle.key_fingerprints(bundle_data)
    return bundle_data, fingerprints


def _confirm_bundle(bundle_data: dict[str, Any], fingerprints: dict[str, str]) -> None:
    print(f"授权中心：{bundle_data['server_url']}")
    for kid, fingerprint in sorted(fingerprints.items()):
        print(f"  公钥 {kid}: SHA256:{fingerprint}")
    if not confirm("确认信任以上地址与公钥指纹？", default=False):
        raise DeploymentError("未确认授权中心信任信息，未导入连接包。")


def _local_docker_socket() -> Path:
    endpoint = os.environ.get("DOCKER_HOST")
    if not endpoint:
        endpoint = run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture=True,
        ).stdout.strip()
    parsed_endpoint = urllib.parse.urlsplit(endpoint)
    if parsed_endpoint.scheme != "unix" or parsed_endpoint.netloc:
        raise DeploymentError(
            "平台部署需要当前主机的 Docker Unix socket；远程 Docker 会使数据挂载错位，拒绝继续。"
        )
    socket_path = Path(urllib.parse.unquote(parsed_endpoint.path))
    if not socket_path.is_absolute() or not socket_path.is_socket():
        raise DeploymentError(
            f"Docker Unix socket 不存在或不是 socket：{socket_path}。"
            "请启动本机 Docker daemon 并确认当前 Docker context 使用本地 Unix socket。"
        )
    return socket_path


def _data_mount(
    source_root: Path, env_file: Path, files: list[Path], project_name: str
) -> dict[str, str]:
    selected = [
        str(path)
        for path in files
        if path.name
        not in {
            "docker-compose.licensing.yml",
            "docker-compose.managed.yml",
            "platform.compose.override.yml",
        }
    ]
    config = {
        "source_root": str(source_root),
        "env_file": str(env_file),
        "compose_files": selected,
        "project_name": project_name,
    }
    result = compose(config, "config", "--format", "json", capture=True)
    try:
        parsed = json.loads(result.stdout)
        service = parsed["services"]["maitu"]
        mounts = [
            item for item in service.get("volumes", []) if item.get("target") == "/data/.octop"
        ]
        ports = service.get("ports", [])
    except (ValueError, TypeError, KeyError) as exc:
        raise DeploymentError(
            "无法从原 Compose 配置确认平台服务、端口和 /data/.octop 数据挂载。"
        ) from exc
    if len(mounts) != 1 or mounts[0].get("type") not in {"bind", "volume"}:
        raise DeploymentError(
            "原 Compose 必须为 /data/.octop 配置且仅配置一个 bind/volume 数据挂载。"
        )
    source = mounts[0].get("source")
    if not isinstance(source, str) or not source:
        raise DeploymentError("原 Compose 数据挂载缺少可识别来源；拒绝创建空数据。")
    mappings = [
        item for item in ports if isinstance(item, dict) and item.get("published") is not None
    ]
    if len(mappings) != 1:
        raise DeploymentError("原 Compose 必须为平台服务配置且仅配置一个可识别的公开端口。")
    try:
        target_port = int(mappings[0]["target"])
        published_port = int(mappings[0]["published"])
        validate_port(str(target_port))
        validate_port(str(published_port))
    except (ValueError, TypeError, KeyError, DeploymentError) as exc:
        raise DeploymentError("原 Compose 平台端口映射无效；拒绝猜测端口。") from exc
    if mounts[0]["type"] == "volume" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", source):
        raise DeploymentError("原 Compose named volume 名称无效；拒绝执行 Docker volume 操作。")
    mount = {
        "type": mounts[0]["type"],
        "source": source,
        "target_port": str(target_port),
        "published_port": str(published_port),
    }
    _verify_existing_storage(config, mount)
    return mount


def _verify_existing_storage(config: dict[str, Any], mount: dict[str, str]) -> None:
    result = compose(config, "ps", "--all", "-q", "maitu", capture=True, check=False)
    for container_id in result.stdout.split():
        result = run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", container_id], capture=True
        )
        try:
            actual = [
                item
                for item in json.loads(result.stdout)
                if item.get("Destination") == "/data/.octop"
            ]
            if len(actual) != 1:
                raise ValueError("missing data mount")
            source = actual[0].get("Name") if actual[0]["Type"] == "volume" else actual[0]["Source"]
            if actual[0]["Type"] != mount["type"] or source != mount["source"]:
                raise ValueError("data mount mismatch")
        except (ValueError, TypeError, KeyError) as exc:
            raise DeploymentError(
                "原容器数据挂载与所选 Compose 配置不一致；拒绝启动，以免误用空目录。请提供原环境文件和项目名。"
            ) from exc


def _build_platform_override(
    path: Path,
    *,
    host: str,
    published_port: int,
    target_port: int,
    server_url: str,
    license_keys_path: Path,
    release_keys_path: Path,
    license_state_dir: Path,
    update_queue_dir: Path,
    ca_path: Path | None,
    time_zone: str = "Asia/Shanghai",
) -> None:
    host_port = f"[{host}]:{published_port}" if ":" in host else f"{host}:{published_port}"
    lines = [
        "# Managed by maitu deploy.sh",
        "services:",
        "  maitu:",
        "    ports: !override",
        f"      - {_public_yaml(f'{host_port}:{target_port}')} ",
        "    volumes:",
        "      - type: bind",
        f"        source: {_public_yaml(str(license_keys_path))}",
        "        target: /run/maitu-trust/license-trusted-keys.json",
        "        read_only: true",
        "      - type: bind",
        f"        source: {_public_yaml(str(release_keys_path))}",
        "        target: /run/maitu-trust/release-trusted-keys.json",
        "        read_only: true",
        "      - type: bind",
        f"        source: {_public_yaml(str(license_state_dir))}",
        "        target: /var/lib/maitu-license",
        "      - type: bind",
        f"        source: {_public_yaml(str(update_queue_dir))}",
        "        target: /var/lib/maitu-update-queue",
    ]
    if ca_path is not None:
        lines.extend(
            [
                "      - type: bind",
                f"        source: {_public_yaml(str(ca_path))}",
                "        target: /run/maitu-trust/root-ca.pem",
                "        read_only: true",
            ]
        )
    lines.extend(
        [
            "    environment:",
            "      MAITU_LICENSE_TRUSTED_KEYS_FILE: /run/maitu-trust/license-trusted-keys.json",
            "      MAITU_LICENSE_STATE_DIR: /var/lib/maitu-license",
            f"      MAITU_LICENSE_CENTER_URL: {_public_yaml(server_url)}",
            "      MAITU_UPDATE_TRUSTED_KEYS_FILE: /run/maitu-trust/release-trusted-keys.json",
            "      MAITU_UPDATE_QUEUE_DIR: /var/lib/maitu-update-queue",
            f"      MAITU_UPDATE_CENTER_URL: {_public_yaml(server_url)}",
            '      MAITU_UPDATE_CLIENT_TOKEN: "${MAITU_CENTER_CLIENT_TOKEN:-}"',
            f"      OCTOP_DEFAULT_TIMEZONE: {_public_yaml(time_zone)}",
        ]
    )
    if ca_path is not None:
        lines.extend(
            [
                "      SSL_CERT_FILE: /run/maitu-trust/root-ca.pem",
                "      REQUESTS_CA_BUNDLE: /run/maitu-trust/root-ca.pem",
            ]
        )
    atomic_write(path, "\n".join(lines) + "\n", mode=0o600)


def _verify_runtime_mounts(
    config: dict[str, Any],
    data_mount: dict[str, str],
    protected_mounts: dict[str, tuple[str, bool]],
) -> None:
    result = compose(config, "config", "--format", "json", capture=True)
    try:
        service = json.loads(result.stdout)["services"]["maitu"]
        volumes = service.get("volumes", [])
        data_mounts = [item for item in volumes if item.get("target") == "/data/.octop"]
    except (ValueError, TypeError, KeyError) as exc:
        raise DeploymentError("无法确认最终平台 Compose 的数据挂载。") from exc
    if (
        len(data_mounts) != 1
        or data_mounts[0].get("type") != data_mount["type"]
        or data_mounts[0].get("source") != data_mount["source"]
    ):
        raise DeploymentError(
            "最终平台 Compose 的 /data/.octop 挂载与原部署数据不一致；拒绝切换数据或实例身份。"
        )
    for target, (source, read_only) in protected_mounts.items():
        matches = [item for item in volumes if item.get("target") == target]
        if (
            len(matches) != 1
            or matches[0].get("type") != "bind"
            or matches[0].get("source") != source
            or bool(matches[0].get("read_only")) != read_only
        ):
            raise DeploymentError(f"最终平台 Compose 的受保护挂载不一致：{target}。")


def _owns_port(config: dict[str, Any], host: str, port: int) -> bool:
    result = compose(config, "ps", "--status", "running", "-q", "maitu", capture=True, check=False)
    for container_id in result.stdout.split():
        inspected = run(
            ["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", container_id],
            capture=True,
        )
        try:
            mappings = json.loads(inspected.stdout)
            for published in mappings.values():
                for item in published or []:
                    if item["HostPort"] == str(port) and item["HostIp"] in {host, "0.0.0.0", "::"}:
                        return True
        except (ValueError, AttributeError, KeyError, TypeError) as exc:
            raise DeploymentError("无法确认既有平台是否占用所选端口；未停止容器。") from exc
    return False


def _named_volume_empty(volume: str) -> bool | None:
    result = run(["docker", "volume", "inspect", volume], capture=True, check=False)
    if result.returncode:
        return None
    try:
        entries = json.loads(result.stdout)
        mountpoint = Path(entries[0]["Mountpoint"])
    except (ValueError, IndexError, TypeError, KeyError) as exc:
        raise DeploymentError("无法安全读取 named volume 元数据；拒绝猜测是否为空。") from exc
    docker_root = Path(
        run(["docker", "info", "--format", "{{.DockerRootDir}}"], capture=True).stdout.strip()
    )
    root = docker_root / "volumes"
    if (
        not mountpoint.is_absolute()
        or not docker_root.is_absolute()
        or ".." in mountpoint.parts
        or not mountpoint.is_relative_to(root)
        or mountpoint.name != "_data"
    ):
        raise DeploymentError("named volume Mountpoint 超出 Docker 数据目录；拒绝读取。")
    if any(component.is_symlink() for component in (mountpoint, *mountpoint.parents)):
        raise DeploymentError("named volume Mountpoint 包含符号链接；拒绝读取。")
    if not mountpoint.is_dir():
        raise DeploymentError("named volume Mountpoint 不存在或不是目录；拒绝启动。")
    return not any(mountpoint.iterdir())


def _health(host: str, port: int, timeout: int = 90) -> dict[str, Any]:
    import time

    url = f"http://{f'[{host}]' if ':' in host else host}:{port}/api/health"
    deadline = time.monotonic() + timeout
    last = "unreachable"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=4) as response:
                value = json.loads(response.read(65537))
            if isinstance(value, dict) and value.get("ok") is True:
                return value
            last = "unexpected health response"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last = type(exc).__name__
        time.sleep(1)
    raise DeploymentError(f"平台健康检查失败（{last}）；请检查容器和反向代理，数据已保留。")


def deploy(config_dir: Path) -> None:
    """Deploy or adopt a platform without replacing original data or identity."""
    require_host()
    check_time()
    ensure_tools(["docker", "openssl"])
    _local_docker_socket()
    compose_version = run(["docker", "compose", "version", "--short"], capture=True).stdout.strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", compose_version)
    if not match or tuple(map(int, match.groups())) < (2, 24, 4):
        raise DeploymentError(
            "宿主 Docker Compose 至少需要 2.24.4，以安全替换原有全网卡端口映射；未改动现有服务。"
        )
    config_dir = _safe_directory(config_dir, "部署配置")
    ensure_directory(config_dir)
    manifest = load_manifest(config_dir, "platform")

    if manifest:
        source_root = _safe_directory(Path(str(manifest["source_root"])), "源码")
        env_file = _safe_existing_file(Path(str(manifest["env_file"])), "环境")
        compose_files = [
            _safe_existing_file(Path(str(value)), "Compose") for value in manifest["compose_files"]
        ]
        project_name = str(manifest["project_name"])
    else:
        source_root = _safe_directory(Path(ask("平台源码目录", str(ROOT))), "源码")
        base_compose = _safe_existing_file(
            Path(ask("原 Compose 主文件", str(source_root / "docker/docker-compose.yml"))),
            "Compose",
        )
        mode = ask("部署模式：new（全新安装）／adopt（接管已有部署）", "adopt")
        if mode not in {"new", "adopt"}:
            raise DeploymentError("部署模式只能是 new 或 adopt。")
        compose_env = source_root / "docker/.env"
        root_env = source_root / ".env"
        default_env = (
            compose_env if compose_env.exists() else root_env if root_env.exists() else None
        )
        if mode == "new":
            data_path = _safe_directory(Path(ask("新平台数据目录", "/srv/maitu/data")), "平台数据")
            if data_path.exists() and any(data_path.iterdir()):
                raise DeploymentError("新安装选择的目录已有数据；请使用 adopt，不会清空目录。")
            initial_port = validate_port(ask("新平台访问端口", "8088"))
            env_file = config_dir / "platform.env"
            atomic_write(
                env_file, env_text({"OCTOP_DATA": str(data_path), "OCTOP_PORT": str(initial_port)})
            )
        elif default_env is not None:
            env_file = _safe_existing_file(
                Path(ask("原 Compose 环境文件", str(default_env))), "环境"
            )
        else:
            env_file = _safe_existing_file(
                Path(ask("原 Compose 环境文件绝对路径（没有时请先保存原部署变量）")), "环境"
            )
        compose_files = [base_compose]
        additional = ask("额外原 Compose 覆盖文件路径（逗号分隔；没有则 none）", "none")
        if additional.strip().lower() != "none":
            compose_files.extend(
                _safe_existing_file(Path(item.strip()), "Compose 覆盖")
                for item in additional.split(",")
                if item.strip()
            )

    mount = _data_mount(source_root, env_file, compose_files, project_name)
    if manifest and manifest.get("data_mount") != mount:
        raise DeploymentError("原 Compose 的平台数据挂载与部署记录不同；拒绝切换数据或实例身份。")
    if mount["type"] == "bind":
        data_dir = _safe_directory(Path(mount["source"]), "平台数据")
        if not data_dir.exists() or not any(data_dir.iterdir()):
            if not confirm(
                f"bind 数据目录 {data_dir} 为空；确认这是新安装并允许首次初始化？", default=False
            ):
                raise DeploymentError("未确认空数据目录的新安装，未启动服务。")
            if not data_dir.exists():
                ensure_directory(data_dir, mode=0o700)
        elif not confirm(f"确认采用原 bind 数据目录 {data_dir}，不复制、不清空？", default=True):
            raise DeploymentError("未确认采用原数据，未启动服务。")
    else:
        data_dir = None
        empty = _named_volume_empty(mount["source"])
        if empty is None:
            if not confirm(
                f"Compose named volume {mount['source']} 尚不存在；确认这是新安装？", default=False
            ):
                raise DeploymentError("未确认新 named volume，未启动服务。")
        elif empty:
            if not confirm(
                f"原 named volume {mount['source']} 为空；确认这是新安装并允许首次初始化？",
                default=False,
            ):
                raise DeploymentError("未确认空 named volume 的新安装，未启动服务。")
        elif not confirm(
            f"确认复用原 named volume {mount['source']}，不删除、不重建？", default=True
        ):
            raise DeploymentError("未确认采用原数据卷，未启动服务。")
    data_location = str(data_dir) if data_dir is not None else f"volume:{mount['source']}"
    license_state_dir = _safe_directory(
        Path(
            ask(
                "独立授权身份及版本栅栏目录",
                str(manifest.get("license_state_dir", "/var/lib/maitu-license"))
                if manifest
                else "/var/lib/maitu-license",
            )
        ),
        "授权身份",
    )
    update_queue_dir = _safe_directory(
        Path(
            ask(
                "独立更新请求队列目录",
                str(manifest.get("update_queue_dir", "/var/lib/maitu-update-queue"))
                if manifest
                else "/var/lib/maitu-update-queue",
            )
        ),
        "更新队列",
    )
    disjoint_paths = [license_state_dir, update_queue_dir, config_dir]
    disjoint_labels = ["授权身份", "更新队列", "部署配置"]
    if data_dir is not None:
        disjoint_paths.append(data_dir)
        disjoint_labels.append("业务数据")
    _require_disjoint_state_paths(tuple(disjoint_paths), tuple(disjoint_labels))
    port = validate_port(
        ask(
            "平台宿主机访问端口",
            str(manifest.get("port", mount["published_port"]))
            if manifest
            else mount["published_port"],
        )
    )
    target_port = validate_port(mount["target_port"])
    bind_host = ask(
        "宿主机绑定地址（默认仅本机；办公网需明确输入地址）",
        str(manifest.get("host", "127.0.0.1")) if manifest else "127.0.0.1",
    )
    try:
        address = ipaddress.ip_address(bind_host)
        if address.is_unspecified or address.is_multicast:
            raise ValueError("wildcard/multicast hosts are not allowed")
        if not address.is_loopback and (not address.is_private or address.is_link_local):
            raise ValueError("only loopback or private office-LAN hosts are allowed")
        bind_host = str(address)
    except ValueError as exc:
        raise DeploymentError(
            "宿主绑定仅支持 loopback 或明确的私有办公网 IPv4/IPv6 地址，不接受公网/全网卡监听。"
        ) from exc
    original_config = {
        "source_root": str(source_root),
        "env_file": str(env_file),
        "compose_files": [
            str(item)
            for item in compose_files
            if item.name
            not in {
                "docker-compose.licensing.yml",
                "docker-compose.managed.yml",
                "platform.compose.override.yml",
            }
        ],
        "project_name": project_name,
    }
    if not port_available(bind_host, port) and not _owns_port(original_config, bind_host, port):
        raise DeploymentError(f"宿主机端口 {port} 已被占用；未启动或停止任何服务。")
    bundle_data = None
    trust_dir = (
        _safe_directory(Path(str(manifest["trust_dir"])), "授权信任")
        if manifest and manifest.get("trust_dir")
        else config_dir / "platform-trust"
    )
    license_keys_path = trust_dir / "license-trusted-keys.json"
    release_keys_path = trust_dir / "release-trusted-keys.json"
    reusable_trust = bool(
        manifest
        and manifest.get("trust_bundle")
        and isinstance(manifest.get("key_fingerprints"), dict)
        and manifest.get("key_fingerprints")
        and license_keys_path.is_file()
        and release_keys_path.is_file()
    )
    if reusable_trust and confirm("继续使用已确认的授权和发布公钥 keyring？", default=True):
        server_url = check_url(str(manifest["server_url"]))
        license_keys_path = _safe_existing_file(license_keys_path, "授权签名公钥 keyring")
        release_keys_path = _safe_existing_file(release_keys_path, "发布签名公钥 keyring")
        ca_path = (
            _safe_existing_file(Path(str(manifest["ca_file"])), "中心 CA")
            if manifest.get("ca_file")
            else None
        )
        try:
            license_keys = json.loads(license_keys_path.read_text(encoding="utf-8"))
            release_keys = json.loads(release_keys_path.read_text(encoding="utf-8"))
            key_data = bundle.validate_bundle(
                {
                    "format": bundle.FORMAT,
                    "version": 2,
                    "server_url": server_url,
                    "license_trusted_keys": license_keys,
                    "release_trusted_keys": release_keys,
                    "ca_certificate_pem": (
                        ca_path.read_text(encoding="ascii") if ca_path is not None else None
                    ),
                }
            )
            fingerprints = bundle.key_fingerprints(key_data)
        except (OSError, UnicodeError, ValueError, DeploymentError) as exc:
            raise DeploymentError(
                "原中心 keyring 损坏；未重新初始化实例，请重新导入可信接入包。"
            ) from exc
        if fingerprints != manifest.get("key_fingerprints"):
            raise DeploymentError("磁盘公钥与已确认指纹不同；请选择重新导入并人工核对信任。")
        print(f"使用原中心 {server_url}；授权与发布 keyring 指纹均已核对。")
    else:
        bundle_path = Path(ask("导入授权中心接入包文件路径（JSON）")).expanduser()
        bundle_data, fingerprints = _trust_bundle(_safe_existing_file(bundle_path, "连接包"))
        _confirm_bundle(bundle_data, fingerprints)
        if manifest and not confirm(
            "此次会修改中心连接配置，但保留实例身份和全部业务数据；确认继续？"
        ):
            raise DeploymentError("已取消中心连接变更。")
        server_url = bundle_data["server_url"]
        trust_dir = config_dir / "platform-trust"
        license_keys_path = trust_dir / "license-trusted-keys.json"
        release_keys_path = trust_dir / "release-trusted-keys.json"
        ca_path = trust_dir / "root-ca.pem" if bundle_data.get("ca_certificate_pem") else None
    time_zone = ask(
        "平台服务器显示时区",
        str(manifest.get("timezone", "Asia/Shanghai")) if manifest else "Asia/Shanghai",
    )
    try:
        ZoneInfo(time_zone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise DeploymentError("服务器时区无效。") from exc
    disjoint_paths = [license_state_dir, update_queue_dir, config_dir, trust_dir]
    disjoint_labels = ["授权身份", "更新队列", "部署配置", "授权信任"]
    if data_dir is not None:
        disjoint_paths.append(data_dir)
        disjoint_labels.append("业务数据")
    _require_disjoint_state_paths(tuple(disjoint_paths), tuple(disjoint_labels))
    host_override = config_dir / "platform.compose.override.yml"
    effective_files = [
        str(item)
        for item in compose_files
        if item.name
        not in {
            "docker-compose.licensing.yml",
            "docker-compose.managed.yml",
            "platform.compose.override.yml",
        }
    ]
    effective_files.append(str(host_override))
    config: dict[str, Any] = {
        "source_root": str(source_root),
        "env_file": str(env_file),
        "compose_files": effective_files,
        "project_name": project_name,
    }
    details = {
        "version": 2,
        "role": "platform",
        "source_root": str(source_root),
        "env_file": str(env_file),
        "compose_files": effective_files,
        "project_name": project_name,
        "data_dir": data_location,
        "data_mount": mount,
        "server_url": server_url,
        "trust_dir": str(trust_dir),
        "trust_bundle": True,
        "ca_file": str(ca_path) if ca_path else None,
        "key_fingerprints": fingerprints,
        "license_state_dir": str(license_state_dir),
        "update_queue_dir": str(update_queue_dir),
        "host": bind_host,
        "port": port,
        "container_port": target_port,
        "platform_override": str(host_override),
        "state": "CONFIGURED",
        "timezone": time_zone,
    }
    files_to_restore = [
        host_override,
        trust_dir / "license-trusted-keys.json",
        trust_dir / "release-trusted-keys.json",
        trust_dir / "root-ca.pem",
        config_dir / "platform.json",
    ]
    with configuration_transaction(files_to_restore):
        if bundle_data is not None:
            bundle.install_bundle(bundle_data, trust_dir)
        license_state_dir = _service_state_directory(license_state_dir, "授权身份")
        update_queue_dir = _service_state_directory(update_queue_dir, "更新队列")
        _build_platform_override(
            host_override,
            host=bind_host,
            published_port=port,
            target_port=target_port,
            server_url=server_url,
            license_keys_path=license_keys_path,
            release_keys_path=release_keys_path,
            license_state_dir=license_state_dir,
            update_queue_dir=update_queue_dir,
            ca_path=ca_path,
            time_zone=time_zone,
        )
        compose(config, "config", "--quiet", capture=True)
        protected_mounts = {
            "/run/maitu-trust/license-trusted-keys.json": (str(license_keys_path), True),
            "/run/maitu-trust/release-trusted-keys.json": (str(release_keys_path), True),
            "/var/lib/maitu-license": (str(license_state_dir), False),
            "/var/lib/maitu-update-queue": (str(update_queue_dir), False),
        }
        if ca_path is not None:
            protected_mounts["/run/maitu-trust/root-ca.pem"] = (str(ca_path), True)
        _verify_runtime_mounts(config, mount, protected_mounts)
        save_manifest(config_dir, "platform", details)
    if confirm(
        "构建当前交付源码的平台镜像？本次 execute 隔离运行时升级需构建；保留旧镜像可能无法使用 execute。",
        default=manifest is None,
    ):
        compose(config, "build", "maitu", timeout=3600)
    compose(config, "up", "-d", timeout=3600)
    _health(bind_host, port)

    try:
        center_health = wait_http(
            server_url + "/health",
            ca_file=ca_path,
            timeout=8,
        )
        if center_health.get("status") != "ok":
            raise DeploymentError("授权中心 health 状态不是 ok。")
        print("授权中心 HTTPS/TLS 与 SQLite health 检查通过。")
    except DeploymentError:
        print(
            "授权中心 HTTPS/health 暂不可达；继续读取现有实例状态并按 ACTIVE/CACHED/UNACTIVATED 安全处理。"
        )
    probe = inspect_platform(config)
    expected_configuration = {
        "MAITU_LICENSE_CENTER_URL": server_url,
        "MAITU_LICENSE_TRUSTED_KEYS_FILE": "/run/maitu-trust/license-trusted-keys.json",
        "MAITU_LICENSE_STATE_DIR": "/var/lib/maitu-license",
        "MAITU_UPDATE_CENTER_URL": server_url,
        "MAITU_UPDATE_TRUSTED_KEYS_FILE": "/run/maitu-trust/release-trusted-keys.json",
        "MAITU_UPDATE_QUEUE_DIR": "/var/lib/maitu-update-queue",
    }
    if (
        probe.get("configuration") != expected_configuration
        or probe.get("fingerprints") != fingerprints
    ):
        raise DeploymentError(
            "实际运行容器的中心地址、CA 或公钥指纹与已确认配置不符；"
            "请检查业务数据目录中的 env 等覆盖来源。向导未执行激活或同步，也不报告部署成功。"
        )
    if not probe.get("health_ok") or not probe.get("route_ok"):
        print(
            f"运行容器到中心的 HTTPS/实例路由检查未通过：{probe.get('error_class', probe.get('route_status', 'unknown'))}；激活仍将使用真实签名协议，不忽略 TLS。"
        )
    state = _compose_status(config)["state"]
    operation_ok = False
    if state in {"ACTIVE", "CACHED_ACTIVE"}:
        operation_ok = _run_license(config, "refresh")
        if not operation_ok:
            after = _compose_status(config)
            if after.get("state") != "CACHED_ACTIVE":
                raise DeploymentError(
                    "授权中心同步失败且没有有效缓存授权；平台业务保持锁定。请运行 check 排障。"
                )
            print("授权中心暂不可达；保留现有实例身份和有效缓存授权（CACHED_ACTIVE）。")
    elif state == "UNACTIVATED":
        code = ask("输入授权中心签发的一次性激活码", secret=True)
        operation_ok = _run_license(config, "activate", code=code)
        if not operation_ok:
            raise DeploymentError(
                "激活失败；激活码未进入参数或日志，实例保持未激活。请运行 check 排障。"
            )
    else:
        operation_ok = _run_license(config, "refresh")
        if not operation_ok:
            raise DeploymentError(f"实例授权状态为 {state}；中心同步失败，业务保持锁定。")

    after_cli = _compose_status(config)
    if (
        after_cli.get("state") not in {"ACTIVE", "CACHED_ACTIVE"}
        or after_cli.get("allowed") is not True
    ):
        raise DeploymentError(
            f"本地 CLI 报告 {after_cli.get('state')}，未确认业务许可已允许；平台保持锁定。"
        )
    # CLI uses a one-off container; the service process caches credential state in memory.
    # Recreate only after a successful sync/activation so it reloads signed disk state.
    if operation_ok:
        print("Octop license CLI 已完成授权中心 HTTPS challenge、实例签名验证和凭据同步。")
        print("正在让平台服务重新载入本地签名授权凭据。")
        compose(config, "up", "-d", "--force-recreate", "maitu")
    _health(bind_host, port)
    runtime = _runtime_status(bind_host, port)
    if (
        runtime.get("state") not in {"ACTIVE", "CACHED_ACTIVE"}
        or runtime.get("allowed") is not True
    ):
        raise DeploymentError(
            f"容器内运行状态 {runtime.get('state')} 未确认许可已允许；不报告激活成功。"
        )
    details["state"] = runtime["state"]
    save_manifest(config_dir, "platform", details)
    print(f"平台就绪：{runtime['state']}；数据源：{data_location}；访问端口：{bind_host}:{port}")
    credential = data_dir / "credential.txt" if data_dir is not None else None
    if credential is not None and credential.is_file():
        print(f"首次管理员凭据由初始化生成，请在服务器上通过安全通道读取：{credential}")
    elif mount["type"] == "volume":
        print(
            "如首次管理员凭据由初始化生成，凭据保存在原 named volume 的 /data/.octop/credential.txt；勿将其复制到部署日志。"
        )


def _runtime_status(host: str, port: int) -> dict[str, Any]:
    url = f"http://{f'[{host}]' if ':' in host else host}:{port}/api/license/status"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            value = json.loads(response.read(65537))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise DeploymentError(
            f"平台授权状态端点不可读（{type(exc).__name__}）；未报告激活成功。"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("state"), str):
        raise DeploymentError("平台授权状态响应无效；未报告激活成功。")
    return value
