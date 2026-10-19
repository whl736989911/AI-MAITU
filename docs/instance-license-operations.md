# 实例授权部署与交付操作

## 1. 部署位置与权限

授权中心由交付方独立部署、保管。办公室业务主机只需要出站访问授权中心，不能安装签发服务或持有签发私钥。客户使用 `enterprise_admin` 企业管理员账号；授权中心的 `owner`、系统管理员账号及主机运维权限不交付客户。

固定公网 IP 可以使用，但生产环境必须有包含该 IP 的可信 TLS 证书。证书必须正确验证；不能使用 `verify=False` 或将公网接口改成 HTTP。没有域名不影响协议。管理接口必须通过 VPN 或反向代理 IP 白名单访问；实例激活与同步接口需要让客户主机访问。

授权限制的是正常应用中的整实例业务能力，不是无法破解的 DRM。拥有主机 root、Docker 管理权或磁盘访问权的人仍然可以修改程序。已经下载的数据、已经发出的外部请求无法撤回。

## 2. 独立授权中心

目录：`services/license-center/`。独立 Python 包、SQLite 数据库、React 管理界面和认证会话，不依赖客户业务数据库。

无桌面 Ubuntu 服务器、固定 IP 证书、Docker/Nginx、MFA 初始化、备份恢复和 systemd 替代方案的逐步操作，见 [授权中心详细部署说明](license-center-deployment.md)。

