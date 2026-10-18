"""Interactive, repeat-safe Ubuntu deployment for the standalone license center."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
from pathlib import Path

from .bundle import key_fingerprints, load_bundle
from .common import (
    ROOT,
    DeploymentError,
    ask,
    atomic_write,
    check_time,
    compose,
    configuration_transaction,
    confirm,
    ensure_directory,
    ensure_tools,
    env_text,
    load_manifest,
    require_host,
    run,
    save_manifest,
    validate_port,
    wait_http,
)

_NGINX_MARKER = "# Managed by maitu deploy.sh; do not edit ownership marker"
_DEFAULT_ROOT = Path("/srv/maitu-license-center")


def _path(label: str, default: Path) -> Path:
    return Path(ask(label, str(default))).expanduser().absolute()


def _is_initialized(data_dir: Path) -> bool:
    if data_dir.is_symlink() or any(parent.is_symlink() for parent in data_dir.parents):
        raise DeploymentError("授权中心数据目录路径包含符号链接。")
    for name in (
        "center.sqlite3",
        "center.sqlite3-wal",
        "center.sqlite3-shm",
        "keys",
        "migrations",
    ):
        path = data_dir / name
        if path.is_symlink():
            raise DeploymentError(f"授权中心持久数据中包含符号链接：{path}")
    keys = data_dir / "keys"
    if keys.is_dir() and any(path.is_symlink() for path in keys.rglob("*")):
        raise DeploymentError("授权中心 keys/ 内存在符号链接；拒绝跟随或覆盖密钥路径。")
    return (
        any((data_dir / name).exists() for name in ("center.sqlite3", "center.sqlite3-wal", "keys"))
        or (data_dir / "migrations").exists()
    )


def _needs_owner(data_dir: Path) -> bool:
    database = data_dir / "center.sqlite3"
    if not database.exists():
        keys = data_dir / "keys"
        if keys.exists() and any(keys.iterdir()):
            raise DeploymentError(
                "中心密钥仍存在但数据库缺失；必须恢复原数据库，不能新建第二个中心身份。"
            )
        return True
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            if connection.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone():
                return False
            if connection.execute("SELECT 1 FROM signing_keys LIMIT 1").fetchone():
                raise DeploymentError(
                    "数据库已有签发身份但没有管理员；请使用原中心的恢复流程，拒绝重新初始化身份。"
                )
    except sqlite3.Error as exc:
        raise DeploymentError(
            "中心数据库无法只读检查初始化状态；请恢复有效数据库，不会将损坏数据当作新安装。"
        ) from exc
    return True


def _protect_data_directory(data_dir: Path) -> None:
    if data_dir.is_symlink() or any(parent.is_symlink() for parent in data_dir.parents):
        raise DeploymentError("授权中心数据目录路径包含符号链接。")
    if not data_dir.exists():
        ensure_directory(data_dir, mode=0o700, uid=10001, gid=10001)
        return
    info = data_dir.stat()
    if info.st_uid != 10001 or info.st_mode & 0o777 != 0o700:
        if not confirm(
            f"现有数据目录权限/属主为 {info.st_uid}:{info.st_gid} mode={info.st_mode & 0o777:o}；"
            "需要改为中心容器 UID/GID 10001、目录 0700，确认这是已采用的中心目录？"
        ):
            raise DeploymentError("数据目录权限未经确认；未更改数据或启动中心。")
        ensure_directory(data_dir, mode=0o700, uid=10001, gid=10001)
    core_paths = [
        data_dir / name for name in ("center.sqlite3", "center.sqlite3-wal", "center.sqlite3-shm")
    ]
    key_dir = data_dir / "keys"
    if key_dir.is_dir():
        core_paths.extend(path for path in key_dir.rglob("*") if path.is_dir() or path.is_file())
        core_paths.append(key_dir)
    mismatched = [
        path
        for path in core_paths
        if path.exists()
        and (
            path.stat().st_uid != 10001
            or path.stat().st_mode & 0o777 != (0o700 if path.is_dir() else 0o600)
        )
    ]
    if mismatched:
        if not confirm(
            "检测到中心数据库/密钥属主或权限不符合 UID/GID 10001 与 0600/0700；确认只修复这些已识别持久文件？"
        ):
            raise DeploymentError("中心持久文件权限未经确认；未更改身份材料。")
        for path in mismatched:
            path.chmod(0o700 if path.is_dir() else 0o600)
            os.chown(path, 10001, 10001)


def _parse_env(path: Path) -> dict[str, str]:
    """Read only the deployment variables needed for safe adoption; never display values."""
    values: dict[str, str] = {}
    if not path.is_file() or path.is_symlink():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$", line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def _validate_storage_overrides(values: dict[str, str]) -> None:
    expected = {
        "LICENSE_CENTER_DB": "/data/center.sqlite3",
        "LICENSE_CENTER_KEY_DIR": "/data/keys",
        "LICENSE_CENTER_ENCRYPTION_KEY": "/data/keys/encryption.key",
    }
    for name, path in expected.items():
        if name in values and values[name] != path:
            raise DeploymentError(
                f"发现 {name} 配置了非默认持久路径；当前 Compose 仅持久挂载 /data。"
                "为避免切换到空数据库或遗漏密钥，尚未修改或初始化；请先将该数据布局迁移到受支持的 /data 目录。"
            )


def _set_env_value(path: Path, name: str, value: str) -> None:
    if path.is_symlink():
        raise DeploymentError(f"拒绝修改符号链接环境文件：{path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}=")
    indexes = [index for index, line in enumerate(lines) if pattern.match(line)]
    if len(indexes) > 1:
        raise DeploymentError(f"环境文件重复定义 {name}；拒绝猜测生效值。")
    setting = env_text({name: value}).splitlines()[1]
    if indexes:
        lines[indexes[0]] = setting
    else:
        lines.append(setting)
    atomic_write(path, "\n".join(lines) + "\n", mode=0o600)


def _compose_config(
    source: Path, env_file: Path, project: str, compose_file: Path
) -> dict[str, object]:
    return {
        "source_root": str(source),
        "env_file": str(env_file),
        "compose_files": [str(compose_file)],
        "project_name": project,
    }


def _gateway(network: str) -> str:
    result = run(["docker", "network", "inspect", network], capture=True)
    try:
        networks = json.loads(result.stdout)
        gateway = networks[0]["IPAM"]["Config"][0]["Gateway"]
        return str(ipaddress.ip_address(gateway))
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise DeploymentError(
            f"无法读取 Docker 网络 {network} 的实际网关；请先检查网络配置。"
        ) from exc


def _assert_existing_center(project: str, compose_file: Path, data_dir: Path) -> None:
    labeled = run(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.service=license-center"],
        capture=True,
    ).stdout.split()
    image_matches = run(
        ["docker", "ps", "-aq", "--filter", "ancestor=maitu-license-center:local"],
        capture=True,
    ).stdout.split()
    for container_id in set(labeled + image_matches):
        actual_project = run(
            [
                "docker",
                "inspect",
                '--format={{index .Config.Labels "com.docker.compose.project"}}',
                container_id,
            ],
            capture=True,
        ).stdout.strip()
        mount_data = run(
            ["docker", "inspect", "--format={{json .Mounts}}", container_id],
            capture=True,
        ).stdout
        try:
            mounts = json.loads(mount_data)
            actual_data = next(
                Path(mount["Source"]).resolve()
                for mount in mounts
                if mount.get("Destination") == "/data" and mount.get("Type") == "bind"
            )
        except (ValueError, KeyError, StopIteration, TypeError) as exc:
            raise DeploymentError(
                f"发现既有授权中心容器 {container_id}，但无法确认其 /data 持久挂载；拒绝切换到空数据目录。"
            ) from exc
        files_label = run(
            [
                "docker",
                "inspect",
                '--format={{index .Config.Labels "com.docker.compose.project.config_files"}}',
                container_id,
            ],
            capture=True,
        ).stdout.strip()
        if actual_project != project or actual_data != data_dir.resolve():
            raise DeploymentError(
                f"发现授权中心容器 {container_id} 实际采用 project={actual_project}、data={actual_data}；"
                "请按该容器的 Compose 项目与数据目录显式采用，未初始化或切换身份。"
            )
        if files_label and str(compose_file.resolve()) not in {
            item.strip() for item in files_label.split(",")
        }:
            raise DeploymentError(
                f"发现授权中心容器 {container_id} 使用不同 Compose 文件 {files_label}；"
                "请采用实际文件，未启动新的中心实例。"
            )


def _validate_cert(cert: Path, key: Path, server_name: str, *, certbot_links: bool = False) -> None:
    for path in (cert, key):
        if not path.is_file():
            raise DeploymentError(f"证书文件不存在或不是普通文件：{path}")
        if path.is_symlink():
            target = path.resolve()
            if (
                not certbot_links
                or not path.is_relative_to(Path("/etc/letsencrypt/live"))
                or not target.is_relative_to(Path("/etc/letsencrypt/archive"))
            ):
                raise DeploymentError(f"证书/私钥符号链接不在 Certbot 管理目录中：{path}")
    if key.stat().st_mode & 0o077 or key.stat().st_uid != 0:
        raise DeploymentError(
            "TLS 私钥应由 root 持有并仅允许 root 读取（chown root:root 与 chmod 0600）。"
        )
    cert_text = run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-dates", "-ext", "subjectAltName"],
        capture=True,
    )
    details = cert_text.stdout
    if "notAfter=" not in details:
        raise DeploymentError("无法读取证书到期时间。")
    expiry = re.search(r"notAfter=(.+)", details)
    if expiry is None:
        raise DeploymentError("无法解析证书到期时间。")
    expires = dt.datetime.strptime(expiry.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=dt.timezone.utc
    )
    if expires <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1):
        raise DeploymentError("证书已过期或将在 24 小时内到期，请先续期。")
    if expires <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7):
        print("证书剩余不足 7 天；短效 IP 证书必须保持自动续期正常，已有证书需由管理员维护续期。")
    try:
        ipaddress.ip_address(server_name)
        identity_option = "-checkip"
    except ValueError:
        identity_option = "-checkhost"
    identity = run(
        ["openssl", "x509", "-in", str(cert), "-noout", identity_option, server_name],
        capture=True,
        check=False,
    )
    if identity.returncode:
        raise DeploymentError(f"证书 SAN 不匹配 {server_name}。")
    public_cert = cert.read_text(encoding="ascii")
    if re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", public_cert):
        raise DeploymentError("公共 TLS 证书文件包含私钥材料；拒绝复制到应用容器证书挂载。")
    cert_pub = run(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"], capture=True).stdout
    key_pub = run(["openssl", "pkey", "-in", str(key), "-pubout"], capture=True).stdout
    if cert_pub.strip() != key_pub.strip():
        raise DeploymentError("TLS 证书与私钥不匹配。")


def _server_origin(server: str) -> str:
    return f"https://[{server}]" if ":" in server else f"https://{server}"


def _nginx_content(server_name: str, cert: Path, key: Path, acl: list[str], port: int) -> str:
    allow = "\n".join(f"        allow {cidr};" for cidr in acl)
    return f"""{_NGINX_MARKER}
