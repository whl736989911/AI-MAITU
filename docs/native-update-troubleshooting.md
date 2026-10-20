# 源码 / systemd 部署：应用更新排障

## 先区分三条独立链路

1. **授权激活**：网页“授权中心”设置保存 `config.json.license`，使用授权签名公钥和实例身份。
2. **检查发行版本**：平台进程读取 `MAITU_UPDATE_CENTER_URL`、`MAITU_UPDATE_CLIENT_TOKEN`、`MAITU_UPDATE_TRUSTED_KEYS_FILE`，访问中心 `/api/v1/releases/latest` 并用**发行签名**公钥验签。
3. **执行安装**：`scripts/maitu_updater.py --native` 消费页面升级队列，在宿主机上完成停服、数据备份、独立新版本虚拟环境构建、原子切换和健康验证；失败自动恢复旧版本和数据。它必须在宿主机 systemd 中运行，不能放进平台容器。

因此“授权已激活”“中心已发布包”均不表示原生部署已接通自动安装。不能只给原生服务添加 `MAITU_UPDATE_QUEUE_DIR` 来消除提示，也不能安装 Docker updater 服务来替代原生升级流程。平台只在 updater 心跳 `updater-ready.json` 有效时才允许排队。

## 1. 确认实际服务，不打印秘密

以下只读命令以系统级 `maitu.service` 为例；先替换为实际服务名称。旧部署可能是 `octop.service`，用户级服务须由原服务用户使用 `systemctl --user`。不要在同一数据目录启动第二个实例。

```bash
systemctl show maitu.service \
  -p LoadState -p ActiveState -p FragmentPath -p DropInPaths -p EnvironmentFiles
```

在服务器本地核对原 `User`、`ExecStart`、`WorkingDirectory`、`OCTOP_HOME` 和虚拟环境。不要把完整 unit、`config.json`、`.env`、`printenv`、`systemctl show -p Environment` 或令牌输出发到聊天。

## 2. 一次性安装原生更新器

以下命令只适用于**系统级** systemd 服务（例如 `maitu.service`），必须由主机 root 执行；用户级 `systemctl --user` 服务当前不能使用该安装器。先在第 1 节核对原服务、数据目录和虚拟环境，再执行：

```bash
cd /opt/maitu   # 实际源码根目录
sudo python3 scripts/install_native_updater.py \
  --unit maitu.service \
  --app-user maitu \
  --source-dir /opt/maitu \
  --venv /opt/maitu/.venv \
  --data-dir /home/maitu/.maitu \
  --queue-dir /var/lib/maitu-update/queue \
  --state-dir /var/lib/maitu-update/state \
  --health-url http://127.0.0.1:8088/api/health \
  --center-url https://your-trusted-center.example \
  --trusted-keys-file /etc/maitu/release-trusted-keys.json \
  --client-token-file /root/maitu-release-reader.token \
  --architecture amd64
```

- `--data-dir` 必须是实际的 `OCTOP_HOME`；旧部署常见为 `/home/<用户>/.octop`，不要按示例猜路径。
- `--client-token-file` 是仅 root 可读的发行包读取令牌文件；不要把令牌放进命令行、聊天或 Git。
- `--trusted-keys-file` 是中心接入包中的 `release_trusted_keys`，单独保存为 JSON，不是 `license_trusted_keys`。
- ARM 主机把 `--architecture` 改成 `arm64`；安装器会检查 `uv` 和 `npm`。
- 安装器用固定启动器覆盖原 `ExecStart`，并执行 `octop run`/`maitu run`。如果原 unit 依赖自定义 CLI 参数、容器包装或多个 `ExecStart`，不要直接运行安装器，先按实际 unit 调整启动器。
- 安装器生成 `/etc/maitu/update.env`（平台只读更新配置）、`/etc/maitu/updater.env`（root-only，含读取令牌和可选 PostgreSQL DSN）、`/etc/maitu/current-release.env` 和稳定的 systemd 启动器，然后启动 `maitu-updater.service` 并重启原平台服务。首次运行会短暂重启平台。

安装完成后检查：

```bash
systemctl is-active maitu-updater.service
systemctl is-enabled maitu-updater.service
test -s /var/lib/maitu-update/queue/updater-ready.json && echo ready
```

