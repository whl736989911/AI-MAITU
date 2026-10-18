# 麦途智造 Docker 部署

---

本目录包含麦途智造的 Docker 构建与部署相关文件。运维名称统一为 `maitu`；内部 Python 包、`octop` CLI、`OCTOP_*` 环境变量和容器内 `/data/.octop` 保持不变，不迁移应用数据格式。

### 文件说明

| 文件 | 说明 |
|------|------|
| `Dockerfile` | 多阶段镜像定义（构建上下文为仓库根目录） |
| `../.dockerignore` | 构建上下文忽略规则（Podman 与 BuildKit 均适用） |
| `docker_build.sh` | 从源码构建镜像（默认开启 BuildKit 缓存） |
| `docker-compose.yml` | 本地开发 / 自托管一键启动 |
| `docker-compose.postgres.yml` | 仅 PostgreSQL（+ pgvector）开发/测试库；扩展在 `postgres/init-vector.sql` |
| `postgres/init-vector.sql` | 实例级 `CREATE EXTENSION vector`（initdb.d；**不**进 Octop 迁移） |
| `docker-entrypoint.sh` | 容器入口：首次初始化数据库并启动服务 |
| `security/seccomp-bwrap.json` | 基于 Moby 默认拒绝策略，允许非特权用户命名空间中的沙箱启动；须与丢弃全部外层 capabilities 一起使用 |
| `security/apparmor-maitu`、`docker-compose.apparmor.yml` | Ubuntu 等 AppArmor 主机的专用用户命名空间策略与叠加配置 |
| `docker-compose.docker-exec.yml` | 显式配置 Docker 执行后端时可选；会授予 MAITU 服务宿主机 Docker API 权限，默认部署不使用 |

### 快速开始

**方式一：Compose（推荐）**

源码目录可命名为 `/opt/maitu`，在该仓库根目录执行：

```bash
export OCTOP_DATA="${OCTOP_DATA:-$HOME/.maitu}"
# 仅初始化新目录；已有实例先按下文备份并检查 UID/GID。
sudo install -d -m 0700 -o 1000 -g 1000 "$OCTOP_DATA"
docker compose -f docker/docker-compose.yml up -d --build
```

访问 `http://localhost:8088`。未设置 `OCTOP_DEFAULT_PASSWORD` 时，首次初始化会自动生成随机密码并写入 `/data/.octop/credential.txt`；设置了则按设置值初始化（须 ≥8 位且同时包含字母和数字；被应用密码策略拒绝的常见弱密码会自动回退为随机密码）。首次登录后请立即修改密码。
当前镜像默认启用实例授权门禁；缺少预置公钥或有效授权时，登录、激活和授权管理仍可使用，但业务保持锁定。客户交付应叠加 `docker-compose.managed.yml`，将信任公钥、实例身份和更新队列挂载到业务数据目录之外。不要把签发私钥放进客户镜像、工作区或业务备份。

部署前统一平台名称可使用已有 `brand.config.json`：

```bash
export MAITU_BRAND_CONFIG_HOST_FILE=/etc/maitu/brand.config.json
docker compose -f docker/docker-compose.yml \
  -f docker/docker-compose.branding.yml up -d --build
```

