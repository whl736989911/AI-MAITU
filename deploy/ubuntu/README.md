# Ubuntu 部署（完整源码包）

本目录把 `maitu-source-*.zip` 部署到一台 Ubuntu 主机。所有脚本都在**解压后的源码根目录**运行，数据默认放在 `/var/lib/maitu`，服务以独立系统用户 `maitu` 运行，不把平台跑在 root 下。

## 一、原生源码 + systemd vs Docker Compose

### 推荐：原生源码 + systemd

适合这份完整源码 ZIP、内网单机、需要宿主工具和真实浏览器/桌面能力的场景。

- 主机只安装一次系统依赖，`uv` 创建独立 Python 3.12 虚拟环境，systemd 以 `maitu` 用户运行。
- Agent 的终端、浏览器、本地文件能力直接使用宿主环境；排障、备份和迁移路径短。
- 升级时只需“停服 → 备份数据 → 解压新源码 → 重新同步依赖 → 启动”；源码和 venv 都在磁盘上，容易审计。
- 代价：需要 `sudo`、systemd、apt 构建依赖；依赖/系统库与主机绑定；升级时源目录和 venv 要同步更新。

### Docker Compose

适合已经标准化使用 Docker、希望镜像可复制/回滚、不想在主机装 Python/Node/Playwright 依赖的场景。

- 镜像内置 Python、前端产物、Chromium、OCR 等依赖；主机只需要 Docker Engine + Compose v2。
- 容器以 UID/GID 1000 运行，默认丢弃全部 capabilities、`no-new-privileges`，**不挂载宿主 Docker socket**；数据通过 bind mount 持久化。
- 升级是重建镜像并替换容器；回滚镜像 tag 比较直接。
- 代价：构建需要网络和磁盘；Agent 的“终端/执行”发生在容器内，宿主桌面、宿主 Docker 执行等能力默认不可用；Ubuntu 24.04 的 AppArmor 用户命名空间限制要额外加载 `docker/security/apparmor-maitu`；数据备份/权限（UID 1000）需要单独管理。

**选择建议**：这台机器是内网单机、没有既定 Docker 平台、需要完整宿主能力 → 用原生；已有 Docker 运维体系、更看重可复制交付和隔离边界 → 用 Docker。两者不要混用同一个数据目录/服务端口。

## 二、通用准备

- Ubuntu 22.04 / 24.04 / 26.04，amd64 或 arm64；建议 ≥4 vCPU、8 GB RAM、20 GB 可用磁盘（模型/知识库另算）。
- 出网访问 PyPI、uv 下载站、npm（仅重建前端时需要）、所选的 LLM API；内网机器请先配置代理/镜像。
- 一个内部 IP 上的端口，例如 `8088`；确认云安全组/防火墙放行。**不要直接暴露公网 8088**，公网场景先做 TLS 反代。
- 先验证源码包（两段都要做）：

```bash
sha256sum --check maitu-source-*.zip.sha256
sudo mkdir -p /opt/maitu
sudo unzip -q maitu-source-*.zip -d /opt/maitu
test -f /opt/maitu/pyproject.toml
```

ZIP 根目录直接是源码根，没有顶层目录。解压后包含 `SOURCE-PACKAGE.json`，脚本会按其中 SHA-256 复核文件。

## 三、原生部署

### 1. 安装

```bash
cd /opt/maitu
sudo bash deploy/ubuntu/install-source.sh --yes --port 8088
```

默认行为：

- 检查 Ubuntu 版本、源码清单和内置前端产物；
- apt 安装构建依赖（`--no-apt` 可跳过，适合已有镜像）；
- 安装 `uv`，把 Python 3.12 安装到 `/opt/maitu-python`，项目 venv 为 `/opt/maitu/.venv`；
- 创建系统用户/组 `maitu`，数据目录 `/var/lib/maitu`（0700）；
- 按 `uv.lock` 执行 `uv sync --frozen --inexact --no-dev --extra office --extra knowledge-ocr`；
- 写 `/etc/systemd/system/maitu.service`（`User=maitu`），启动并等待 `/api/health` 通过。

常用参数：