`updater-ready.json` 超过 15 分钟未更新、文件损坏或 updater 未运行时，页面会显示“当前安装未配置自动安装”，不会允许排队。构建/备份期间 updater 单线程阻塞是正常的，心跳窗口已覆盖这一阶段。

## 3. 安装器实际写入什么

安装器把更新检查配置与更新器配置分开，避免把 PostgreSQL DSN 暴露给平台进程：

- `/etc/maitu/update.env`：平台读取；`0400 root:<app-user>` 或 `0640 root:<app-group>`，只含中心地址、发行读取令牌、发行公钥路径和队列目录。
- `/etc/maitu/updater.env`：仅 root 读取；额外包含数据目录、健康地址、systemd unit、release env 路径和可选 PostgreSQL DSN。
- `/etc/maitu/current-release.env`：当前 release 根目录和独立虚拟环境路径，更新时原子替换。
- `/opt/maitu-updater/run-current`：固定 systemd 启动器，只从 release env 读取路径并 exec `octop run`/`maitu run`。
- `/etc/systemd/system/<unit>.d/90-maitu-updater.conf`：用固定启动器覆盖原 `ExecStart`，并加载平台更新配置。

发行读取令牌不是管理员会话令牌、激活码或实例授权 JWT。公开接入包故意不含令牌；经安全通道单独交付。网页授权设置中保存的 CA 不会自动用于发行版本检查；私有 CA 场景仍由运维设置可信证书，不关闭 TLS 验签。

## 4. 检查结果应如何解读

登录平台后打开应用更新，点击“检查更新”，或通过已认证的 `POST /api/update/check` 检查：

| 结果 | 含义 |
| --- | --- |
| `center_configured=false`、`missing_configuration` 非空 | 平台进程缺少列出的环境变量；没有执行远端检查 |
| `error` 非空 | 检查失败，即使 HTTP 为 200 也不能当作成功 |
| 中心 HTTP 401 | 发行读取令牌不匹配 |
| 中心 HTTP 503 | 中心可能未配置合格的读取令牌；由中心运维核对 |
| TLS 验证失败 | 中心证书/主机名/有效期或平台 CA 信任需要核对 |
| 签名 KID 未知或验签失败 | 核对发行 keyring，不能改用授权 keyring 或跳过验签 |
| `error=null`、`checked_at` 非空、`latest_version=null` | 中心没有适配当前 Linux CPU 架构的已发布包 |
| `latest_version` 等于 `current_version` | 已检查成功，但没有更高版本 |
| 有新版本、`install_available=false` | 可以检查；该实例没有配置自动安装 |

**版本比较只看 PEP 440 版本号，不看上传时间、提交号或 ZIP 内容。** 如果已安装 `0.1.0`，发布另一个 `maitu-0.1.0.zip` 不会成为新版本。下一次正式发行应统一递增 `pyproject.toml`、锁文件和包元数据后，用 `scripts/package_release.py` 构建。不要仅改 ZIP 文件名。

`install_available` 只有在 `updater-ready.json` 心跳有效时才为真；只有队列目录而没有消费者时不会允许排队。

## 5. 原生升级事务的边界

原生更新器会：

1. 校验签名 manifest、下载 ZIP，并核对大小与 SHA-256；
2. 在新的 `state/releases/<id>` 解压，创建独立 `.venv`，安装锁定依赖并构建 dashboard；
3. 停止原 systemd 服务，把整个数据目录归档，可选执行 PostgreSQL `pg_dump`；
4. 原子替换 `current-release.env`，启动服务并要求 `/api/health` 报告**目标版本**；
5. 失败时停止服务、恢复旧 release env 和备份数据，把失败数据保留到 `state/failed-data-*`，启动旧版本并做健康检查；事务中断后重启 updater 也会执行恢复；
6. 成功后写 `upgrade-state.json` 和 updater 心跳。

这仍不是“无风险升级”。大工作区会增加备份时间；外部 PostgreSQL 必须通过 `--postgres-dsn-file` 提供备份凭据；前端构建需要 `npm` 和可用的 Node.js；如果旧安装的 `ExecStart` 有自定义参数或用户级 systemd unit，当前安装器会拒绝/不适用，需要先按实际 unit 手工设计，不能直接套用。

`--native-prepare` 仍保留给不想启用自动队列的管理员，但它会修改原虚拟环境且不自动切换 systemd，不能当作自动升级的替代验证。

在没有确认实际服务名称、路径、备份及恢复方式前，不直接执行安装或升级。
