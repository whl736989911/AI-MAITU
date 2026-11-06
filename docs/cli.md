# CLI Reference

`octop` is a single Click app installed as `octop` on `pip install octop`.
It is registered in `src/octop/cli/main.py`; commands are loaded
lazily from `src/octop/cli/registry.py`.

```
$ maitu --help
Usage: maitu [OPTIONS] COMMAND [ARGS]...

  麦途 command-line interface.

Options:
  -v, --version           Show the installed maitu version.
  --user TEXT             Default --user for subcommands (admin acting on behalf
                          of a user). [env: MAITU_USER]
  --agent TEXT            Default --agent for subcommands. [env: MAITU_AGENT]
  --json                  Emit machine-readable JSON for list-style commands.
  -h, --help              Show this message and exit.

Commands:
  acp        Run 麦途 agent as ACP server (stdio).
  admin      Admin commands.
  agent      Agent lifecycle commands.
  backup     Export and restore 麦途 backups.
  chat       [deprecated] Alias for `maitu chats`.
  chats      Chat REPL and session management.
  channel    Channel management commands.
  clean      Remove CLI state or wipe all of ~/.maitu.
  completion Shell completion utilities.
  config     CLI state (base URL, defaults).
  cron       Cron job management commands.
  doctor     Audit runtime dependencies and install missing OS packages.
  init       Bootstrap an 麦途 server install.
  models     Model catalog and active-model settings.
  plugin     Install and manage plugins.
  provider   Provider management (admin write).
  run        Run octop-server in the foreground.
  service    System service lifecycle (start/stop/restart/status).
  skills     Per-agent skill enable/disable.
  storage    Inspect storage usage without changing user data.
  update     Check for and install a newer 麦途 release.
  user       User management commands.
  version    Show the installed maitu version.
```

> **Tip.** Regenerate the per-subcommand listings below with
> `make docs-cli` (each `maitu <cmd> --help` is captured to stdout).

## Global options

| Option | Env | Effect |
|--------|-----|--------|
| `--user NAME` | `MAITU_USER` | Default `--user` for subcommands (admin acting on behalf of a user) |
| `--agent ID` | `MAITU_AGENT` | Default `--agent` for subcommands |
| `--json` | — | Emit machine-readable JSON for list-style commands |
| `-v, --version` | — | Print version and exit |

## Transport layers

麦途 commands pick one of three transports:

| Layer | When | Login? | Examples |
|-------|------|--------|----------|
| **Offline** (local DB only) | Need to read/write `~/.maitu` without a running server | No | `init`, `backup`, `plugin`, `agent list`, `chats list/get/create/update/delete`, `cron list`, `user *`, `admin overview/audit`, `models presets/list/active` |
| **Attach** (HTTP / WS) | Need a live `maitu run` process (IM, streams, model pulls) | Yes (`maitu user login`) | `chats send/repl`, `channel test/probe`, `models ollama-*`, `skills enable/disable`, `provider test` |
| **Embedded** (in-process) | CLI boots `麦途Server` for a single command | No | `maitu acp`, `maitu chats repl`, `maitu chats send` (defaults to embedded), `maitu agent create/from-expert/start/stop/reload` |

The dashboards and HTTP callers manage their own JWTs and do **not**
share `~/.maitu/cli_state.json`.

## `maitu init`

Bootstrap a fresh install (DB migrations, JWT secret, first admin).
Idempotent on the DB; pass `--force` to wipe `~/.maitu` first.

```
Usage: maitu init [OPTIONS]

  Bootstrap an 麦途 server (~/.maitu dir, DB migrations, JWT secret, first admin).

Options:
  --admin-username TEXT       [env: MAITU_ADMIN_USERNAME]
  --admin-password TEXT       [env: MAITU_ADMIN_PASSWORD]
  --admin-display-name TEXT   [env: MAITU_ADMIN_DISPLAY_NAME]
  --force                     Wipe existing ~/.maitu contents before bootstrapping.
  --yes                       Skip all interactive prompts.
  -h, --help                  Show this message and exit.
```

## `maitu run`

Start the FastAPI app in the foreground (delegates to
`octop.launch.run_foreground_blocking`).