```bash
# 数据目录、端口、绑定地址
sudo bash deploy/ubuntu/install-source.sh --yes \
  --data-home /data/maitu --port 8088 --host 0.0.0.0

# 额外能力：本地向量 / 浏览器 / 桌面（不要默认全开）
sudo bash deploy/ubuntu/install-source.sh --yes --extras office,knowledge-ocr,local-embedding

# 如果 ZIP 内前端产物缺失，或你想用 Node 22 重新构建
sudo bash deploy/ubuntu/install-source.sh --yes --build-frontend --extras office,knowledge-ocr

# 检查计划但不改系统
sudo bash deploy/ubuntu/install-source.sh --dry-run
```

说明：

- 默认使用 ZIP 内已构建的 `src/octop/dashboard/`，因此首次安装不需要 Node；`--build-frontend` 才会安装 Node 22 并执行 `npm ci && npm run build`。
- 服务以 `maitu` 用户运行；源码归 root、组可读，数据目录归 `maitu`。不要把源码目录改为 `maitu` 可写，除非你明确接受 Agent 能修改自身代码。
- 重新对同一路径执行安装会拒绝覆盖已有的 `.venv`/service；修复重装用 `--force`，升级走下一节的 upgrade 脚本。

### 2. 防火墙与访问

```bash
sudo ufw allow 8088/tcp       # 如果启用了 ufw；安装时也可加 --open-firewall
hostname -I                   # 找到内网 IP
```

浏览器打开 `http://<ubuntu-ip>:8088/`，首次进入会跳转 setup 向导：创建管理员、配置数据库（默认 SQLite 即可）、完成初始化。然后在“设置 → 模型”中配置 LLM 提供商/密钥。平台默认在 8088 提供 HTTP；公网部署请在前面加 Nginx/Caddy TLS 反向代理，或使用平台自带 TLS 配置。

### 3. 服务与日志

```bash
sudo systemctl status maitu.service
sudo systemctl restart maitu.service
sudo journalctl -u maitu.service -f
sudo ls -la /var/lib/maitu          # config.json、maitu.db、logs、agents、security
```

健康检查：`curl -fsS http://127.0.0.1:8088/api/health`。

### 4. 备份

数据目录是 `/var/lib/maitu`（如果 `--data-home` 改了就用实际路径）。备份时建议短停服务：

```bash
sudo systemctl stop maitu.service
sudo tar --numeric-owner -C /var/lib -czf /var/backups/maitu-$(date +%F-%H%M).tar.gz maitu
sudo systemctl start maitu.service
```

数据库默认是 `maitu.db`；如使用 PostgreSQL 或外置 license 身份目录，还需要按 `docs/configuration.md` / `docs/instance-license-operations.md` 单独备份。不要把数据备份放在 `/opt/maitu` 内，升级时目录会被移动。

## 四、原生升级

1. 核对新 ZIP 的 SHA-256，解压到**新目录**，不要覆盖旧目录：

```bash
sudo mkdir -p /opt/maitu.new
sudo unzip -q maitu-source-<new>.zip -d /opt/maitu.new
test -f /opt/maitu.new/pyproject.toml
```

2. 执行升级脚本（从新源码目录运行）：

```bash
cd /opt/maitu.new
sudo bash deploy/ubuntu/upgrade-source.sh \
  --source /opt/maitu.new \
  --install-dir /opt/maitu \
  --extras office,knowledge-ocr \
  --yes
```

脚本会：停止 `maitu.service` → 备份 `/var/lib/maitu` 到 `/var/backups/maitu/` → 旧源码移到 `/opt/maitu.prev-<时间>` → 新源码放到 `/opt/maitu` → 重新 `uv sync` → 启动并检查健康。失败会恢复旧源码并重启旧服务，保留备份和新源码 `.failed-*` 目录。

3. 成功后在浏览器核对原管理员、用户、Agent、知识库和会话仍在。保留旧源码和备份，直到确认新版本稳定。

**回滚边界**：脚本的自动回滚只回滚源码；新版本如果已经迁移数据库，直接启动旧代码可能失败。必须完整回滚时，先停服，再用备份恢复 `/var/lib/maitu`（和任何外置数据库/授权身份目录），然后恢复旧源码。不要只切回旧目录就认为完成了数据回滚。