修改文件中的 `name`（平台名）、`full_name`（公司名）、`short_name`（简称），重启后应用到 Agent 运行期身份及服务提示；管理页已保存的品牌设置优先于该文件，重置后才回到部署默认值。图片仍按现有品牌配置单独设置。真实 `octop` 命令及内部协议标识不替换，详见 [品牌配置](../docs/configuration.md#deployment-brand-and-runtime-identity)。


**方式二：构建脚本**

```bash
bash docker/docker_build.sh
docker run -d \
  --name maitu \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --security-opt "seccomp=$(pwd)/docker/security/seccomp-bwrap.json" \
  -p 8088:8088 \
  -v maitu-data:/data/.octop \
  -e HOME=/data \
  maitu:latest
```

### 执行沙箱与浏览器（Linux）

镜像以 UID/GID `1000:1000` 运行，Python 虚拟环境位于 `/usr/local/maitu-venv`，可被执行沙箱只读访问；预装 `python-docx`、PyMuPDF 与 Chromium。Compose 丢弃全部外层 capabilities，使用 `no-new-privileges` 和默认拒绝的 seccomp 策略，不使用 `privileged`、`SYS_ADMIN`、`seccomp=unconfined` 或浏览器 `--no-sandbox`。不要只换镜像而遗漏 Compose 策略。
默认 Compose **不会**挂载宿主机 Docker socket。`docker-compose.docker-exec.yml` 仅供可信运维显式配置 Docker 执行后端时叠加；启用后 MAITU 服务获得高信任级别的 Docker API 权限。不要将 socket 暴露给业务 Agent 启动的作业/容器。

Ubuntu 24.04 等启用 AppArmor 用户命名空间限制的主机，先由运维加载专用策略，再叠加配置：

```bash
sudo install -m 0644 docker/security/apparmor-maitu /etc/apparmor.d/maitu-sandbox
sudo apparmor_parser -r /etc/apparmor.d/maitu-sandbox
docker compose -f docker/docker-compose.yml \
  -f docker/docker-compose.apparmor.yml up -d --build
```

授权托管、品牌等 overlay 继续叠加使用；宿主更新器的 Compose 文件列表也必须包含同一 AppArmor overlay。直接 `docker run` 时须同时加 `--security-opt apparmor=maitu-sandbox`。未加载策略不可直接引用此名称。不要全局关闭 AppArmor 或非特权用户命名空间限制来绕过错误。

宿主 `user.max_user_namespaces` 必须大于零；提供 `kernel.unprivileged_userns_clone` 的发行版还需允许非特权用户命名空间。若宿主或上层容器完全禁止该能力，须由宿主管理员处理，容器配置不能越权开启。执行工具就绪检查会实际启动沙箱，不再以“已安装 bwrap”冒充可执行；命名空间启动失败会保留具体原因，不能靠关闭沙箱掩盖故障。

浏览器固定使用镜像内 `/opt/maitu-browser` 的 Chromium，并设置 `BROWSER_USE_NO_SANDBOX=0`：无法启用沙箱时应明确失败，不自动关闭浏览器沙箱。不要给不明 `chrome-sandbox` 文件设置 SUID。平台环境变量管理不允许覆盖这两个受运维管理的浏览器路径/沙箱设置。

Ubuntu 原生源码安装（非 Docker）请参阅[宿主安装指南](../scripts/README.md#原生沙箱)及[配置参考](../docs/configuration.md#execution-sandbox-native-installs)。

「支持输入图片」及 `input` 只提供能力提示，不是硬性门禁。遇到图片任务，保留当前选定且可用的模型，先按已配置的 OpenAI 或 Anthropic 协议发送真实图片；协议请求失败后，再使用同一提供商、同一模型尝试另一协议，任一种成功即返回结果，不会静默删除图片。两种协议均失败时说明当前无法处理并保留具体原因，不把鉴权、限流或网络错误永久记成模型“不支持图片”。中途已开始流式输出的失败不重放，避免重复回答；DOCX/PDF 本身仍需解析或导出图片，不把二进制当文字发送。

DeepSeek 同样以具体端点的真实调用为准，参见[官方视觉输入说明](https://api-docs.deepseek.com/guides/vision/)及 [V4.1 Flash 兼容别名说明](https://api-docs.deepseek.com/news/news260910/)。已验证的官方 `deepseek-v4-flash` 可将 `input` 标为 `["text", "image"]` 供界面提示，但漏标不再阻止真实识图，无需为此切换供应商或默认模型；第三方同名模型不能仅凭名称判定能力。

### 国内镜像加速

构建时可通过环境变量加速依赖下载：

```bash
PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/ \
APT_MIRROR=mirrors.cloud.tencent.com \
bash docker/docker_build.sh
```

### 常用环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HOME` | `/data` | 必须为 `/data`，数据目录映射到 `~/.octop` |
| `OCTOP_DATA` | `~/.maitu` | Compose 的宿主机数据目录；已有部署必须指定原数据目录 |
| `OCTOP_PORT` | `8088` | HTTP 服务端口 |
| `OCTOP_DEFAULT_PASSWORD` | _(未设置)_ | 首次管理员密码（≥8 位，字母+数字）。未设置时自动生成随机密码并写入 `credential.txt` |
| `OCTOP_ADMIN_USERNAME` | `admin` | 首次管理员用户名 |
| `OCTOP_DATABASE_URL` | — | PostgreSQL DSN（或其他 `OCTOP_DATABASE_*`，见 [configuration.md](../docs/configuration.md)） |
| `OCTOP_DATABASE_DRIVER` | — | 通过环境变量覆盖时：`sqlite` \| `postgresql` |
| `OPENAI_API_KEY` | — | OpenAI 兼容 API Key |
| `DASHSCOPE_API_KEY` | — | 阿里云通义千问 API Key |

Compose 可在 `docker/.env` 中配置上述变量。注意：`.env` 只参与 Compose 插值，变量必须出现在 `docker-compose.yml` 的 `environment:` 中才会进入容器。也可把相同键写入挂载数据目录下的 `~/.octop/env`。

### 数据持久化

- Compose 项目、服务和容器名均为 `maitu`，镜像为 `maitu:latest`；默认将宿主机 `~/.maitu` 挂载到容器 `/data/.octop`。
- `docker run` 示例使用命名卷 `maitu-data`。
- 首次启动会自动执行 `octop init`，凭据写入容器内 `/data/.octop/credential.txt`。未设置 `OCTOP_DEFAULT_PASSWORD` 时自动生成随机密码；指定的密码被应用密码策略拒绝时自动回退为随机密码（首次初始化绝不因弱默认密码而失败）。
- 非特权容器必须能够写入业务数据、授权身份和更新队列。新挂载目录由运维按 UID/GID `1000:1000` 创建；授权身份与队列建议权限 `0700`，预置信任公钥须对 UID 1000 可读。已有目录先备份、停止旧实例，再核查并迁移所需文件归属；不要对整台主机或不相关目录递归 `chown`，也不要通过恢复 root 运行规避权限问题。

### 已有部署升级（必须保留原数据挂载）

默认宿主机目录已经改变。已有部署不能直接用新默认值启动，否则会得到一个空的新实例；原数据不会自动搬移。先备份原数据，再停止旧容器，并显式复用原挂载目录：

```bash
docker stop octop
OCTOP_DATA="$HOME/.octop" docker compose -f docker/docker-compose.yml up -d --build
```

将 `OCTOP_DATA` 的实际绝对路径保存到 `docker/.env`，后续升级、重启均使用同一目录。原部署若使用自定义目录，应替换上面的路径；若使用命名卷，继续挂载原卷，并保留上文 `docker run` 的 capabilities、seccomp、AppArmor 配置，不要创建新的空卷。不要删除旧数据或使用 `down -v`，也不要让新旧实例同时写入同一目录。此版本改为 UID/GID `1000:1000`，旧 root 容器的数据必须先完成权限核查。

移动端 Compose 同样使用 `maitu` 和 `~/.maitu`；已有 Android 容器可通过 `OCTOP_MOBILE_CONTAINER` 指定原名称。PostgreSQL 辅助容器改名为 `maitu-postgres`，升级前须停止旧 `octop-postgres`；原 `octop_pgdata` 卷及数据库用户、库名保持不变，并继续使用原 Compose 项目名以复用原卷。

这是运维品牌改名，不是源码来源隐藏：内部命令、错误栈和依赖仍可能出现上游标识。镜像中的 `LICENSE` 及版权声明保留。

### 健康检查

镜像内置 `HEALTHCHECK`，探测 `GET /api/health`：

```bash
curl http://localhost:8088/api/health
```

### 常用运维命令

```bash
docker logs -f maitu
docker exec -it maitu octop --version
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml up -d --build
```