```
Usage: maitu run [OPTIONS]

  Start the 麦途 server (foreground; uvicorn).

Options:
  --host TEXT                 Override MAITU_BIND_HOST.
  --port INTEGER              Override MAITU_PORT.
  --reload / --no-reload      Enable uvicorn auto-reload (dev only).
  --ssl / --no-ssl            Enable HTTPS with a self-signed cert (or a real one).
  --certfile PATH             TLS certificate (PEM).
  --keyfile PATH              TLS private key (PEM).
  --log-level [debug|info|warning|error]
  -h, --help                  Show this message and exit.
```

## `maitu service`

Install and manage the 麦途 system service. The backend is detected
automatically (systemd on Linux, launchd on macOS); the scope
(`user` vs `system`) can be forced via `--scope` or
`MAITU_SERVICE_SCOPE`.

```
Usage: maitu service [OPTIONS] COMMAND [ARGS]...

  Manage the 麦途 system service (systemd on Linux, launchd on macOS).

Commands:
  start     Install (if missing) and start the service.
  stop      Stop the service.
  restart   Restart the service.
  status    Print whether the service is installed / running / healthy.
```

`status` probes the HTTP health endpoint and prints journal/log hints
on failure.

On Linux, `start` and `restart` install a systemd drop-in
(`octop.service.d/10-nofile.conf`) with `LimitNOFILE=65535` (user
units are capped to the process hard rlimit so systemd cannot fail to
spawn). The main unit file is not rewritten unless it is missing or
you pass `--force-install`. `restart` always runs `systemctl restart`
even if writing the drop-in fails, so an upgrade that only restarts
still comes back. To undo: delete the drop-in, then
`systemctl daemon-reload && systemctl restart maitu`.

## `maitu user`

Local-DB user management. `login` requires a running server; the
rest are offline.

```
Usage: maitu user [OPTIONS] COMMAND [ARGS]...

  User management commands (local DB; no server login required).

Commands:
  create    Create a new user.
  list      List all users.
  passwd    Reset a user's password (admin).
  role      Set a user's role.
  disable   Disable a user.
  delete    Delete a user.
  login     Login against a running server; stores JWT for optional remote HTTP attach.
```

## `maitu agent`

Lifecycle and templates. Most commands boot an embedded
`麦途Server` to operate on the local DB without a remote round-trip.

```
Usage: maitu agent [OPTIONS] COMMAND [ARGS]...

  Agent lifecycle commands.

Commands:
  create        Create a new agent.
  from-expert   Create an agent from a bundled expert template.
  list          List agents.
  use           Pin this agent as the default for subsequent commands.
  start         Start the agent runtime.
  stop          Stop the agent runtime.
  reload        Reload the agent runtime.
  delete        Delete the agent row (and runtime).
  experts       List bundled expert templates.
```

`create` and `from-expert` accept `--user` (admin only) to create
agents on behalf of another user. `use` writes the agent id into
`~/.maitu/cli_state.json` so subsequent commands default to it.

## `maitu chats`

Thread CRUD + interactive REPL. The REPL and `send` use an embedded
server (no separate `maitu run` needed); `list` / `get` / `create` /
`update` / `delete` work fully offline against `~/.maitu/maitu.db`.

```
Usage: maitu chats [OPTIONS] COMMAND [ARGS]...

  Thread list/history and interactive chat (CLI channel / local DB).

Commands:
  list        List conversation threads for an agent.
  get         Show message history for a thread.
  create      Start a new thread (/new equivalent).
  update      Rename or pin a thread.
  delete      Delete a thread.
  send        Send one message and stream the response.
  repl        Interactive chat REPL (embedded server + CLI gateway channel).
```

`maitu chat` is a deprecated alias that prints a stderr warning and
forwards to `chats`.

## `maitu channel`

Local DB channel CRUD plus platform-specific bot creators. The
`wecom` / `weixin` / `feishu-setup` subcommands drive the QR-code
bot-creator flows; `config` is the offline config editor.

