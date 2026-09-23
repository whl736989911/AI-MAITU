# Scripts

在**仓库根目录**运行以下命令。

## 麦途智造源码安装与升级

项目地址：<https://github.com/whl736989911/AI-MAITU>。

本项目从 v0.1.0 起在 GitHub 发布 Release；升级仍按下方源码步骤操作。`scripts/install.*` 的默认安装源及
`pip install -U octop` 指向上游 PyPI 包，**不能用于升级麦途智造**。
从本项目 GitHub 拉取源码，沿用原有 `OCTOP_HOME` 数据目录：

```bash
# 首次安装
git clone https://github.com/whl736989911/AI-MAITU.git
cd AI-MAITU
uv sync
cd dashboard && npm ci && npm run build && cd ..
uv run octop run --port 8088
```

已从本项目 GitHub 克隆的安装，在原目录更新并重新启动服务：

```bash
git pull --ff-only
uv sync
cd dashboard && npm ci && npm run build && cd ..
uv run octop run --port 8088
```

升级前先停止旧进程；不要删除 `~/.octop`（或自定义 `OCTOP_HOME`）。

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

## 平台说明

- **macOS / Linux**：`install.sh` 支持自动安装 uv、创建 venv；Playwright Chromium 仅在 `--extras browser` 时下载（系统已有 Chrome 则跳过）。
- **Windows**：使用 `install.ps1` 或 `install.bat`；PTY 终端、飞书 bot creator 等非阻塞 stdout 等功能在 Windows 上受限（见下方兼容性分析）。

完整跨平台兼容性分析见项目文档或 PR 说明。