server {{
    listen 80;
    listen [::]:80;
    server_name {server_name};
    location ^~ /.well-known/acme-challenge/ {{ root /var/www/maitu-acme; default_type text/plain; }}
    location / {{ return 404; }}
}}
server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {server_name};
    ssl_certificate {cert};
    ssl_certificate_key {key};
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 64k;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header X-Frame-Options DENY always;
    location ^~ /api/v1/admin/ {{
{allow}
        deny all;
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 15s;
    }}
    location ^~ /api/v1/instance/ {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 15s;
    }}
    location = /health {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }}
    location / {{
{allow}
        deny all;
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 15s;
    }}
}}
"""


def _install_nginx(server_name: str, cert: Path, key: Path, acl: list[str], port: int) -> Path:
    available = Path("/etc/nginx/sites-available/maitu-license-center")
    enabled = Path("/etc/nginx/sites-enabled/maitu-license-center")
    for path in (available, enabled):
        if path.is_symlink() and path.resolve() != available:
            raise DeploymentError(f"拒绝覆盖非本项目 Nginx 符号链接：{path}")
        if path.exists() and not path.is_symlink():
            previous = path.read_text(encoding="utf-8")
            if _NGINX_MARKER not in previous:
                raise DeploymentError(f"{path} 已存在且无本项目所有权标记；请手工审查，未修改。")
    if enabled.exists() and not enabled.is_symlink():
        raise DeploymentError(f"{enabled} 不是可安全替换的符号链接。")
    old_content = available.read_text(encoding="utf-8") if available.exists() else None
    enabled_created = not enabled.exists()
    atomic_write(available, _nginx_content(server_name, cert, key, acl, port), mode=0o644)
    if enabled_created:
        enabled.symlink_to(available)
    try:
        run(["nginx", "-t"])
    except Exception:
        if old_content is None:
            available.unlink(missing_ok=True)
        else:
            atomic_write(available, old_content, mode=0o644)
        if enabled_created:
            enabled.unlink(missing_ok=True)
        raise
    run(["systemctl", "enable", "--now", "nginx"])
    run(["systemctl", "reload", "nginx"])
    return available


def _certbot(server_name: str, email: str) -> tuple[Path, Path, Path]:
    ensure_tools(["nginx"])
    if not shutil.which("snap"):
        ensure_tools(["snapd"])
    if not shutil.which("snap"):
        raise DeploymentError("Snap 未安装；无法继续使用 Certbot，未切换到其他未核验方案。")
    certbot = Path("/snap/bin/certbot")
    if not certbot.exists():
        if shutil.which("certbot"):
            raise DeploymentError(
                "检测到非 Snap Certbot；请先按 Certbot 官方迁移步骤处理，避免混用定时任务。"
            )
        if not confirm("安装 Snap Certbot（IP 证书要求版本至少 5.4）？"):
            raise DeploymentError("已取消自动签发；请准备现有证书后重新运行。")
        run(["snap", "install", "--classic", "certbot"])
    version = run([str(certbot), "--version"], capture=True).stdout
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match or tuple(int(x or 0) for x in match.groups()) < (5, 4, 0):
        raise DeploymentError(f"Certbot 版本不足 5.4：{version.strip()}。")
    webroot = Path("/var/www/maitu-acme")
    ensure_directory(webroot / ".well-known/acme-challenge", mode=0o755)
    site = Path("/etc/nginx/sites-available/maitu-license-acme")
    site_enabled = Path("/etc/nginx/sites-enabled/maitu-license-acme")
    acme_marker = "# Managed by maitu deploy.sh ACME"
    if site.exists() and acme_marker not in site.read_text(encoding="utf-8"):
        raise DeploymentError(f"{site} 已存在且非本项目所有；不覆盖。")
    atomic_write(
        site,
        f"{acme_marker}\nserver {{ listen 80; listen [::]:80; server_name {server_name}; location ^~ /.well-known/acme-challenge/ {{ root {webroot}; }} location / {{ return 404; }} }}\n",
        mode=0o644,
    )
    if site_enabled.is_symlink() and site_enabled.resolve() != site:
        raise DeploymentError(f"拒绝替换其他 ACME 站点：{site_enabled}")
    if site_enabled.exists() and not site_enabled.is_symlink():
        raise DeploymentError(f"{site_enabled} 是独立配置文件；拒绝覆盖。")
    if not site_enabled.exists():
        site_enabled.symlink_to(site)
    run(["nginx", "-t"])
    run(["systemctl", "enable", "--now", "nginx"])
    run(["systemctl", "reload", "nginx"])
    try:
        ipaddress.ip_address(server_name)
        cert_name = server_name.replace(":", "_")
        args = [
            str(certbot),
            "certonly",
            "--webroot",
            "--webroot-path",
            str(webroot),
            "--preferred-profile",
            "shortlived",
            "--ip-address",
            server_name,
        ]
    except ValueError:
        cert_name = server_name
        args = [
            str(certbot),
            "certonly",
            "--webroot",
            "--webroot-path",
            str(webroot),
            "-d",
            server_name,
        ]
    args.extend(["--email", email, "--agree-tos", "--non-interactive", "--cert-name", cert_name])
    run(args)
    lineage = Path("/etc/letsencrypt/live") / cert_name
    cert, key = lineage / "fullchain.pem", lineage / "privkey.pem"
    _validate_cert(cert, key, server_name, certbot_links=True)
    if site_enabled.is_symlink() and site_enabled.resolve() == site:
        site_enabled.unlink()
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    return cert, key, lineage


def _write_renewal_hook(config: dict[str, object], public_copy: Path, lineage: Path) -> None:
    hook = Path("/etc/letsencrypt/renewal-hooks/deploy/maitu-license-center.sh")
    q = shlex.quote
    content = f"""#!/bin/sh