```
Usage: maitu channel [OPTIONS] COMMAND [ARGS]...

  Channel management commands.

Commands:
  list          List channels for an agent.
  get           Show one channel row.
  create        Create a channel.
  patch         Update a channel.
  delete        Delete a channel.
  test          Test a channel (instantiate → start → stop).
  config        Edit channel config offline.
  feishu-setup  Drive the Feishu bot-creator flow.
  bind          Bind-group helper for WeCom / Weixin (QR login, etc.).
```

## `maitu cron`

Local DB cron management. `run-now` requires a running `maitu run`
because the actual fire is dispatched through the live
`CronManager`.

```
Usage: maitu cron [OPTIONS] COMMAND [ARGS]...

  Cron job management commands.

Commands:
  list        List cron jobs for an agent.
  create      Create a cron job.
  delete      Delete a cron job.
  run-now     Trigger a cron job immediately.
```

`create --trigger` accepts cron expressions (`"0 9 * * *"`) and the
`interval:N` / `date:ISO8601` aliases. `--task-type text` pushes the
prompt verbatim; `--task-type agent` (default) runs the LLM and
pushes the reply. `--prompt` is required, must be non-empty and ≤
2000 characters.

## `maitu provider`

Local DB provider CRUD. `test` requires a running server.

```
Usage: maitu provider [OPTIONS] COMMAND [ARGS]...

  Provider management commands (local DB).

Commands:
  list      List providers.
  create    Create a provider.
  delete    Delete a provider.
  test      Ping the provider (requires running server).
```

## `maitu models`

Provider presets and active model management.

```
Usage: maitu models [OPTIONS] COMMAND [ARGS]...

  Model catalog and active-model settings.

Commands:
  presets       List built-in provider templates from harness-agent.
  list          List all resolved models across enabled providers.
  active        Show or set the global default model (admin).
  config        Interactively create a provider from presets and set the active model.
  ollama-list   List local Ollama models (requires a running server).
  ollama-pull   Pull a model via Ollama (requires a running server).
  ollama-rm     Remove a local Ollama model (requires a running server).
```

## `maitu skills`

Per-agent skill enable / disable. All subcommands need a running
server (the dashboard's Skill Hub and bundled `~/.maitu/skills/`
library are queried at boot).

```
Usage: maitu skills [OPTIONS] COMMAND [ARGS]...

  Manage agent skills (enable / disable / list).

Commands:
  list      List skills for an agent.
  enable    Enable a skill for the agent.
  disable   Disable a skill for the agent.
  config    Show / edit the per-agent skills config.
```

## `maitu admin`

Local DB admin operations. `rotate-jwt-secret` works without a
running server (it edits the SQLite secret row directly).

```
Usage: maitu admin [OPTIONS] COMMAND [ARGS]...

  Admin commands (local DB).

Commands:
  overview             Show admin overview (user count, agent state distribution).
  audit                Show audit log entries.
  providers            Global (admin) providers.
  rotate-jwt-secret    Rotate the JWT secret directly via the local DB.
```

## `maitu backup`

Export and restore 麦途 backups. Works fully offline.

```
Usage: maitu backup [OPTIONS] COMMAND [ARGS]...

  Backup and restore database + local agent workspaces.

Commands:
  create     Create a backup archive (DB + workspaces + config).
  restore    Restore a backup archive into ~/.maitu.
  auto       Automatic backup status and one-shot run.
```

### `maitu backup auto`

Automatic backups are scheduled **inside** a running `maitu run` process
(via the same system-job mechanism as TLS renewal). Configure in
`config.json` under `backup`, with env overrides
`MAITU_BACKUP_AUTO_ENABLED`, `MAITU_BACKUP_SCHEDULE`,
`MAITU_BACKUP_RETENTION_COUNT`, or via the dashboard Backup settings.

```
Usage: maitu backup auto [OPTIONS] COMMAND [ARGS]...

Commands:
  status  Print automatic backup settings from config.json.
  run     Create one automatic backup now and prune by retention.
```

Default schedule when enabled: `cron:0 4 * * *` (daily 04:00, server
timezone). Automatic archives are named `maitu-auto-backup-*.tar.gz`;
retention only deletes those files and never touches manual
`maitu-backup-*.tar.gz` archives.

## `maitu storage`

只读查看当前实例的文件占用，不需要运行中的服务或登录，也不会执行旧目录迁移、创建配置、删除文件或压缩数据库。

