# 实例授权部署与交付操作

## 1. 部署位置与权限

授权中心由交付方独立部署、保管。办公室业务主机只需要出站访问授权中心，不能安装签发服务或持有签发私钥。客户使用 `enterprise_admin` 企业管理员账号；授权中心的 `owner`、系统管理员账号及主机运维权限不交付客户。

固定公网 IP 可以使用，但生产环境必须有包含该 IP 的可信 TLS 证书。证书必须正确验证；不能使用 `verify=False` 或将公网接口改成 HTTP。没有域名不影响协议。管理接口必须通过 VPN 或反向代理 IP 白名单访问；实例激活与同步接口需要让客户主机访问。

授权限制的是正常应用中的整实例业务能力，不是无法破解的 DRM。拥有主机 root、Docker 管理权或磁盘访问权的人仍然可以修改程序。已经下载的数据、已经发出的外部请求无法撤回。

## 2. 独立授权中心

目录：`services/license-center/`。独立 Python 包、SQLite 数据库、React 管理界面和认证会话，不依赖客户业务数据库。

无桌面 Ubuntu 服务器、固定 IP 证书、Docker/Nginx、MFA 初始化、备份恢复和 systemd 替代方案的逐步操作，见 [授权中心详细部署说明](license-center-deployment.md)。

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

环境配置：

| 变量 | 内容 |
| --- | --- |
| `OCTOP_LICENSE_SERVER_URL` | 授权中心 HTTPS 根地址，例如包含 IP 证书的固定公网 IP 地址 |
| `OCTOP_LICENSE_TRUST_FILE` | 运维预置的公开验签 JSON 文件，内容为 `kid → PEM` |
| `OCTOP_LICENSE_CA_FILE` | 可选受信任 CA PEM 文件；使用私有 CA 时必须明确安装，不能跳过证书验证 |
| `OCTOP_LICENSE_CONTACT` | 锁定页面显示的交付方联系方式 |

Linux Docker 可以叠加 `docker/docker-compose.licensing.yml`：

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.licensing.yml up -d
```

同时设置 `OCTOP_LICENSE_TRUST_DIR` 指向宿主机上的运维管理公开密钥目录。该目录以只读形式挂载到 `/etc/maitu/license`。配置 CA 文件时使用容器内的路径。原有 `OCTOP_DATA` 数据挂载必须继续复用，不能因为升级重新生成空数据目录。

实例身份私钥、缓存凭证和已见版本栅栏位于业务数据目录的 `license/state.json`，与 Agent 工作区隔离。保护业务数据目录的系统权限，保持系统时间同步，不允许客户通过终端、环境变量、插件安装、自定义 stdio MCP 或任意宿主机目录后端接触该边界。Windows 部署还需要设置真实 NTFS ACL，不能把 `chmod` 当作 NTFS 权限隔离。

### 3.2 开通与交付

1. 交付方登录授权中心，创建客户记录。
2. 创建一次性激活码，分别确认授权截止时间和激活码有效期。日期输入以授权中心设置的时区解释，不能用运营人员电脑的本地时区替代。
3. 在办公室主机完成基本安装。首次安装尚未授权时，业务初始化会暂存，激活后再恢复，不能借初始化接口启动业务。
4. 系统/企业管理员在锁定页面输入激活码；主机会生成独立实例身份并向中心签名证明。也可以由主机运维人员使用下面的本地 CLI。
5. 确认授权状态、截止时间、企业权限和正常业务可用，再创建/交付客户的企业管理员账号。不交付中心账户、系统管理员凭据或签发密钥。

本地运维命令使用同一验签和状态机：

```bash
uv run octop license status
uv run octop license activate
uv run octop license refresh
```

`activate` 会交互要求输入激活码；非交互使用 `uv run octop license activate --code <激活码>`。命令行参数可能进入进程列表或终端历史，交付现场优先使用交互输入，不要把激活码写入部署日志或共享聊天记录。

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

正常状态每小时同步，启动时立即尝试；锁定后每 5 分钟尝试。手动检查受实例级限频与并发合并保护。提前撤销不是互联网即时推送：正常联网最长等待一次同步周期，断网实例最多等待已有凭证截止。不能向客户承诺断网时即时停用。

同步失败时采用指数退避和随机抖动：起始约 30 秒，有效缓存期间最长约 1 小时，锁定期间最长约 5 分钟；到期监视不等待下一次网络重试。签名响应会为当前进程建立可信时间锚点，并计入往返时间的保守上界，不能利用略慢的客户时钟延长试用。严重时间异常仍需运维修正系统时间。

解锁不需要删除数据库、修改到期字段或重新安装。中心更新授权后，客户企业管理员只触发同步；可信新凭证到达后恢复 Agent、通道、业务计划任务和索引处理。企业管理员没有签发或修改本地授权凭证的权限。

## 5. 数据恢复边界

客户业务备份与授权中心备份是两种独立产物。业务导出不包含实例私钥、缓存凭证及 `OCTOP_LICENSE_*` 验签配置；导入也不能覆盖当前主机的受保护授权目录。业务归档不是转移授权的手段。原主机业务恢复继续使用它自己的受保护身份；主机迁移应走中心替换和新激活流程。

锁定不会删除已有用户、文件、知识库或会话。自动 TLS 续期和可信系统备份保持运行；普通手动业务备份和业务导出仍被锁定门禁拒绝，不能将自动维护的豁免用于下载客户数据。无法撤回已经离开应用的请求；迁移、恢复和手动运维前应停止有关外部任务并核对客户数据备份。

中心数据库的一致性备份必须同时保留受保护的签名密钥目录和 MFA 加密材料。签名密钥元数据只存文件名，恢复到不同数据目录时不依赖原主机的绝对路径。旧备份中较低的授权版本会被客户的已见版本栅栏拒绝；恢复后应核对审计记录并以更高版本明确延期/恢复授权，不能删除客户缓存或版本栅栏来绕过拒绝。

## 6. 排障顺序

1. 查看公开授权状态以及企业管理员可见的实例 ID、指纹和同步结果，不上传私钥或完整凭证。
2. 检查客户主机出站 HTTPS、证书链/IP SAN、服务器时间和只读可信公开密钥文件。
3. 在中心核对客户启用状态、实例绑定、实际截止时间、授权版本和操作审计。
4. 延期或恢复后由企业管理员手动检查；已失效缓存本身不能用来解除锁定。
5. 配置错误、中心暂时不可达、缓存过期和真正授权到期分别排查；不要用删缓存、回滚时钟、改数据库或关闭验签处理。

授权地址、信任文件或 CA 配置错误时，业务平台以未配置锁定状态启动，并保留登录与授权诊断入口；具体配置异常写入主机日志。修正运维配置后重启平台，再检查授权；这不是临时放行或跳过验签。
