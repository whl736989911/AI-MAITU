# Scripts

在**仓库根目录**运行以下命令。

> 从完整源码 ZIP 部署到 Ubuntu 主机（原生 systemd 或 Docker Compose），
> 使用 [`deploy/ubuntu/README.md`](../deploy/ubuntu/README.md) 中的脚本；它覆盖首次安装、升级、
> 数据备份和防火墙/安全边界。

## 麦途智造源码安装与升级

项目地址：<https://github.com/whl736989911/AI-MAITU>。

本项目的源码 ZIP 不包含 `.git`；不能在 ZIP 解压目录运行 `git pull`。`scripts/install.*` 和
`pip install -U maitu` 默认指向上游 PyPI 包，**不能用于安装或升级麦途智造**。
原生部署需要 Python ≥ 3.12、uv、Node.js/npm；不要盲目用 `--all-extras`。
当前核心依赖 `orcakit-harness-agent[all]` 已传递安装 `pynput`/`evdev`，
所以 Ubuntu **即使不选择 desktop** 也应在首次同步前安装构建依赖：

```bash
sudo apt-get install build-essential python3-dev linux-libc-dev
```

自定义 Python 版本需对应的开发头文件；已有可用二进制包的环境不需要重装系统包。

### 首次原生安装

从本项目克隆源码，或将可信 Release 源码 ZIP 解压到新的空目录，然后进入包含 `pyproject.toml` 的目录：

```bash
uv sync --inexact --no-dev --extra office --extra knowledge-ocr
cd dashboard && npm ci && npm run build && cd ..
uv run --no-sync maitu run --port 8088
```

- `office` 安装办公技能的 `pdfplumber`、`pandas`；`knowledge-ocr` 安装 `rapidocr`、`onnxruntime`。
- 本地向量模型另加 `--extra local-embedding`（`fastembed`），模型文件仍须下载；远程嵌入不需要此 extra。
- 桌面控制是可选功能，只有实际使用才选择 `--extra desktop`；不选择该 extra
  目前也不能绕过上述传递依赖的 evdev 编译要求。编译成功不代表 headless/Wayland 会话可控制桌面。
- 每次同步均保留 `--inexact`，避免移除既有可选包；重复声明已选 extras 才会同时按新约束更新它们。
  启动使用 `uv run --no-sync` 或环境内的 `maitu`，不要再用裸 `uv sync` 修复部署。

### Git 源码升级

先停止实际服务并备份完整 `MAITU_HOME` 和外置数据库；使用原服务用户、相同 `HOME`、环境变量和数据路径：

```bash
git pull --ff-only
uv sync --inexact --no-dev --extra office --extra knowledge-ocr
cd dashboard && npm ci && npm run build && cd ..
uv run --no-sync maitu run --port 8088
```

### 原生 Release ZIP 升级（不需要 Git）

此路径只准备新源码和更新原虚拟环境，不自动停止/启动服务、修改 systemd、覆盖配置、迁移或恢复数据库。
Docker 的签名队列升级器仍按原有 `--once`/常驻契约工作，**不要**对 Docker 安装使用下列原生命令。

1. 查看当前服务的 `WorkingDirectory`、`ExecStart`、服务用户及环境文件，记录实际绝对 `MAITU_HOME`；
   若未设置，沿用该服务用户的 `~/.maitu`，不要在 sudo 后误用 root 的目录。
2. 停止旧服务，备份完整数据目录（包括 config、实例/授权状态、会话和附件）及 PostgreSQL 等外置数据库。
   同时保留旧源码和原 `.venv`；不要删除、重建或交换它们，不要生成新配置或重新激活实例。
   授权身份目录可能位于 `MAITU_HOME` 之外：另行记录并备份服务实际使用的
   `MAITU_LICENSE_STATE_DIR`、信任公钥及 CA 文件；不要重新生成实例密钥。
3. 从可信发布方取得 `maitu-版本.zip` 和独立核验的 SHA-256（不能仅相信同一次不可信下载的摘要）。
   GitHub 自动生成的仓库 ZIP 没有 `maitu-release.json`，不能传给此命令；它应使用首次安装的解压方式，
   在新目录显式复用 `UV_PROJECT_ENVIRONMENT`、执行同样的 inexact 同步和前端构建。
   若旧版本脚本没有 `--native-prepare`，先核验整个 ZIP，再将其中的新脚本输出到独立工具目录，
   不覆盖旧源码（此引导步骤需要 `unzip`，不需要 Git）：

```bash
printf '%s  %s\n' "$TRUSTED_RELEASE_SHA256" /srv/downloads/maitu-0.1.1.zip | sha256sum --check - &&
  mkdir /srv/tools/maitu-0.1.1 &&
  unzip -p /srv/downloads/maitu-0.1.1.zip scripts/maitu_updater.py > /srv/tools/maitu-0.1.1/maitu_updater.py
```