```bash
maitu storage status
maitu storage status --home /path/to/instance
maitu storage status --json
```

`octop storage status` 是同一入口。`--home` 必须写在 `status` 后；未指定时按 `MAITU_HOME`、兼容的 `OCTOP_HOME`、默认 `~/.maitu` 解析实例目录。只读盘点不会把旧的 `~/.octop` 自动迁到新目录，需要查看旧目录时请显式传入 `--home`。

输出按临时文件、预览、日志、备份、知识库、工作区、数据库、已下载模型和其他文件分类计数，数据库文件及其 sidecar 不重复计入工作区。符号链接与 Windows junction 不会被遍历；实例目录外的数据库、自定义工作区及远程存储不属于本地盘点范围。读取失败会出现在报告中，不能把失败当成零占用。

该命令不是 `maitu clean --all` 的别名，不提供“清空工作区”操作。自动维护只处理明确归属的平台临时目录、可重建预览和已识别的日志归档；原件、历史附件、交付文件、模型及手动备份保留。详见 [运行时存储生命周期](configuration.md#runtime-storage-lifecycle)。

## `maitu update`

Check for and install a newer 麦途 release from the configured
channel (PyPI by default).

```
Usage: maitu update [OPTIONS]

  Check for and install a newer 麦途 release.

Options:
  --check / --no-check     Only check; do not install.
  --channel TEXT           Release channel (pypi, testpypi, custom index).
  -h, --help               Show this message and exit.
```

## `maitu plugin`

Install and manage third-party plugins (drop into `~/.maitu/plugins/`).
The same plugin manager powers the dashboard's "Plugins" page.

麦途 also **seeds bundled plugins** into `~/.maitu/plugins/` during
`maitu init` and every `maitu run` start. They are listed in Admin →
Plugins but stay **globally disabled** until you turn them on. An id
already recorded in `config.json` → `bundled_plugins_seeded` is not
copied again after uninstall.

```
Usage: maitu plugin [OPTIONS] COMMAND [ARGS]...

  Install and manage plugins.

Commands:
  list       List installed plugins.
  install    Install a plugin (local path or git URL).
  uninstall  Remove an installed plugin.
  reload     Reload all installed plugins without restart.
```

## `maitu acp`

Expose an 麦途 agent as a stdio JSON-RPC ACP server. Boots a
standalone `麦途Server` (reads `~/.maitu`); does **not** require
`maitu run` to be running.

```
Usage: maitu acp [OPTIONS]

  Run 麦途 agent as ACP server (stdio).

Options:
  --agent TEXT   Agent to expose (default: MAITU_AGENT or first agent).
  --debug        Log to stderr.
  -h, --help     Show this message and exit.
```

See [ACP integration](./acp.md) for the Zed setup example and the
runner object schema.

## `maitu clean`

Reset CLI state or wipe the whole `~/.maitu` tree. Destructive — read
the help carefully.

```
Usage: maitu clean [OPTIONS]

  Remove CLI state or wipe all of ~/.maitu.

Options:
  --state / --all       What to remove.
  --yes                 Skip the confirmation prompt.
  -h, --help            Show this message and exit.
```

## `maitu config`

Inspect / edit `~/.maitu/cli_state.json` (default base URL, user,
agent).

```
Usage: maitu config [OPTIONS] COMMAND [ARGS]...

  CLI state (base URL, defaults).

Commands:
  show       Print the current CLI state.
  set-user   Pin the default --user.
```

## `maitu completion` / `maitu version`

```text
$ maitu completion show bash   # dump a shell snippet
$ maitu completion install     # append to ~/.zshrc / ~/.bashrc
$ maitu version                # print the installed maitu version
```

## CLI state file

`~/.maitu/cli_state.json` stores:

```json
{
  "base_url": "http://127.0.0.1:8088",
  "token": null,
  "default_user": null,
  "default_agent": null
}
```

The path is exposed as `octop.cli.support.state.default_state_path()`
and can be overridden via `MAITU_HOME`. Delete the file to log out
the CLI without hitting the server. The dashboard and HTTP callers
do not share this file — they manage their own tokens.
