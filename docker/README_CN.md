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

### 快速开始

**方式一：Compose（推荐）**

源码目录可命名为 `/opt/maitu`，在该仓库根目录执行：

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

访问 `http://localhost:8088`。未设置 `OCTOP_DEFAULT_PASSWORD` 时，首次初始化会自动生成随机密码并写入 `/data/.octop/credential.txt`；设置了则按设置值初始化（须 ≥8 位且同时包含字母和数字；被应用密码策略拒绝的常见弱密码会自动回退为随机密码）。首次登录后请立即修改密码。

**方式二：构建脚本**

```bash
bash docker/docker_build.sh
docker run -d \
  --name maitu \
  -p 8088:8088 \
  -v maitu-data:/data/.octop \
  -e HOME=/data \
  maitu:latest
```

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

### 已有部署升级（必须保留原数据挂载）

默认宿主机目录已经改变。已有部署不能直接用新默认值启动，否则会得到一个空的新实例；原数据不会自动搬移。先备份原数据，再停止旧容器，并显式复用原挂载目录：

```bash
docker stop octop
OCTOP_DATA="$HOME/.octop" docker compose -f docker/docker-compose.yml up -d --build
```

将 `OCTOP_DATA` 的实际绝对路径保存到 `docker/.env`，后续升级、重启均使用同一目录。原部署若使用自定义目录，应替换上面的路径；若使用命名卷，继续用 `docker run --name maitu -v <原卷名>:/data/.octop -e HOME=/data -p 8088:8088 maitu:latest`，不要创建新的空卷。不要删除旧数据或使用 `down -v`，也不要让新旧实例同时写入同一目录。

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
