"""Read-only diagnostics for customer license deployments."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import ssl
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import bundle
from .center_probe import inspect_center
from .common import DeploymentError, compose, load_manifest, run, wait_http
from .probe import inspect_platform


def _report(label: str, ok: bool, detail: str) -> bool:
    print(f"{'[OK]' if ok else '[FAIL]'} {label}: {detail}")
    return ok


def _safe_path(path: Path) -> bool:
    path = path.expanduser().absolute()
    return ".." not in path.parts and not any(
        component.is_symlink() for component in (path, *path.parents)
    )


def _managed_certbot_key(path: Path) -> bool:
    live_root = Path("/etc/letsencrypt/live")
    archive_root = Path("/etc/letsencrypt/archive")
    try:
        if not path.is_symlink() or not path.is_relative_to(live_root):
            return False
        relative = path.relative_to(live_root)
        resolved = path.resolve(strict=True)
        return (
            len(relative.parts) == 2
            and relative.parts[1] == "privkey.pem"
            and resolved.is_relative_to(archive_root)
            and resolved.parent.name == relative.parts[0]
            and resolved.name.startswith("privkey")
            and resolved.is_file()
        )
    except (OSError, ValueError, RuntimeError):
        return False


def _fetch_health(url: str, ca_file: Path | None) -> dict[str, Any]:
    return wait_http(url, ca_file=ca_file, timeout=8)


def _current_kid(state_dir: Path) -> str | None:
    state_file = state_dir / "license-state.json"
    if not _safe_path(state_file) or not state_file.is_file():
        return None
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        tokens = state.get("online") or state.get("offline") if isinstance(state, dict) else None
        header = tokens.split(".", 1)[0] if isinstance(tokens, str) else ""
        header += "=" * (-len(header) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(header)) if header else {}
        kid = decoded.get("kid") if isinstance(decoded, dict) else None
        return kid if isinstance(kid, str) else None
    except (OSError, ValueError, TypeError):
        return None


def _trust_report(trust_dir: Path, expected: dict[str, str]) -> tuple[bool, set[str]]:
    paths = (
        trust_dir / "license-trusted-keys.json",
        trust_dir / "release-trusted-keys.json",
    )
    if any(not _safe_path(path) or not path.is_file() for path in paths):
        return False, set()
    try:
        calculated = bundle.key_fingerprints(
            {
                "license_trusted_keys": json.loads(paths[0].read_text(encoding="utf-8")),
                "release_trusted_keys": json.loads(paths[1].read_text(encoding="utf-8")),
            }
        )
        return calculated == expected, set(calculated)
    except (OSError, UnicodeError, ValueError, DeploymentError):
        return False, set()


def _platform_check(config_dir: Path) -> int:
    failures = 0
    manifest = load_manifest(config_dir, "platform")
    if manifest is None:
        _report("平台部署记录", False, "尚无 platform.json；仅报告失败，不执行安装")
        return 1
    source = manifest.get("source_root", "unknown")
    env_file = manifest.get("env_file", "unknown")
    compose_files = manifest.get("compose_files", [])
    project = manifest.get("project_name", "unknown")
    data_dir = str(manifest.get("data_dir", "unknown"))
    host = str(manifest.get("host", "127.0.0.1"))
    port = manifest.get("port")
    print(f"平台有效来源：source={source}; env_file={env_file}; project={project}")
    print(f"Compose 文件：{', '.join(str(item) for item in compose_files)}")
    print(f"平台数据：{data_dir}; host bind={host}:{port}")

    for value, label in [
        (source, "源码"),
        (env_file, "环境"),
        *[(item, "Compose") for item in compose_files],
    ]:
        path = Path(str(value))
        ok = _safe_path(path) and path.exists()
        failures += not _report(f"{label}路径", ok, str(path))
    mount = manifest.get("data_mount", {})
    if isinstance(mount, dict) and mount.get("type") == "bind":
        data_path = Path(str(mount.get("source", "")))
        data_ok = _safe_path(data_path) and data_path.is_dir()
        failures += not _report("平台数据挂载", data_ok, str(data_path))
    elif isinstance(mount, dict) and mount.get("type") == "volume":
        data_ok = bool(mount.get("source"))
        failures += not _report(
            "平台数据挂载", data_ok, f"named volume {mount.get('source', 'unknown')}"
        )
    else:
        failures += not _report("平台数据挂载", False, "部署记录缺少原始数据挂载类型/来源")
    trust_dir = Path(str(manifest.get("trust_dir", "")))
    expected = manifest.get("key_fingerprints", {})
    if not isinstance(expected, dict):
        expected = {}
    trust_ok, kids = _trust_report(trust_dir, expected)
    failures += not _report(
        "挂载公钥信任", trust_ok, f"目录={trust_dir}; kids={','.join(sorted(kids)) or 'none'}"
    )

    ca_value = manifest.get("ca_file")
    ca_file = Path(str(ca_value)) if ca_value else None
    ca_ok = ca_file is None or (_safe_path(ca_file) and ca_file.is_file())
    failures += not _report("中心 TLS CA", ca_ok, str(ca_file) if ca_file else "使用系统 CA")
    try:
        center = _fetch_health(str(manifest["server_url"]).rstrip("/") + "/health", ca_file)
        center_ok = center.get("status") == "ok"
        failures += not _report(
            "授权中心 HTTPS/SQLite health", center_ok, str(center.get("status", "unexpected"))
        )
    except (
        OSError,
        ValueError,
        urllib.error.URLError,
        DeploymentError,
        KeyError,
        ssl.SSLError,
    ) as exc:
        failures += not _report("授权中心 HTTPS/health", False, type(exc).__name__)

    compose_config = {
        "source_root": str(source),
        "env_file": str(env_file),
        "compose_files": compose_files,
        "project_name": project,
    }
    running = False
    try:
        result = compose(
            compose_config, "ps", "--status", "running", "-q", "maitu", capture=True, check=False
        )
        running = bool(result.stdout.strip())
        failures += not _report("平台容器", running, "running" if running else "未运行")
    except DeploymentError as exc:
        failures += not _report("平台容器", False, str(exc))
    if running:
        try:
            actual = inspect_platform(compose_config)
            effective = actual.get("configuration", {})
            expected_configuration = {
                "MAITU_LICENSE_CENTER_URL": manifest["server_url"],
                "MAITU_LICENSE_TRUSTED_KEYS_FILE": "/run/maitu-trust/license-trusted-keys.json",
                "MAITU_LICENSE_STATE_DIR": "/var/lib/maitu-license",
                "MAITU_UPDATE_CENTER_URL": manifest["server_url"],
                "MAITU_UPDATE_TRUSTED_KEYS_FILE": "/run/maitu-trust/release-trusted-keys.json",
                "MAITU_UPDATE_QUEUE_DIR": "/var/lib/maitu-update-queue",
            }
            failures += not _report(
                "容器实际连接配置",
                effective == expected_configuration,
                json.dumps(effective, ensure_ascii=False),
            )
            failures += not _report(
                "容器实际公钥指纹", actual.get("fingerprints") == expected, "与已确认接入包逐项比较"
            )
            failures += not _report(
                "容器出站 HTTPS/TLS/health",
                actual.get("health_ok") is True,
                str(actual.get("error_class", "ok")),
            )
            failures += not _report(
                "实例接口只读路由检查",
                actual.get("route_ok") is True,
                f"OPTIONS HTTP {actual.get('route_status', 'unknown')}；签名协议由实际激活/同步验证",
            )
        except (DeploymentError, KeyError) as exc:
            failures += not _report("容器实际配置/出站诊断", False, type(exc).__name__)

    try:
        health = _fetch_local_json(host, int(port), "/api/health")
        failures += not _report(
            "平台健康接口",
            health.get("ok") is True,
            f"ok={health.get('ok')}; db={health.get('db')}",
        )
        if not running:
            raise DeploymentError("平台容器未运行，无法只读读取本地许可状态。")
        license_state = _fetch_local_json(host, int(port), "/api/license/status")
        state = license_state["state"]
        current_kid = _current_kid(Path(str(manifest["license_state_dir"])))
        license_kids = {key.split(":", 1)[1] for key in kids if key.startswith("license:")}
        kid_ok = (state == "UNACTIVATED" and current_kid is None) or (
            current_kid is not None and current_kid in license_kids
        )
        failures += not _report(
            "实例授权 current kid",
            kid_ok,
            f"kid={current_kid or '无签名凭据（未激活）'}",
        )
        state_ok = (state == "UNACTIVATED" and license_state.get("allowed") is False) or (
            state in {"ACTIVE", "CACHED_ACTIVE"} and license_state.get("allowed") is True
        )
        state_detail = (
            "UNACTIVATED（正常锁定，等待激活）"
            if state == "UNACTIVATED"
            else f"{state}; allowed={license_state.get('allowed')}"
        )
        failures += not _report("平台许可状态", state_ok, state_detail)
    except (OSError, ValueError, urllib.error.URLError, DeploymentError, TypeError) as exc:
        failures += not _report("平台健康/许可状态", False, type(exc).__name__)
    return int(bool(failures))


def _fetch_local_json(host: str, port: int, path: str) -> dict[str, Any]:
    rendered_host = f"[{host}]" if ":" in host else host
    with urllib.request.urlopen(f"http://{rendered_host}:{port}{path}", timeout=5) as response:
        value = json.loads(response.read(65537))
    if not isinstance(value, dict):
        raise DeploymentError("平台健康响应格式无效。")
    return value


def _center_check(config_dir: Path) -> int:
    manifest = load_manifest(config_dir, "center")
    if manifest is None:
        _report("授权中心部署记录", False, "尚无 center.json；仅报告失败，不执行安装")
        return 1
    print(
        f"授权中心有效来源：source={manifest.get('source_root', 'unknown')}; "
        f"env_file={manifest.get('env_file', 'unknown')}; "
        f"data={manifest.get('data_dir', 'unknown')}; "
        f"project={manifest.get('project_name', 'unknown')}; "
        f"server={manifest.get('server_name', 'unknown')}"
    )
    failures = 0
    source = Path(str(manifest.get("source_root", "")))
    failures += not _report("中心源码", _safe_path(source) and source.is_dir(), str(source))
    cert_path = Path(str(manifest.get("cert_host", ""))) if manifest.get("cert_host") else None
    key_path = Path(str(manifest.get("key_host", ""))) if manifest.get("key_host") else None
    cert_ok = cert_path is not None and _safe_path(cert_path) and cert_path.is_file()
    certbot_managed = manifest.get("certificate_mode") == "certbot"
    key_ok = key_path is not None and (
        (_safe_path(key_path) and key_path.is_file())
        or (certbot_managed and _managed_certbot_key(key_path))
    )
    try:
        key_permissions_ok = (
            key_path is not None and stat.S_IMODE(key_path.stat().st_mode) & 0o077 == 0
        )
    except OSError:
        key_permissions_ok = False
    key_ok = key_ok and key_permissions_ok
    failures += not _report(
        "中心 TLS 证书与密钥",
        cert_ok and key_ok,
        f"mode={manifest.get('certificate_mode', 'unknown')}; certificate={cert_path}; private key path validated without reading its contents",
    )
    # The public liveness endpoint is read-only and performs a real SQLite health query.
    url = manifest.get("health_url") or manifest.get("server_url")
    if isinstance(url, str):
        endpoint = url.rstrip("/") + ("/health" if not url.rstrip("/").endswith("/health") else "")
        try:
            health = _fetch_health(
                endpoint, Path(str(manifest["ca_file"])) if manifest.get("ca_file") else None
            )
            ok = health.get("status") == "ok"
            failures += not _report(
                "中心 HTTPS/SQLite health", ok, str(health.get("status", "unexpected"))
            )
        except (OSError, ValueError, urllib.error.URLError, DeploymentError, ssl.SSLError) as exc:
            failures += not _report("中心 HTTPS/health", False, type(exc).__name__)
    else:
        failures += not _report("中心健康地址", False, "部署记录中缺少只读 health URL")
    try:
        actual = inspect_center(manifest)
        rendered = json.loads(compose(manifest, "config", "--format", "json", capture=True).stdout)
        environment = rendered["services"]["license-center"]["environment"]
        names = (
            "LICENSE_CENTER_DATA",
            "LICENSE_CENTER_TRUSTED_PROXY_IPS",
            "LICENSE_CENTER_TLS_CERT",
            "LICENSE_CENTER_TIMEZONE",
        )
        expected_environment = {name: str(environment.get(name, "")) for name in names}
        failures += not _report(
            "中心容器实际配置",
            actual.get("environment") == expected_environment,
            json.dumps(actual.get("environment", {}), ensure_ascii=False),
        )
        certificate_hash = hashlib.sha256(cert_path.read_bytes()).hexdigest() if cert_path else None
        failures += not _report(
            "容器实际公共证书",
            actual.get("certificate_sha256") == certificate_hash,
            "比较挂载文件 SHA256，不读取 TLS 私钥",
        )
        exported = Path(
            str(
                manifest.get("connection_file", Path(str(manifest["data_dir"])) / "connection.json")
            )
        )
        expected_keys = bundle.key_fingerprints(bundle.load_bundle(exported))
        failures += not _report(
            "实际签发身份与导出接入包",
            actual.get("fingerprints") == expected_keys,
            "只读数据库公钥与导出指纹一致",
        )
        failures += not _report(
            "中心管理员初始化",
            isinstance(actual.get("owners"), int) and actual["owners"] > 0,
            "仅查询数量，不显示密码或 MFA 材料",
        )
    except (DeploymentError, OSError, ValueError, TypeError, KeyError) as exc:
        failures += not _report("中心容器配置/身份", False, type(exc).__name__)
    try:
        backup = run(
            ["systemctl", "is-enabled", "maitu-license-center-backup.timer"],
            capture=True,
            check=False,
        )
        failures += not _report(
            "加密备份 timer", backup.returncode == 0, backup.stdout.strip() or "not enabled"
        )
        last_backup = run(
            [
                "systemctl",
                "show",
                "maitu-license-center-backup.service",
                "--property=Result",
                "--value",
            ],
            capture=True,
            check=False,
        )
        failures += not _report(
            "最近备份任务",
            last_backup.returncode == 0 and last_backup.stdout.strip() == "success",
            last_backup.stdout.strip() or "unknown",
        )
        if certbot_managed:
            renewal = run(
                ["systemctl", "is-enabled", "snap.certbot.renew.timer"], capture=True, check=False
            )
            failures += not _report(
                "Certbot 续期 timer",
                renewal.returncode == 0,
                renewal.stdout.strip() or "not enabled",
            )
    except DeploymentError as exc:
        failures += not _report("备份/续期任务", False, type(exc).__name__)
    return int(bool(failures))


def check_native(
    source: Path, python: Path, extras: list[str], health_url: str | None = None
) -> int:
    """Inspect the selected native interpreter without uv sync or application startup."""
    source = source.expanduser().absolute()
    python = python.expanduser().absolute()
    failures = 0
    for name in ("pyproject.toml", "uv.lock", "src/octop/dashboard/index.html"):
        failures += not _report(name, (source / name).is_file(), str(source / name))
    for name in ("uv", "node", "npm", "bwrap"):
        failures += not _report(
            name, shutil.which(name) is not None, shutil.which(name) or "未安装"
        )
    print("[INFO] make 非运行依赖；缺失时按 scripts/README.md 的直接命令验证。")
    print(
        "[INFO] bwrap 存在不代表 userns/AppArmor 已授权；请另运行 setup-host-sandbox.sh --status。"
    )
    modules = [
        "octop",
        "fastapi",
        "uvicorn",
        "harness_agent",
        "pdfplumber",
        "pandas",
        "rapidocr",
        "onnxruntime",
    ]
    if "local-embedding" in extras:
        modules += ["fastembed", "huggingface_hub"]
    if "desktop" in extras:
        modules += ["mss", "pynput"]
    else:
        print(
            "[INFO] desktop 未选用；不检查桌面会话，但当前 harness-agent[all] 仍传递安装 pynput/evdev。"
        )
    compiler = shutil.which("gcc")
    print(
        f"[INFO] evdev 构建编译器: {compiler or '未安装；源码安装前 Ubuntu: sudo apt-get install build-essential python3-dev linux-libc-dev'}"
    )
    if not python.is_file():
        _report("服务 Python", False, str(python))
        return 1
    # Import in the service interpreter: metadata alone misses broken native libraries.
    probe = (
        "import importlib,json,sys; result={}; "
        'exec("for name in sys.argv[1:]:\\n'
        " try: importlib.import_module(name); result[name]='ok'\\n"
        ' except Exception as exc: result[name]=type(exc).__name__\\n"); '
        "print(json.dumps(result))"
    )
    try:
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", probe, *modules],
            cwd=source,
            capture_output=True,
            text=True,
            check=True,
            timeout=90,
        )
        status = json.loads(result.stdout.strip().splitlines()[-1])
        for name in modules:
            failures += not _report(name, status.get(name) == "ok", status.get(name, "unknown"))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        failures += not _report("依赖导入", False, "指定 Python 无法完成导入检查；未安装或修改依赖")
    print("[INFO] 依赖导入不验证 OCR/向量模型下载、桌面会话权限、配置或授权身份。")
    if health_url:
        try:
            response = _fetch_health(health_url, None)
            failures += not _report(
                "原生服务 health", response.get("ok") is True, json.dumps(response)
            )
        except (DeploymentError, OSError, ValueError):
            failures += not _report("原生服务 health", False, "不可达或不是有效健康响应")
    else:
        print("[INFO] 未提供 --health-url；未验证运行中的服务、数据库或授权状态。")
    return int(bool(failures))


def check(config_dir: Path, role: str | None = None) -> int:
    """Report state without installing, restarting, activating, or changing config."""
    if role not in {None, "center", "platform"}:
        raise DeploymentError("role 必须是 center 或 platform。")
    clock_failed = 0
    try:
        clock = run(
            ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
            capture=True,
            check=False,
            timeout=10,
        )
        clock_failed = int(
            not _report(
                "主机时钟 NTP",
                clock.returncode == 0 and clock.stdout.strip() == "yes",
                clock.stdout.strip() or "unknown",
            )
        )
    except DeploymentError as exc:
        clock_failed = int(not _report("主机时钟 NTP", False, type(exc).__name__))
    if role == "platform":
        return int(bool(_platform_check(config_dir) or clock_failed))
    if role == "center":
        return int(bool(_center_check(config_dir) or clock_failed))
    available = []
    for name in ("center", "platform"):
        if (config_dir / f"{name}.json").is_file():
            available.append(name)
    if not available:
        _report("部署记录", False, "没有 center.json 或 platform.json")
        return 1
    failures = clock_failed
    for name in available:
        result = _center_check(config_dir) if name == "center" else _platform_check(config_dir)
        failures += result
    return int(bool(failures))