set -eu
[ "${{RENEWED_LINEAGE:-}}" = {q(str(lineage))} ] || exit 0
install -m 0644 "$RENEWED_LINEAGE/fullchain.pem" {q(str(public_copy) + ".new")}
mv -f {q(str(public_copy) + ".new")} {q(str(public_copy))}
nginx -t
systemctl reload nginx
docker compose --project-name {q(str(config["project_name"]))} --env-file {q(str(config["env_file"]))} -f {q(str(config["compose_files"][0]))} up -d --no-deps --force-recreate license-center
"""
    ensure_directory(hook.parent, mode=0o755)
    atomic_write(hook, content, mode=0o750)
    run(["systemctl", "enable", "--now", "snap.certbot.renew.timer"], check=False)
    timers = run(["systemctl", "is-enabled", "snap.certbot.renew.timer"], capture=True, check=False)
    if timers.returncode != 0:
        run(["systemctl", "list-timers", "--all"], check=False)
        raise DeploymentError("Certbot 自动续期 timer 未启用；检查 Snap Certbot 定时器后重试。")
    run(["/snap/bin/certbot", "renew", "--dry-run"])


def _backup_script(config: dict[str, object], data: Path, backups: Path, recipient: str) -> str:
    q = shlex.quote
    return f"""#!/bin/bash