4. 使用**原服务用户**，以下路径和摘要替换为实际值；新目录必须不存在，且与数据目录、原环境分离：

```bash
# 用原服务环境运行已核验的新版本宿主脚本；无需安装另一份业务服务。
/srv/AI-MAITU/.venv/bin/python /srv/tools/maitu-0.1.1/maitu_updater.py \
  --native-prepare --service-stopped \
  --archive /srv/downloads/maitu-0.1.1.zip \
  --sha256 "$TRUSTED_RELEASE_SHA256" \
  --destination /srv/releases/maitu-0.1.1 \
  --environment /srv/AI-MAITU/.venv \
  --home /srv/maitu-data \
  --extra local-embedding
```

脚本核验摘要、大小、平台/架构及现有 ZIP 安全边界后，只解压到新目录；复用原环境执行
`uv sync --inexact --no-dev --frozen --extra office --extra knowledge-ocr` 及指定 extras，再构建仪表盘。
不接受缺失 metadata 的 ZIP，不声称本地摘要等同于在线签名认证，不连接或修改授权记录。
命令失败时保留新目录、旧源码、数据和原环境供诊断；**不自动回滚已更新的 Python 包**。
不要重试覆盖失败目录，应检查错误后另选空目录。

5. 准备成功后，手动将服务 `WorkingDirectory` 指向新源码，`ExecStart` 仍使用原环境的 `maitu`，
   保留相同 `MAITU_HOME`、配置环境文件和服务用户，执行 `daemon-reload` 后启动。
   editable 安装已指向新源码，因此仅切回旧 WorkingDirectory **不构成回滚**；
   需停服、在旧源码目录对同一环境执行旧版本的 inexact 同步，并根据迁移情况由管理员恢复备份。
6. 运行下方只读检查，并在应用中核对原实例身份、原用户和历史数据仍存在。不要删除旧版本和备份。

### 完整源码 ZIP 升级（含 SOURCE-PACKAGE.json）

此前交付的 `maitu-source-*.zip` 是完整源码包，不是带 `maitu-release.json` 的 Release 包，
不能传给 `--native-prepare`。它同样不需要 Git：先执行上一节的停服、身份与数据备份步骤，
核对可信发布方单独提供的 SHA-256，再解压至一个不存在的新目录：

```bash
# 示例路径均需替换为当前服务的真实路径；不要改用另一个账号的数据目录。
SOURCE_ZIP=/srv/downloads/maitu-source.zip
NEW_SOURCE=/srv/releases/maitu-source-new
OLD_ENV=/srv/AI-MAITU/.venv
printf '%s  %s\n' "$TRUSTED_RELEASE_SHA256" "$SOURCE_ZIP" | sha256sum --check - &&
  mkdir -- "$NEW_SOURCE" &&
  unzip -q "$SOURCE_ZIP" -d "$NEW_SOURCE"
```

确认 `NEW_SOURCE` 指向含 `pyproject.toml` 的实际源码根目录；若包内有顶层目录，调整该变量，
不要移动或覆盖旧源码和数据。使用原服务用户、原环境及原绝对 `MAITU_HOME` 执行：

```bash
test -f "$NEW_SOURCE/pyproject.toml" &&
  UV_PROJECT_ENVIRONMENT="$OLD_ENV" uv sync --project "$NEW_SOURCE" \
    --inexact --no-dev --frozen --extra office --extra knowledge-ocr &&
  npm --prefix "$NEW_SOURCE/dashboard" ci &&
  npm --prefix "$NEW_SOURCE/dashboard" run build
```

原来启用了 `local-embedding` 或其他 extras 时，在同步命令中重复追加相应 `--extra`，
以更新所选能力；`--inexact` 保留未声明的既有包。离线环境还必须提前准备相应版本的依赖缓存，
源码 ZIP 本身不包含全部 Python/npm 依赖。任何步骤失败都不要启动新服务或继续执行后续切换。
成功后按上一节第 5、6 步切换服务并检查健康、身份及历史数据；只切回目录不能回滚已更新的环境。

### 原生只读检查

```bash
# 无需 sudo、Docker、Git、make 或 /etc/maitu-deploy/platform.json。
python3 -I scripts/deploy.py check \
  --native-source /srv/releases/maitu-0.1.1 \
  --python /srv/AI-MAITU/.venv/bin/python \
  --extra local-embedding \
  --health-url http://127.0.0.1:8088/api/health
```

检查源码/前端产物、工具以及**指定服务解释器**的实际依赖导入；缺失或导入失败返回非零。
默认检查 office/OCR；`--extra desktop` 才检查桌面依赖。不会同步依赖、启动应用、激活实例或修改配置。
省略 `--health-url` 可离线检查，输出明确说明未验证运行状态；模型下载、OCR 推理、
AppArmor/userns、桌面会话、数据库及授权功能仍需单独验证，不能将依赖通过当成功能通过。