推荐使用根目录统一终端入口 `deploy.sh`：腾讯云 Ubuntu 运行 `sudo bash ./deploy.sh center`，办公室 Ubuntu 导入公开接入包后运行 `sudo bash ./deploy.sh platform`；两端各完成一次向导，再用 `check --role center`／`check --role platform` 进行只读诊断。备份接收方公钥、接管旧数据、秘密输入、配置备份和失败恢复的完整步骤，见 [两机一体化部署向导](license-center-deployment.md#0-推荐入口两台主机各运行一次部署向导)。下文的手工安装仍适用于不使用向导的受控运维场景。

### 2.1 本地安装

在该目录执行：

```bash
uv sync --frozen
```

在 `services/license-center/dashboard/` 执行：

```bash
npm ci
npm run build
```

构建后的前端写入 `license_center/dashboard/`，由授权中心进程提供；不是客户业务后台的构建产物。

为中心设置独立、持久且仅运维用户可访问的数据目录：

```bash
export LICENSE_CENTER_DATA=/var/lib/maitu-license-center
export LICENSE_CENTER_TIMEZONE=Asia/Shanghai
umask 077
uv run license-center init
uv run license-center export-trust /etc/maitu/license/trusted-keys.json
uv run license-center run --host 127.0.0.1 --port 8090
```

`init` 会交互要求至少 12 位密码，显示一次 TOTP 密钥和配对 URI，并要求输入当前验证码。配对到交付方自己的身份验证器。初始化失败或恢复账户时只能在中心主机本地操作，不向客户提供万能密码。

默认使用安全 Cookie；公网登录必须经过 HTTPS。仅在真正的回环网络本地开发时可以设置 `LICENSE_CENTER_COOKIE_SECURE=0`；不要用于生产。

### 2.2 Docker 与反向代理

提供的 `docker-compose.yml` 只绑定宿主机 `127.0.0.1:8090`，使用非 root 用户、只读根文件系统、受限临时目录和持久数据挂载。先创建独立网络，并查看它的网关地址：

```bash
docker network create maitu-license-center
docker network inspect maitu-license-center
```

设置以下变量后再执行 Compose：

| 变量 | 设置 |
| --- | --- |
| `LICENSE_CENTER_DATA` | 宿主机上的受保护持久目录，容器用户 UID 10001 必须可写 |
| `LICENSE_CENTER_TLS_CERT_HOST` | 宿主机上的公开证书链 PEM 文件；不挂载 TLS 私钥到应用容器 |
| `LICENSE_CENTER_TRUSTED_PROXY_IPS` | 应用实际看到的反向代理来源地址，通常是该独立 Docker 网络的宿主机网关；不能填写 `*` |
| `LICENSE_CENTER_TIMEZONE` | 运营界面显示和输入授权截止时间的时区，默认 `Asia/Shanghai` |

在 `services/license-center/` 执行：

```bash
docker compose build
docker compose run --rm license-center init
docker compose run --rm license-center export-trust
docker compose up -d
```

将 `export-trust` 输出的 JSON 保存到客户主机的 `trusted-keys.json`。它只有公开验签密钥；不得复制中心的 `keys/` 或完整中心数据目录。

参考同目录 `nginx.conf.example` 配置宿主机 HTTPS 反向代理。替换示例 IP、证书路径和管理网段。私钥仅由 Nginx 使用；限制 `/api/v1/admin/`，包括登录接口。实例接口 `/api/v1/instance/` 保持客户可访问。配置证书自动续期、中心健康检查、磁盘容量及备份告警；不能只依赖界面上显示的证书日期。

### 2.3 备份、恢复和密钥轮换

```bash
uv run license-center backup /protected-backups/center.sqlite3
```

该命令使用 SQLite 备份 API，能正确处理 WAL。另行加密备份签发私钥和 TOTP 加密密钥；数据库快照不能替代密钥备份。恢复时先停止服务并保留当前数据，再执行：

```bash
uv run license-center restore /protected-backups/center.sqlite3 --confirm
```

恢复配套的密钥文件后启动服务。不要在运行中的服务上直接复制 SQLite 主文件。

签发密钥轮换顺序：先 `stage-key`，将包含新公开密钥的 `export-trust` 分发到所有客户主机，重启客户服务以加载更新后的可信密钥，逐台确认仍可验签，再 `activate-key <kid> --confirm`。客户端在初始化时读取可信密钥文件，不能只复制文件就立即切换签发密钥。保留旧公开密钥覆盖仍有效的历史缓存。新签发私钥始终留在中心。

遗失中心管理员身份验证器时，由中心主机运维人员执行 `recover <username> --confirm`，设置新密码、重新配对 TOTP 并验证。旧会话会失效；该操作有审计记录，不影响客户签发身份。

## 3. 办公室业务主机

### 3.1 配置与升级顺序

该版本没有关闭授权检查的配置开关。缺少配置或有效凭证时默认锁定。因此升级现有实例前必须先部署中心、导出可信公开密钥、备份业务数据，并在隔离副本完成激活验证；不要直接替换正在使用的未授权实例。

Docker 部署通过 `docker/docker-compose.managed.yml` 加入托管授权和更新配置：

```bash
docker compose -f docker/docker-compose.yml \
  -f docker/docker-compose.managed.yml up -d --build
```

在 Compose 环境文件中设置以下宿主路径；不要把身份私钥、客户数据和密钥文件放在同一目录：

| 变量 | 用途 |
| --- | --- |
| `MAITU_CENTER_URL` | 授权及更新中心的可信 HTTPS 根地址 |
| `MAITU_LICENSE_TRUSTED_KEYS_HOST_FILE` | 授权签名 keyring JSON（KID→PEM），只读挂载至 `/run/maitu-trust/license-trusted-keys.json` |
| `MAITU_UPDATE_TRUSTED_KEYS_HOST_FILE` | 发布签名 keyring JSON（KID→PEM），只读挂载至 `/run/maitu-trust/release-trusted-keys.json`；不得与授权 keyring 混用 |
| `MAITU_LICENSE_STATE_HOST_DIR` | 独立实例身份、在线/离线凭据、版本栅栏及提醒账本目录，容器 UID/GID 1000 可写 |
| `MAITU_UPDATE_QUEUE_HOST_DIR` | 独立更新请求和进度队列目录；须与宿主更新器的 `MAITU_UPDATE_QUEUE_DIR` 指向同一目录 |
| `MAITU_CENTER_CLIENT_TOKEN` | 必须配置独立客户侧版本读取令牌；不得使用中心管理凭据 |

`MAITU_LICENSE_TRUSTED_KEYS_FILE`、`MAITU_LICENSE_STATE_DIR`、`MAITU_LICENSE_CENTER_URL`、`MAITU_UPDATE_TRUSTED_KEYS_FILE`、`MAITU_UPDATE_QUEUE_DIR` 和 `MAITU_UPDATE_CENTER_URL` 由托管 Compose overlay 指向容器内路径。keyring 应保留所有仍可能用于验证有效凭据/发布签名的 KID；换钥前先导出更新接入包。私有 CA 环境由中心连接包与部署向导配置，不能关闭 TLS 验证。已有实例升级务必保留原 `MAITU_DATA`、授权身份目录、队列目录和 Compose project name。

平台也可以在初始化向导的“授权”步骤或 **应用设置 → 高级设置 → 授权中心** 中配置同一组公开 trust 材料（授权中心根地址、`trusted-keys.json`、可选根 CA）。此时显式的 `config.json` `license` 配置优先于 `MAITU_LICENSE_*` 环境变量；实例状态目录和 keyring 路径默认会保留原环境配置，避免升级时更换实例身份。若仍使用 `setup-license.sh` 修改环境变量，而平台已有 `config.json` 授权配置，请先在应用设置中更新，或移除旧 `license` 段后再重启，否则脚本写入的环境值会被显式配置覆盖。
实例身份、已验证的在线/离线凭据、共用版本栅栏和提醒账本存放在独立的授权状态 bind mount；业务数据备份不能覆盖它们。默认执行器使用镜像内 Bubblewrap，能读到 `/usr/local/maitu-venv` 的只读 Python 环境，但不能访问授权目录、源码或宿主 Docker socket。Compose 默认不挂载 Docker socket；独立 Docker 执行路径仅由可信运维显式启用，宿主更新器运行于单独的 systemd 服务，不向客户业务容器开放。Linux 镜像包含 LibreOffice / Bubblewrap，用户命名空间必须由宿主 AppArmor/内核策略允许；见 [Docker 部署说明](../docker/README_CN.md#执行沙箱与浏览器linux)。Windows 部署还需要设置真实 NTFS ACL，不能把 `chmod` 当作 NTFS 权限隔离。

### 3.2 开通与交付

1. 交付方登录授权中心，创建客户记录。
2. 创建一次性激活码，分别确认授权截止时间和激活码有效期。日期输入以授权中心设置的时区解释，不能用运营人员电脑的本地时区替代。
3. 在办公室主机完成基本安装。首次安装尚未授权时，业务初始化会暂存，激活后再恢复，不能借初始化接口启动业务。
4. 系统/企业管理员在锁定页面输入激活码；主机会生成独立实例身份并向中心签名证明。也可以由主机运维人员使用下面的本地 CLI。
5. 确认授权状态、截止时间、企业权限和正常业务可用，再创建/交付客户的企业管理员账号。不交付中心账户、系统管理员凭据或签发密钥。
6. 如交付系统管理员分享的通用助手，确认客户账号具有该助手的共享使用权及 `features` 权限。客户可在共享助手中创建功能，新功能归当前登录客户用户，而非通用助手分享者；随后在新功能自己的会话中配置和训练。共享不代表拥有源助手，也不授予分享者的管理员权限，其他企业的目标功能仍受原管理边界限制。

本地运维命令使用同一验签和状态机：

```bash
uv run maitu license status
uv run maitu license activate
uv run maitu license refresh
```

`activate` 会交互要求输入激活码；非交互使用 `uv run maitu license activate --code <激活码>`。命令行参数可能进入进程列表或终端历史，交付现场优先使用交互输入，不要把激活码写入部署日志或共享聊天记录。

## 4. 到期、撤销与解锁

| 场景 | 操作与结果 |
| --- | --- |
| 正常到期 | 到实际授权截止时间自动拒绝业务读写、新任务和连接；取消应用可控制的任务，保留数据和登录/授权页面 |
| 延期 | 中心选择实例授权执行延期，填写新截止时间及原因；客户点击检查授权，或等待锁定状态下的自动同步 |
| 提前撤销 | 中心撤销授权并确认原因；客户下一次成功同步后锁定 |
| 恢复撤销 | 中心恢复授权；若原截止时间已过去，同时设置新的截止时间；客户同步后恢复业务 |
| 客户整体停用 | 中心停用客户，其所有实例同步后拒绝业务；重新启用客户后同步恢复仍在有效期内的授权 |
| 断网/中心不可用 | 已有有效凭证最多支持 24 小时，且不超过真实授权截止时间；不存在到期后的额外宽限 |
| 更换主机 | 中心执行替换/解绑旧实例，生成新激活码，在新主机激活；旧实例联网获知撤销或缓存截止后锁定，不复制旧身份私钥冒充新主机 |

配置了授权信任公钥和中心地址的托管实例，启动时立即尝试同步，之后每小时尝试一次；锁定状态同样按这一周期尝试。提前撤销不是互联网即时推送：正常联网最长等待一次同步周期，断网实例最多等待已有凭证截止。不能向客户承诺断网时即时停用。

同步失败会保存最近一次 `sync_error`，启动同步和周期同步的异常也会写入平台进程日志；当前周期循环没有指数退避或锁定后每 5 分钟重试。到期监视不等待下一次网络请求。签名响应会为当前进程建立可信时间锚点，并计入往返时间的保守上界，不能利用略慢的客户时钟延长试用。严重时间异常仍需运维修正系统时间。

解锁不需要删除数据库、修改到期字段或重新安装。中心更新授权后，客户企业管理员只触发同步；可信新凭证到达后恢复 Agent、通道、业务计划任务和索引处理。企业管理员没有签发或修改本地授权凭证的权限。

## 5. 数据恢复边界

客户业务备份与授权中心备份是两种独立产物。业务导出不包含实例私钥、缓存凭证及 `MAITU_LICENSE_*` 验签配置；导入也不能覆盖当前主机的受保护授权目录。业务归档不是转移授权的手段。原主机业务恢复继续使用它自己的受保护身份；主机迁移应走中心替换和新激活流程。

锁定不会删除已有用户、文件、知识库或会话。自动 TLS 续期和可信系统备份保持运行；普通手动业务备份和业务导出仍被锁定门禁拒绝，不能将自动维护的豁免用于下载客户数据。无法撤回已经离开应用的请求；迁移、恢复和手动运维前应停止有关外部任务并核对客户数据备份。

中心数据库的一致性备份必须同时保留受保护的签名密钥目录和 MFA 加密材料。签名密钥元数据只存文件名，恢复到不同数据目录时不依赖原主机的绝对路径。旧备份中较低的授权版本会被客户的已见版本栅栏拒绝；恢复后应核对审计记录并以更高版本明确延期/恢复授权，不能删除客户缓存或版本栅栏来绕过拒绝。

## 6. 排障顺序

1. 查看公开授权状态以及企业管理员可见的实例 ID、指纹和同步结果，不上传私钥或完整凭证。
2. 检查客户主机出站 HTTPS、证书链/IP SAN、服务器时间和只读可信公开密钥文件。
3. 在中心核对客户启用状态、实例绑定、实际截止时间、授权版本和操作审计。
4. 延期或恢复后由企业管理员手动检查；已失效缓存本身不能用来解除锁定。
5. 配置错误、中心暂时不可达、缓存过期和真正授权到期分别排查；不要用删缓存、回滚时钟、改数据库或关闭验签处理。

授权地址、信任文件或 CA 配置错误时，业务平台以未配置锁定状态启动，并保留登录与授权诊断入口；具体配置异常写入主机日志。修正运维配置后重启平台，再检查授权；这不是临时放行或跳过验签。

中心实例列表的“最近联系”取自 `last_seen_at`；显示 `—` 表示接口没有返回联系时间，不表示已在线。成功通过身份验证的在线激活或授权同步会写入该字段，检查新版本、下载升级包不会更新它。平台启动同步还要求容器内实际配置了 `MAITU_LICENSE_TRUSTED_KEYS_FILE` 和 `MAITU_LICENSE_CENTER_URL`；升级中心配置不能替代授权中心配置。

如果激活成功、重启后仍不能更新联系时间，检查同步请求是否被中心返回 HTTP 401。平台同步的签名请求必须同时携带实例公钥和挑战证明，中心继续验证签名、挑战和实例绑定；不要通过关闭中心验签来修复。更新到包含这一协议修复的平台源码并重启即可，无需重新激活，也不要删除或更换现有实例身份目录。修复前的历史失败无法补写，只能从后续成功同步开始记录联系时间。