set -euo pipefail
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
data={q(str(data))}
backups={q(str(backups))}
snapshot="$data/center-backup-$stamp.sqlite3"
archive="$backups/center-$stamp.tar.gpg"
plain="$backups/center-$stamp.tar"
compose=({q("docker")} {q("compose")} --project-name {q(str(config["project_name"]))} --env-file {q(str(config["env_file"]))} -f {q(str(config["compose_files"][0]))})
was_running=0
stopped=0
if "${{compose[@]}}" ps --status running --services | grep -Fxq license-center; then
    was_running=1
fi
cleanup() {{
    status=$?
    trap - EXIT
    set +e
    if [ "$stopped" -eq 1 ]; then
        "${{compose[@]}}" start license-center || status=1
    fi
    rm -f "$snapshot" "$plain"
    if [ "$status" -ne 0 ]; then rm -f "$archive"; fi
    exit "$status"
}}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [ "$was_running" -eq 1 ]; then
    stopped=1
    "${{compose[@]}}" stop license-center
fi
"${{compose[@]}}" run --rm -T license-center backup "/data/$(basename "$snapshot")"
tar -C "$data" -cf "$plain" "$(basename "$snapshot")" keys
gpg --batch --trust-model always --encrypt --recipient {q(recipient)} --output "$archive" "$plain"
if [ "$stopped" -eq 1 ]; then
    "${{compose[@]}}" start license-center
    stopped=0
