# AI-MAITU · 麦途智造

面向制造企业的**自托管企业 AI 工作台**。

基于 **[腾讯云麦途智造](https://github.com/TencentCloud/麦途智造)**（多用户、多 Agent 的自托管 AI 助手）改造，扩展为**有治理的业务能力平台**。

> **本项目是一个派生（fork）项目。** 我们更换了品牌，并新增了下文描述的企业模块。上游项目本身、它的架构与工程质量，属于麦途智造的作者们。详见 [开源协议与署名](#开源协议与署名)。

---

## 这是什么

员工打开的是一个**业务能力目录**——「起草报价单」「提取客户 BOM」「生成机加工工艺包」——填好表单，直接取走可用的草稿。

它不是一个"套了系统提示词的聊天框"，而是一个**受治理的能力**：

- 有归属、有版本
- 有明确的输入表单与输出形态
- 有权限边界（哪个部门、哪个角色能用）
- 每次运行都有审计留痕

**为制造业而做**：能力本身是流程密集型的（多步骤、需人工把关、产出文档），产出的东西要交给工艺员、车间和机床用。

## 我们在麦途智造之上做了什么

| 模块 | 作用 |
|---|---|
| **品牌配置层** | 一个 `brand.config.json` 驱动产品名、标志、配色、PWA 清单。**可反复运行**——改配置后跑 `scripts/apply_brand.py`，它记得上次写过什么，能干净地换品牌。 |
| **企业功能目录** | 声明式业务能力（磁盘上的 `feature.json`）：表单结构 + 提示词 + 输出形态。**加一个能力是编辑文件，不是发版**。 |
| **三级权限模型** | `admin` / `unit_admin` / `user` + 组织单元。有效权限 = 角色 ∪ 部门 ∪ 授权 − 拒绝，**拒绝优先级最高**。 |
| **统一共享治理** | Agent、连接器、知识库、功能共用**同一个访问控制对象**。改成"全公司可见"需要审批；每次变更都有版本、可回滚。 |
| **自我改进闭环** | 每次运行记录「AI 的草稿」和「人改成的定稿」。差异被累积，模型从真实修正中**归纳规则**，**由人审核**，通过的规则注入后续运行。 |
| **数据源与知识库作用域** | 数据源入知识库。运行时作用域**按调用者解析**——**功能定义"怎么做"，调用者的权限决定"能看什么数据"**。 |

## 架构一屏

```
功能 = 有归属、有版本、有生命周期、有 ACL 的 agent-backed 任务流

定义
  ① 元数据   名称 / 图标 / 归属部门 / 谁能用
  ② 能力     功能自己的 agent 配置（模型、工具、技能、子 agent、MCP、知识库、人设文件）
  ③ 任务     表单（输入）+ 步骤（见下）+ 输出形态
  ④ 学习     规则（个人 / 部门 / 全局）+ 案例 + 差异

横切
  A  配置共享，数据按调用者
  B  运行是可恢复状态机（继续 / 回退 / 带修正重跑）
  C  每次运行快照：配置、规则、agent、模型怎么拆的
```

**步骤是混合架构**：**骨架由我们声明**（因此在骨架层能插人工门、校验门和回退点），**骨架之内由模型自主拆解并并行调度子 agent**。平台只提供**并发上限**并**记录模型实际拆了什么**——不替模型编排并行方案。

> 现状：**骨架（线性步骤）已实现**——类型化产物、三种门（`auto` / `confirm` / `validate`）、`on_failure`
> （`abort` / `escalate` / `retry`）、可中断可恢复的运行（继续 / 回退重跑 / 带修正重跑）与运行审计。
> **步骤之内由模型自主拆解并行**（`mode: "orchestrate"` / `agent_role`）尚未实现：定义可以声明，
> 但**运行会被明确拒绝**，不会降级成单 agent 跑（`max_parallel` 只做校验与记录，本轮不并发）。

## 快速开始（源码运行）

前置：**Python ≥ 3.12**、[`uv`](https://docs.astral.sh/uv/)、**Node.js**（构建前端）。
Ubuntu 首次同步前安装 `build-essential python3-dev linux-libc-dev`：
当前核心 `orcakit-harness-agent[all]` 即使未选 desktop 也会传递安装 pynput/evdev。

```bash
git clone https://github.com/whl736989911/AI-MAITU.git
cd AI-MAITU

# 后端依赖
uv sync --inexact --no-dev --extra office --extra knowledge-ocr

# 前端（构建产物由后端直接服务）
cd dashboard && npm ci && npm run build && cd ..

# 启动
uv run --no-sync maitu run --port 8088
```

**首次启动**会打印一次性安装密码和密码文件路径（`maitu-login.txt`）。打开 `http://127.0.0.1:8088` 粘贴密码，向导会：
1. 选择数据库后端（SQLite 或 PostgreSQL）
2. 创建第一个管理员
3. 可选：配置模型提供商

**本地嵌入模型**：同步命令追加 `--extra local-embedding`，再在管理界面下载模型。
桌面控制仅按需追加 `--extra desktop`，且需要兼容图形会话；不选择该 extra 目前不能免除传递依赖的 evdev 编译要求。

已有部署同步始终保留 `--inexact`，避免静默卸载已装可选包；重复声明选用的 extras 才会同步更新其依赖。
Release ZIP 没有 `.git`，不能使用 `git pull`；请按[原生 ZIP 升级、依赖检查和无 make 命令](scripts/README.md#原生-release-zip-升级不需要-git)操作。
升级复用原环境和 `MAITU_HOME`，新源码独立准备，手动切换服务，不重建配置或授权身份。

### Ubuntu 原生沙箱（源码安装）

agent 的 `execute` 运行在 bubblewrap jail 中。Ubuntu 24.04+ 默认用 AppArmor 限制非特权 user namespace，jail 会以 `bwrap: setting up uid map: Permission denied` 失败。请使用仓库内置的最小权限、可撤销方案，**不要**全局关闭 AppArmor 的限制。

```bash
sudo bash scripts/setup-host-sandbox.sh              # 安装 bubblewrap + userns profile（仅授予 /usr/bin/bwrap）
sudo bash scripts/setup-host-sandbox.sh --status     # 查看当前状态
sudo bash scripts/setup-host-sandbox.sh --revert     # 撤销 profile
```

管理后台的「执行沙箱」页面展示实时状态：`GET /api/filesystem/bwrap-status` 只诊断、不安装；显式操作 `POST /api/filesystem/ensure-bwrap` 才安装并给出失败分类（`missing_binary`、`install_permission_denied`、`userns_denied`、`runtime_failure`）与运维命令（仅展示，不自动执行）。服务启动时不会自行安装任何系统包。配置了作用域 `local_shell` 的后端是失效关闭的：沙箱不可用时 agent 拒绝启动，而不是静默退回宿主机执行。

技能脚本在 jail 内使用平台 Python 环境。办公/OCR 依赖：`uv sync --inexact --no-dev --extra office --extra knowledge-ocr`。

浏览器/视觉通道（可选）：Chromium 不再以 `--no-sandbox` 启动，Playwright 的 Chromium 需要自己的 userns profile：

```bash
sudo bash scripts/setup-host-sandbox.sh --with-browser  # Chromium（以当前用户安装）+ profile
sudo bash scripts/setup-host-sandbox.sh --revert-browser
```

浏览器步骤请以运行服务的用户身份执行（systemd user 服务的 `HOME`/`PATH` 来自该用户）；profile 只匹配 `~/.cache/ms-playwright/...` 下的可执行文件，并保持 Chromium 自身沙箱开启。若服务 `HOME` 不在 `/home` 下，请调整 `scripts/security/apparmor-playwright-chromium` 中的路径。

### 开发

```bash
make dev            # 后端 + 前端开发服务器
make dev-backend
make dev-frontend
make test           # 全量测试
make precommit      # 格式化、lint、类型检查、受影响测试
```

## 配置

数据存放在 `MAITU_HOME`（默认 `~/.maitu`）：数据库、Agent 工作区、日志、插件、嵌入模型。
启动前设置 `MAITU_HOME` 可以并行跑多个实例。

品牌配置在 `brand.config.json`，应用方式：

```bash
uv run python scripts/apply_brand.py
```

## 开源协议与署名

**本项目采用 MIT 协议。见 [LICENSE](LICENSE)。**

### 上游项目

本项目派生自 **[TencentCloud/麦途智造](https://github.com/TencentCloud/麦途智造)**，该项目同样采用 MIT 协议。

MIT 协议要求：**原始版权声明与许可声明必须包含在本软件的所有副本和实质性部分中**。
**上游声明已在 [`LICENSE`](LICENSE) 中原样保留**：

```
Copyright (c) 2026 麦途智造
```

我们的修改记录在其下方：

```
Copyright (c) 2026 Suzhou Maitou — modifications in this fork
```

`scripts/apply_brand.py` **在代码层面拒绝改写 `LICENSE`**（有专门的守卫），因此**换品牌这个操作不可能意外抹掉上游署名**。

### 我们改了什么

以下为不完全清单，便于区分上游与派生内容：

- 产品名、标志、图标、配色、PWA 元数据
- 移除了内置吉祥物素材与桌面端外壳相关代码
- 新增：功能目录、权限模型、共享治理、自我改进闭环、数据源模块
- 新增数据库迁移 `016`–`022`

**其余部分均为上游麦途智造的成果。** 如需同步上游更新，可添加上游仓库：

```bash
git remote add upstream https://github.com/TencentCloud/麦途智造.git
git fetch upstream
```

### 第三方组件

本项目捆绑并依赖大量第三方软件（Python 与 Node 包、字体、图标、模型权重）。它们各自保留自己的许可协议；请查阅相应软件包及其锁文件（`uv.lock`、`dashboard/package-lock.json`）。

**分发本项目的构建产物意味着同时分发这些组件**，请自行核对其授权条款。

---

## 致谢

本项目的基石——Agent 运行时、工具与技能系统、网关、多渠道架构、以及整个仪表盘——**是 [麦途智造](https://github.com/TencentCloud/麦途智造) 作者与贡献者们的成果**。在此致谢。