API 健康不代表所有智能体均已启动，升级后还要在管理页核对原有智能体的运行状态。
旧 `backend.root_dir` 会夹取到受管理工作区；显式的旧外部 `workspace_dir` 则拒绝接管，
不会自动搬迁或删除其中的数据。遇到这种配置应先停服、备份并核实原文件，再由管理员迁移，
不要把直接更改路径、看不到旧资料误认为升级成功。

### 没有 make 时

`make` 不是部署运行依赖；Ubuntu 可显式安装 `sudo apt-get install make`。
只有完整开发源码（含 tests）才能运行完整质量门；发布 ZIP 不带测试时不可声称门禁通过。
以下为无需 make 的直接检查（先用 inexact 同步安装 dev 组；会保留已装 extras）：

```bash
uv sync --inexact --group dev --extra office --extra knowledge-ocr
uv run --no-sync ruff check src tests scripts/deploy.py scripts/deployment scripts/maitu_updater.py scripts/package_release.py
uv run --no-sync ruff format --check src tests scripts/deploy.py scripts/deployment scripts/maitu_updater.py scripts/package_release.py
uv run --no-sync mypy src/octop
uv run --no-sync pytest -n auto -m "not live"
(cd dashboard && npm ci && npm run lint && npm run format:check && npx tsc -b && npx vitest run && npm run build)
```

这些是非写入式检查，不包含 `make all` 的格式化写入步骤及独立授权中心门禁；
需要完整 ship bar 时仍以完整源码的 `make all` 为准。

## 原生沙箱（Ubuntu 24.04+ 源码安装）

agent `execute` 运行在 bubblewrap jail 中。Ubuntu 24.04+ 默认限制非特权 user namespace（AppArmor），
失败现象为 `bwrap: setting up uid map: Permission denied`。使用仓库内置的最小权限方案——只给
`/usr/bin/bwrap` 授予 userns，不全局放开，且每一步都可撤销：

```bash
sudo bash scripts/setup-host-sandbox.sh                  # 安装 bubblewrap + AppArmor profile
sudo bash scripts/setup-host-sandbox.sh --with-browser   # 追加 Playwright Chromium 及其 profile
sudo bash scripts/setup-host-sandbox.sh --status         # 只查看当前状态
sudo bash scripts/setup-host-sandbox.sh --revert         # 撤销 bwrap profile
sudo bash scripts/setup-host-sandbox.sh --revert-browser # 撤销 Chromium profile
```

- AppArmor 模板：`scripts/security/apparmor-bwrap-userns`、`scripts/security/apparmor-playwright-chromium`。
- 浏览器步骤请以运行服务的用户身份执行（systemd user 服务的 `HOME`/`PATH` 来自该用户）。
- 仪表盘：`GET /api/filesystem/bwrap-status` 只诊断不安装；`POST /api/filesystem/ensure-bwrap`
  显式安装并返回失败分类与运维命令（仅展示）。
- 平台不会在启动时自动安装系统包；安装只发生在管理员显式操作或上述脚本。
- 办公/OCR Python 依赖：`uv sync --inexact --no-dev --extra office --extra knowledge-ocr`。

---

## 构建 PyPI wheel

先构建前端（产物写入 `src/octop/dashboard/`，与 `dashboard/vite.config.ts` 的 `outDir` 一致），再打包 wheel。

```bash
bash scripts/wheel_build.sh
```

Windows:

```powershell
powershell -File scripts/wheel_build.ps1
```

输出：`dist/*.whl`、`dist/*.tar.gz`

发布前请确认 `pyproject.toml` 中的 `orcakit-harness-agent`、`harness-gateway` 等依赖已发布到 PyPI（`[tool.uv.sources]` 仅对 uv 本地开发生效，pip/PyPI 不读取）。

---

## 源码 / systemd 的应用更新

授权激活、发行版本检查和安装是独立链路。原生源码 / systemd 部署可运行 `scripts/install_native_updater.py` 一次性接通检查与原生自动升级：使用独立 release 虚拟环境、停服备份、原子切换、健康校验和失败回滚；只有 `maitu-updater.service` 心跳有效时页面才允许排队。不要给原生服务安装 Docker 更新器，也不要只添加队列目录。安装参数、同版本不触发升级和回滚边界见[源码 / systemd 更新排障](../docs/native-update-troubleshooting.md)。

## 平台说明

- **macOS / Linux**：`install.sh` 支持自动安装 uv、创建 venv；Playwright Chromium 仅在 `--extras browser` 时下载（系统已有 Chrome 则跳过）。
- **Windows**：使用 `install.ps1` 或 `install.bat`；PTY 终端、飞书 bot creator 等非阻塞 stdout 等功能在 Windows 上受限（见下方兼容性分析）。

完整跨平台兼容性分析见项目文档或 PR 说明。