## 五、Docker Compose 部署

前提：Docker Engine + Compose v2 已安装（`docker compose version` 正常），且主机有 `python3`
用于校验 `SOURCE-PACKAGE.json`（没有时加 `--skip-verify`）。容器运行 UID/GID 1000；本脚本不会安装
Docker、不会挂载宿主 Docker socket。

```bash
sudo mkdir -p /opt/maitu
sudo unzip -q maitu-source-*.zip -d /opt/maitu

# 首次安装（数据目录默认 /var/lib/maitu-docker）
sudo bash /opt/maitu/deploy/ubuntu/install-docker.sh --port 8088
```

- 浏览器打开 `http://<ubuntu-ip>:8088/`。
- 未设置 `OCTOP_DEFAULT_PASSWORD` 时镜像会生成随机管理员密码，写入宿主机数据目录的 `credential.txt`（`/var/lib/maitu-docker/credential.txt`）；登录后立即修改。
- 升级：拉取/解压新源码后，在新目录重新执行 `install-docker.sh`（或手工 `docker compose build && up -d`）；先备份数据目录和数据库。
- Ubuntu 24.04+ 的 AppArmor 限制：先加载专用策略，再加 `--apparmor` 安装；不要全局关闭 AppArmor/userns。

常用运维：

```bash
cd /opt/maitu
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs -f maitu
docker compose -f docker/docker-compose.yml down      # 保留数据目录
```

Docker 默认策略和额外 compose overlay（Postgres/品牌/移动端/托管）见 [`docker/README_CN.md`](../../docker/README_CN.md)。默认不挂载宿主 Docker socket；只有可信运维显式叠加 `docker-compose.docker-exec.yml` 才会产生高权限边界。

## 六、验收与排障

安装后至少核对：

```bash
curl -fsS http://127.0.0.1:8088/api/health
sudo systemctl is-active maitu.service
sudo journalctl -u maitu.service -n 50 --no-pager
```

常见问题：

| 现象 | 排查 |
|---|---|
| 8088 无法访问 | 云安全组/ufw 是否放行；服务是否 `active`；`ss -lntp` 是否有 8088；`--host` 是否为 0.0.0.0 |
| 服务启动失败 | `journalctl -u maitu.service -n 100`；检查 `/opt/maitu/.venv/bin/maitu` 是否存在、数据目录权限是否 `maitu` 所有 |
| `uv sync` 网络失败 | 配置 `UV_INDEX_URL`/代理；内网使用镜像源；检查 `uv.lock` 是否被改过 |
| 前端 404/白屏 | 确认 ZIP 内有 `src/octop/dashboard/index.html`；否则用 `--build-frontend` 重建 |
| Agent 执行/浏览器失败 | 原生安装需要宿主 user namespace/bubblewrap 支持；Ubuntu 24.04 检查 AppArmor/userns；Docker 方式按 `docker/README_CN.md` 加载策略，不要关闭沙箱 |
| 实例授权锁定 | 按 `docs/instance-license-operations.md` 配置可信公钥/实例授权；登录页和授权管理仍可用 |
| 端口冲突 | 改 `--port` 重装；确认旧 `octop.service`/旧进程已停止 |

## 七、安全边界

- 平台默认内网 HTTP，不要在没有 TLS/访问控制的情况下暴露公网；公网请配置反代、证书和防火墙。
- 原生服务以 `maitu` 用户运行；不要为了省事改成 root。Agent 的终端/文件能力继承服务用户权限，必要时再叠加宿主级隔离（bubblewrap/AppArmor）。
- 源码目录 root 所有、组/其他只读；`/var/lib/maitu` 0700 且只归 `maitu`。不要把 `.env`、密钥、数据库放进源码目录或源码 ZIP。
- Docker 默认不把宿主 Docker socket 给业务容器；`docker-compose.docker-exec.yml` 是显式高权限选择，不要交给不可信 Agent 使用。
- 安装/升级脚本不会创建管理员密码；请通过浏览器 setup 或 Docker 生成的 `credential.txt` 获取初始凭据。