fi
rm -f "$snapshot" "$plain"
find "$backups" -maxdepth 1 -type f -name 'center-*.tar.gpg' -mtime +30 -delete
"""


def deploy(config_dir: Path) -> None:
    """Deploy or safely adopt the authorization center on the local Ubuntu host."""
    require_host()
    check_time()
    manifest = load_manifest(config_dir, "center")
    print("授权中心部署向导：仅操作本机；不会连接云服务器、修改云安全组或自动更新 Git 源码。")
    if manifest:
        use_saved = confirm(
            "发现本机授权中心部署记录，沿用已记录的源代码、Compose 项目、环境和数据路径？",
            default=True,
        )
    else:
        use_saved = False
    if use_saved:
        source = Path(manifest["source_root"]).absolute()
        env_file = Path(manifest["env_file"]).absolute()
        data = Path(manifest["data_dir"]).absolute()
        project = str(manifest["project_name"])
        compose_file = Path(
            manifest.get(
                "compose_files", [str(source / "services/license-center/docker-compose.yml")]
            )[0]
        ).absolute()
        server = str(manifest["server_name"])
        acl = list(manifest["admin_cidrs"])
        cert = Path(manifest["cert_host"]).absolute()
        key = Path(manifest["key_host"]).absolute()
        mode = str(manifest["certificate_mode"])
        lineage = (
            Path(manifest.get("certbot_lineage", str(cert.parent))) if mode == "certbot" else None
        )
    else:
        adopt = confirm("是否接管已有授权中心 Compose 部署？选择“否”将建立新配置。")
        if adopt:
            source = _path("已有授权中心源代码根目录", ROOT)
            compose_file = _path(
                "该部署实际使用的授权中心 Compose 文件",
                source / "services/license-center/docker-compose.yml",
            )
            env_file = _path(
                "实际 Compose 环境文件（不会输出或重写已有秘密）",
                Path("/etc/maitu-license-center/center.env"),
            )
            project = ask("现有 Compose 项目名", "maitu-license-center")
            prior_env = _parse_env(env_file)
            data = Path(
                prior_env.get("LICENSE_CENTER_DATA", "/srv/maitu-license-center/data")
            ).absolute()
            server = ask("中心 HTTPS 公网 IPv4、IPv6 或域名", "")
            acl = [
                ipaddress.ip_network(x.strip(), strict=False).with_prefixlen
                for x in ask(
                    "管理界面允许访问的运维公网 IP/CIDR（逗号分隔；与客户实例 API 分开）"
                ).split(",")
                if x.strip()
            ]
            if not acl:
                raise DeploymentError("管理 ACL 不能为空；需要明确允许的运维来源网段。")
            cert = Path(
                prior_env.get(
                    "LICENSE_CENTER_TLS_CERT_HOST", "/etc/maitu-license-center/fullchain.pem"
                )
            ).absolute()
            key = _path(
                "现有 TLS 私钥路径（只保留在宿主机）",
                Path("/etc/ssl/maitu-license-center/privkey.pem"),
            )
            mode = "existing"
            lineage = None
        else:
            source, env_file, data = (
                ROOT,
                Path("/etc/maitu-license-center/center.env"),
                _DEFAULT_ROOT / "data",
            )
            compose_file = source / "services/license-center/docker-compose.yml"
            project = ask("新 Compose 项目名", "maitu-license-center")
            server = ask("中心 HTTPS 公网 IPv4、IPv6 或域名", "")
            acl = [
                ipaddress.ip_network(x.strip(), strict=False).with_prefixlen
                for x in ask(
                    "管理界面允许访问的运维公网 IP/CIDR（逗号分隔；与客户实例 API 分开）"
                ).split(",")
                if x.strip()
            ]
            if not acl:
                raise DeploymentError("管理 ACL 不能为空；需要明确允许的运维来源网段。")
            env_file = _path("新环境文件位置", env_file)
            data = _path("授权中心持久数据目录（现有数据需采用其真实目录）", data)
            mode = ask(
                "证书方式：existing（已有可信证书）或 certbot（自动申请）", "certbot"
            ).lower()
            cert, key, lineage = Path(), Path(), None
    if any(ipaddress.ip_network(value).prefixlen == 0 for value in acl):
        raise DeploymentError("管理 ACL 不允许使用全网网段 0.0.0.0/0 或 ::/0。")
    if env_file.is_symlink():
        raise DeploymentError(f"拒绝使用符号链接环境文件：{env_file}")
    preflight_env = _parse_env(env_file)
    _validate_storage_overrides(preflight_env)
    _is_initialized(data)
    for name in ("LICENSE_CENTER_DATA", "LICENSE_CENTER_TLS_CERT_HOST"):
        if name in preflight_env and not Path(preflight_env[name]).is_absolute():
            raise DeploymentError(
                f"现有环境变量 {name} 必须是绝对路径；拒绝猜测其 Compose 解析位置。"
            )
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise DeploymentError(
            "Compose 项目名仅可包含小写字母、数字、连字符和下划线，且须以字母或数字开头。"
        )
    if not compose_file.is_file() or compose_file.is_symlink():
        raise DeploymentError(f"授权中心 Compose 文件不存在或是符号链接：{compose_file}")
    if mode not in {"existing", "certbot"}:
        raise DeploymentError("证书方式只能是 existing 或 certbot。")
    try:
        ipaddress.ip_address(server)
        if "%" in server:
            raise DeploymentError("不支持带接口作用域的 IPv6 地址；请使用公网 IPv6 地址本身。")
        server = str(ipaddress.ip_address(server))
    except ValueError:
        if not re.fullmatch(
            r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*",
            server,
        ):
            raise DeploymentError("请输入有效公网 IP 或 DNS 域名。") from None
        server = server.lower()
    if mode == "certbot" and env_file.exists() and not use_saved:
        raise DeploymentError(
            "现有环境文件必须明确采用其证书与数据路径；不能在其上直接签发新证书。"
        )
    recipient = ask(
        "加密一致性备份必需：备份接收方完整 GPG 公钥指纹（解密私钥不得存放在此服务器；每日备份会短暂停止中心以保证 SQLite 与 keys/ 一致）",
        str(manifest["backup_recipient"])
        if manifest and manifest.get("backup_recipient")
        else None,
    )
    if not re.fullmatch(r"[A-Fa-f0-9]{40,64}", recipient):
        raise DeploymentError("GPG 接收方必须是核验过的完整十六进制指纹。")
    if mode == "existing" and not use_saved and not adopt:
        cert = _path("已有 TLS 完整证书链文件", Path("/etc/ssl/maitu-license-center/fullchain.pem"))
        key = _path(
            "已有 TLS 私钥文件（仅宿主机使用）", Path("/etc/ssl/maitu-license-center/privkey.pem")
        )
    ensure_tools(["docker", "nginx", "openssl", "gpg"])
    _assert_existing_center(project, compose_file, data)
    if key.resolve().is_relative_to(data.resolve()):
        raise DeploymentError(
            "TLS 私钥不能放在中心数据挂载目录内；请只保留在宿主机受保护的独立目录。"
        )
    owner_needed = _needs_owner(data)
    network_result = run(
        ["docker", "network", "inspect", "maitu-license-center"], capture=True, check=False
    )
    if network_result.returncode:
        run(["docker", "network", "create", "--driver", "bridge", "maitu-license-center"])
    gateway = _gateway("maitu-license-center")
    available_key = run(
        ["gpg", "--list-keys", "--with-fingerprint", recipient], capture=True, check=False
    )
    if available_key.returncode:
        raise DeploymentError(
            "未找到备份接收方公钥。请在独立运维电脑生成 GPG 密钥，只将公钥导入本服务器，再运行向导。"
        )
    private_key = run(
        ["gpg", "--list-secret-keys", "--with-colons", recipient], capture=True, check=False
    )
    if any(
        line.startswith("sec:") or line.startswith("ssb:")
        for line in private_key.stdout.splitlines()
    ):
        raise DeploymentError("服务器 GPG keyring 含备份解密私钥；请仅导入备份接收方公钥。")
    if mode == "certbot" and use_saved:
        _validate_cert(cert, key, server, certbot_links=True)
    elif mode == "certbot":
        if not confirm(
            "确认公网 80/TCP 已允许 CA HTTP-01 校验，且此 Nginx 站点可独占该 server_name？"
        ):
            raise DeploymentError("未确认 HTTP-01 网络前提，未申请证书。")
        cert, key, lineage = _certbot(server, ask("用于证书通知的电子邮箱"))
    else:
        _validate_cert(cert, key, server)
    if env_file.is_symlink():
        raise DeploymentError(f"拒绝使用符号链接环境文件：{env_file}")
    if not env_file.exists():
        cert_host = Path("/etc/maitu-license-center/fullchain.pem")
        ensure_directory(cert_host.parent, mode=0o700)
        if cert_host.is_symlink():
            raise DeploymentError(f"拒绝覆盖符号链接证书文件：{cert_host}")
        cert_content = cert.read_text(encoding="ascii")
        if (
            cert_host.exists()
            and cert_host.read_text(encoding="ascii") != cert_content
            and not confirm(f"将备份并替换已有项目证书文件 {cert_host}？")
        ):
            raise DeploymentError("未获准替换证书文件。")
        atomic_write(cert_host, cert_content, mode=0o644)
        cert = cert_host
        config = _compose_config(source, env_file, project, compose_file)
        port = validate_port(ask("宿主机仅回环绑定的中心端口", "8090"))
        values = {
            "LICENSE_CENTER_DATA": str(data),
            "LICENSE_CENTER_PORT": str(port),
            "LICENSE_CENTER_TLS_CERT_HOST": str(cert),
            "LICENSE_CENTER_TRUSTED_PROXY_IPS": gateway,
            "LICENSE_CENTER_TIMEZONE": "Asia/Shanghai",
            "MAITU_CENTER_CLIENT_TOKEN": secrets.token_urlsafe(48),
        }
        with configuration_transaction([env_file]):
            atomic_write(env_file, env_text(values), mode=0o600)
            compose(config, "config", "--quiet")
    else:
        ensure_directory(env_file.parent, mode=0o700)
        existing = _parse_env(env_file)
        client_token = existing.get("MAITU_CENTER_CLIENT_TOKEN", "")
        if client_token and len(client_token) < 32:
            raise DeploymentError(
                "MAITU_CENTER_CLIENT_TOKEN 必须至少 32 字符；请通过可信渠道更换。"
            )
        if not client_token:
            client_token = secrets.token_urlsafe(48)
            with configuration_transaction([env_file]):
                _set_env_value(env_file, "MAITU_CENTER_CLIENT_TOKEN", client_token)
        actual_data = Path(existing.get("LICENSE_CENTER_DATA", str(data))).absolute()
        if actual_data != data:
            raise DeploymentError(
                "现有环境文件的数据目录与选择的路径不一致；请准确采用实际数据目录。"
            )
        config = _compose_config(source, env_file, project, compose_file)
        if existing.get("LICENSE_CENTER_TRUSTED_PROXY_IPS") != gateway:
            print(
                f"现有可信代理配置与当前 Docker 网关 {gateway} 不同；将在备份原环境文件后更新为实际网关。"
            )
            if not confirm("更新现有环境文件中的 LICENSE_CENTER_TRUSTED_PROXY_IPS？"):
                raise DeploymentError("可信代理地址需匹配实际 Docker 网关。")
            with configuration_transaction([env_file]):
                _set_env_value(env_file, "LICENSE_CENTER_TRUSTED_PROXY_IPS", gateway)
                compose(config, "config", "--quiet")
            existing["LICENSE_CENTER_TRUSTED_PROXY_IPS"] = gateway
        else:
            with configuration_transaction([env_file]):
                compose(config, "config", "--quiet")
            existing["LICENSE_CENTER_TRUSTED_PROXY_IPS"] = gateway
        configured_cert = Path(existing.get("LICENSE_CENTER_TLS_CERT_HOST", "")).absolute()
        if configured_cert != cert:
            raise DeploymentError(
                "选择的 TLS 公共证书路径与实际 Compose 环境文件不一致；拒绝改变现有部署。"
            )
    initialized = _is_initialized(data)
    if not initialized and data.exists() and any(data.iterdir()):
        raise DeploymentError("新中心数据目录非空；拒绝初始化，先人工确认数据归属。")
    _protect_data_directory(data)
    if initialized:
        print("检测到数据库/迁移/密钥；保留原数据，不重建已有 owner 或签发身份。")
    _validate_cert(cert, key, server, certbot_links=mode == "certbot")
    environment = _parse_env(env_file)
    app_port = validate_port(environment.get("LICENSE_CENTER_PORT", "8090"))
    origin = _server_origin(server)
    server_url = origin + "/"
    health_url = origin + "/health"
    certificate_hash = hashlib.sha256(cert.read_bytes()).hexdigest()
    certificate_changed = (
        initialized and (manifest or {}).get("certificate_sha256") != certificate_hash
    )
    save_manifest(
        config_dir,
        "center",
        {
            **(manifest or {}),
            "version": 1,
            "role": "center",
            "source_root": str(source),
            "compose_files": [str(compose_file)],
            "env_file": str(env_file),
            "data_dir": str(data),
            "project_name": project,
            "server_name": server,
            "server_url": server_url,
            "health_url": health_url,
            "admin_cidrs": acl,
            "cert_host": str(cert),
            "certificate_sha256": certificate_hash,
            "key_host": str(key),
            "certificate_mode": mode,
            "certbot_lineage": str(lineage) if lineage else None,
        },
    )
    _install_nginx(server, cert, key, acl, app_port)
    if confirm("构建当前交付源码的授权中心镜像？（重入可保留原镜像）", default=manifest is None):
        compose(config, "build", "license-center", timeout=3600)
    startup = ["up", "-d"]
    if certificate_changed:
        startup.append("--force-recreate")
    compose(config, *startup, "license-center", timeout=3600)
    if mode == "certbot":
        # Certbot's lineage remains private-key material on host; only fullchain is copied to Docker.
        public_copy = cert
        if lineage is None:
            raise DeploymentError("证书续期谱系信息缺失，未安装无效续期钩子。")
        _write_renewal_hook(config, public_copy, lineage)
    if owner_needed:
        print(
            "现在通过 Compose 交互初始化唯一的 owner，并现场完成 MFA；请将恢复材料保存在受控密码库。"
        )
        if not confirm("开始首次 owner 初始化？", default=True):
            raise DeploymentError(
                "中心服务已启动但 owner 尚未初始化；再次运行可继续，数据库不会被重置。"
            )
        compose(config, "run", "--rm", "license-center", "init", timeout=3600)
    ca_value = ask(
        "用于客户端导出的可选私有 CA 根证书路径（输入 system 使用系统信任）",
        str(manifest["ca_file"]) if manifest and manifest.get("ca_file") else "system",
    )
    ca_file = None if ca_value == "system" else Path(ca_value).expanduser().absolute()
    volumes: list[str] = []
    ca_option: list[str] = []
    if ca_file is not None:
        if ca_file.is_symlink() or not ca_file.is_file():
            raise DeploymentError("指定的 CA 证书必须是普通文件；未导出连接配置。")
        volumes.extend(["-v", f"{ca_file}:/run/maitu-center-ca.pem:ro"])
        ca_option = ["--ca-file", "/run/maitu-center-ca.pem"]
    export_path = (
        Path(
            ask(
                "公开连接配置导出路径",
                str(manifest.get("connection_file", data / "connection.json"))
                if manifest
                else str(data / "connection.json"),
            )
        )
        .expanduser()
        .absolute()
    )
    if export_path.parent != data.absolute() or export_path.name in {"", ".", ".."}:
        raise DeploymentError("导出文件必须直接保存在授权中心持久数据目录中，以便容器安全写入。")
    allow_replace = export_path.exists() and confirm(
        f"若已有导出文件内容不同，允许替换 {export_path}？"
    )
    export_args = [
        "export-connection",
        f"/data/{export_path.name}",
        "--server-url",
        server_url,
        *ca_option,
    ]
    if allow_replace:
        export_args.append("--confirm")
    compose(config, "run", "--rm", "-T", *volumes, "license-center", *export_args)
    ca_for_health = ca_file
    if ca_for_health is None:
        ca_health_value = ask(
            "用于本机 HTTPS 健康检查的私有 CA 根证书路径（输入 system 使用系统信任）", "system"
        )
        ca_for_health = (
            None if ca_health_value == "system" else Path(ca_health_value).expanduser().absolute()
        )
        if ca_for_health is not None and (
            ca_for_health.is_symlink() or not ca_for_health.is_file()
        ):
            raise DeploymentError("指定的 HTTPS 健康检查 CA 必须是普通文件。")
    health = wait_http(health_url, ca_file=ca_for_health)
    if health.get("status") != "ok":
        raise DeploymentError("授权中心 HTTPS 健康接口未报告 status=ok。")
    saved_manifest = load_manifest(config_dir, "center")
    if saved_manifest is not None:
        saved_manifest["ca_file"] = str(ca_for_health) if ca_for_health else None
        saved_manifest["connection_file"] = str(export_path)
        saved_manifest["backup_recipient"] = recipient
        save_manifest(config_dir, "center", saved_manifest)
    backup_dir = _DEFAULT_ROOT / "backups"
    ensure_directory(backup_dir, mode=0o700)
    backup_script = Path("/usr/local/sbin/maitu-license-center-backup")
    atomic_write(backup_script, _backup_script(config, data, backup_dir, recipient), mode=0o750)
    service = Path("/etc/systemd/system/maitu-license-center-backup.service")
    timer = Path("/etc/systemd/system/maitu-license-center-backup.timer")
    atomic_write(
        service,
        "[Unit]\nDescription=Encrypted consistent Maitu license center backup\nRequires=docker.service\nAfter=docker.service network-online.target\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/maitu-license-center-backup\n",
        mode=0o644,
    )
    atomic_write(
        timer,
        "[Unit]\nDescription=Daily encrypted license center backup\n[Timer]\nOnCalendar=daily\nPersistent=true\n[Install]\nWantedBy=timers.target\n",
        mode=0o644,
    )
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "maitu-license-center-backup.timer"])
    run(["systemctl", "start", "maitu-license-center-backup.service"])
    if wait_http(health_url, ca_file=ca_for_health).get("status") != "ok":
        raise DeploymentError("首次备份后中心尚未恢复健康；请使用 check，不能报告部署就绪。")
    http_rule = (
        "入站 80/TCP 需向 CA 开放用于 HTTP-01 校验"
        if mode == "certbot"
        else "现有证书方式不要求开放 80/TCP"
    )
    print(
        f"云安全组需由管理员人工核对：入站 443/TCP 允许客户主机与运维出口；{http_rule}；SSH 仅运维来源；宿主机应用端口不开放公网。客户办公室需出站 HTTPS 访问中心，中心安装/续期需出站访问软件源、DNS 与 CA。脚本不会读取或验证云安全组。"
    )
    print(f"管理地址：{server_url}（管理 API 仅允许 {', '.join(acl)}）")
    print(f"客户实例 API：{server_url}；不受管理 ACL 限制。")
    print(
        f"公开连接配置已导出：{export_path}（仅含 HTTPS 地址、验签公钥和可选 CA 证书，不含秘密）。"
    )
    for kid, fingerprint in sorted(key_fingerprints(load_bundle(export_path)).items()):
        print(f"中心公钥 {kid}: SHA256:{fingerprint}；请通过可信运维通道与平台导入提示核对。")
    if mode == "existing":
        print(
            "现有证书续期由证书签发方/管理员负责；更新后需安全复制新的公共 fullchain、重载 Nginx 并重建中心容器；TLS 私钥始终留在宿主机。"
        )
    print(
        "请从外部运维网络实测管理 ACL 与客户实例 API 可达性，并将加密备份复制到异地；不要把签发私钥或 TLS 私钥交付客户。"
    )
