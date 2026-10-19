# Configuration

麦途智造 stores all of its state under `~/.maitu/`. The directory is created
on first server start (or by `maitu init` / `maitu run`).

## Filesystem layout

```
~/.maitu/
├── config.json              # process-level settings (host, port, CORS, DB, TLS, …)
├── env                      # optional dotenv (MAITU_DATABASE_*, API keys, …); loaded at server start
├── maitu.db                 # SQLite — users, agents, providers, sessions, audit
├── cli_state.json           # CLI token + pinned defaults (`maitu user login`)
├── repl_history             # readline-style history for `maitu chats repl`
├── secrets/
│   └── jwt_secret           # 32-byte random; rotate with `maitu admin rotate-jwt-secret`
├── agents/<agent_id>/       # per-agent workspace (LangGraph state, attachments, …)
├── plugins/                 # installed third-party plugins
├── ssl/                     # self-signed certs when `maitu run --ssl` is used without --cert/--key
├── logs/                    # rotating log files (when `maitu service start` writes a logfile)
└── maitu.log                # default foreground log path
```

`env` is dotenv format, applied before `config.json` is loaded
(`麦途智造Server.start` → `apply_env_file`). Dashboard **Advanced → Environment
variables** writes this file. Saving **replaces** the file and drops removed
keys from the process environment (host/systemd keys that were never in the
file are left alone). Every running agent inherits those keys:

- **Local shell** — live process env (including this file) plus the agent's
  workspace `.env` (workspace wins on the same key; `MAITU_*` / `HOME` / `USER`
  cannot be overridden from `.env`). New agents store that file at
  `{workspace}/.maitu/.env`; existing agents keep `{workspace}/.env` when
  their stored internal system-files prefix uses the legacy layout. Remote
  object-store workspaces are not read from the host path — 麦途智造/harness
  also reads `.env` through the agent backend on each execute. 麦途智造 also
  injects `MAITU_AGENT_ID`, `MAITU_AUTH_DIR` (under `{workspace}/.maitu/auth`
  for new agents; legacy agents keep `{workspace}/.maitu-auth`),
  and `MAITU_HOME` into shell/sandbox env.
- **Docker sandbox** — re-reads `~/.maitu/env` on execute, plus workspace `.env`
  and a minimal `PATH` (not the full host environment). Admin `PATH`/`HOME`
  cannot override the container toolchain; workspace `.env` may set `PATH`.
- **Web search tools** (Tavily, Brave, …) register at agent start from process
  env. Put search API keys in the global file, not only in an agent's `.env`.
  Changing search keys triggers a background agent reload; other keys take
  effect on the next execute without reload.

Per-agent secrets belong in `{workspace}/.env` or, for new agents,
`{workspace}/.maitu/.env` (also writable via the `write_env_file` tool).
That file is excluded from published-expert snapshots.

The root can be overridden with `MAITU_HOME` (absolute path). Most
sub-paths are exposed as properties on `PathLayout` in
`octop.infra.utils.paths`.

## Deployment brand and runtime identity

Set `MAITU_BRAND_CONFIG_FILE` to a deployment-owned JSON file before starting
the process. Docker reads its bundled `/app/brand.config.json` by default;
`docker/docker-compose.branding.yml` mounts an external brand file read-only,
so changing company identity does not require editing Python source.

```json
{
  "name": {"zh": "公司 AI 平台", "en": "Company AI Platform"},
  "full_name": {"zh": "公司全称", "en": "Company Limited"},
  "short_name": {"zh": "公司简称", "en": "Company"}
}
```

`name` is the platform display name, `full_name` is the company identity, and
`short_name` is the short display name. Existing dashboard branding settings
in the database override these deployment defaults; resetting branding returns
to the deployment file. File defaults load at process startup, so restart after
changing the file. Dashboard branding changes update subsequent runtime messages.
Malformed explicitly configured files fail visibly rather than silently selecting
a different brand.

Agent model requests receive the configured platform identity in the current
turn's locale. Platform-owned prompt tokens, bundled expert/skill descriptions,
tool labels, slash status/help, CLI version text, API documentation, and bot
creation greetings use that identity. Existing user SOUL files and historical
messages are not rewritten. An LLM's final wording is not guaranteed merely by
changing its system instructions.

Package names, API fields, environment keys (`MAITU_*`, `MAITU_*`), actual commands,
storage paths, and upstream copyright/license notices remain technical identifiers.
Do not globally replace them: that breaks integrations and makes upstream merges
unnecessarily difficult. Frontend static assets and PWA build-time copy continue
to use the existing `scripts/apply_brand.py` workflow.

After merging upstream updates, check new platform-owned display copy for the same
branding helpers/tokens. Technical compatibility reduces merge churn; it does not
guarantee that every future upstream change merges without conflicts.

On license-managed installs, business environment settings cannot replace or remove
deployment trust/configuration (`MAITU_*`) or host interpreter/loader settings such
as `PATH`, `PYTHONPATH`, and `LD_PRELOAD`. Configure those in the operator's service
environment instead, outside the writable application data directory.

## `config.json`

Generated with defaults on first run; merged with environment overrides
on each start. Schema (`麦途智造Config` in `octop/config.py`):

```json
{
  "bind_host": "127.0.0.1",
  "port": 8088,
  "log_level": "info",
  "access_token_ttl_seconds": 86400,
  "login_max_attempts": 5,
  "login_lockout_seconds": 900,
  "cors_origins": [],
  "default_timezone": "Asia/Shanghai",
  "enable_dashboard": true,
  "enable_api_docs": false,
  "require_setup_password": true,
  "max_upload_mb": 100,
  "database": {
    "driver": "sqlite",
    "sqlite_path": "maitu.db",
    "host": "127.0.0.1",
    "port": 5432,
    "database": "maitu",
    "user": "maitu"
  },
  "tls": {
    "enabled": false,
    "mode": "",
    "domains": [],
    "cert_file": "",
    "key_file": "",
    "issued_at": "",
    "expires_at": "",
    "acme_staging": false,
    "http_port": 80
  }
}
```

Notes:

- `default_timezone` is the process default for dashboard timestamps, cron
  scheduling, and harness. Legacy `cron_timezone` in `config.json` and
  `MAITU_CRON_TIMEZONE` are still accepted; the new key/env wins when both
  are set.
- `database.password`：向导用离散字段配置 PostgreSQL 时**可能**写入
  `config.json`（便于本机首次启动）。生产环境更推荐只用
  `MAITU_DATABASE_PASSWORD` 或带密码的 `MAITU_DATABASE_URL`，并限制
  `config.json` 文件权限。环境变量始终覆盖文件中的同名配置。
- `enable_api_docs=false` keeps `/api/docs` (Scalar) off in production
  while still serving `/api/openapi.json` to the dashboard.
- `require_setup_password=true` adds the wizard password gate to the
  first-run setup flow; set `false` for unattended bootstraps via
  `MAITU_ADMIN_USERNAME` / `MAITU_ADMIN_PASSWORD`.
- `max_upload_mb` is the process-wide ceiling for dashboard chat
  attachments, IM inbound files, and knowledge-base documents (default
  100). Existing `config.json` files that omit the key pick up the
  default on load. Change it and restart; reverse proxies may still
  impose their own body-size limit (for example nginx
  `client_max_body_size`). Agent workspace file upload, plugin ZIPs,
  and backup archives use separate limits and are not this setting.
- `plugins.<id>.enabled` is the **global** plugin switch (Dashboard Admin →
  Plugins). Bundled plugins are copied into `~/.maitu/plugins/` on init and
  server start with `enabled: false`. `bundled_plugins_seeded` lists ids
  already offered so uninstall does not come back on the next start.

## Environment overrides

Each variable, when set, takes precedence over the matching key in
`config.json`. Unset variables leave the on-disk value untouched.

| Variable | Type | Default | Effect |
|----------|------|---------|--------|
| `MAITU_HOME` | path | `~/.maitu` | Install root (DB, secrets, workspaces, plugins) |
| `MAITU_BIND_HOST` | string | `127.0.0.1` | Listen address (use `0.0.0.0` for LAN access) |
| `MAITU_PORT` | int | `8088` | Listen port |
| `MAITU_LOG_LEVEL` | string | `info` | One of `debug` `info` `warning` `error` |
| `MAITU_LOG_RETENTION_DAYS` | int | `14` | Keep rotated `maitu.log.YYYY-MM-DD` files for this many days |
| `MAITU_LOG_MAX_BYTES` | int | `104857600` (100 MiB) | Roll the active log when it exceeds this size (in addition to daily rotation) |
| `MAITU_LOG_COMPRESS` | bool | `true` | logrotate-style `compress` + `delaycompress`: gzip prior rotated plains on the next cycle (`maitu.log.YYYY-MM-DD.gz`); the newest rotated file stays uncompressed until then |
| `MAITU_ACCESS_TOKEN_TTL` | int (seconds) | `86400` | JWT access-token lifetime |
| `MAITU_LOGIN_MAX_ATTEMPTS` | int | `5` | Failed-login attempts before lockout |
| `MAITU_LOGIN_LOCKOUT_SECONDS` | int | `900` | Lockout duration after `MAITU_LOGIN_MAX_ATTEMPTS` failures |
| `MAITU_CAPTCHA_PROVIDER` | slug | `slider` | Login captcha (`slider`, `turnstile`, `hcaptcha`, `recaptcha-v3`, `tencent`; `recaptcha` v2 stays resolvable for existing configs but is unlisted). Boot snapshot; restart after change |
| `MAITU_CAPTCHA_SITE_KEY` | string | empty | Public site key (Tencent: CaptchaAppId; required when the env snapshot is a strong provider) |
| `MAITU_CAPTCHA_SECRET` | string | empty | Siteverify secret (Tencent: AppSecretKey; never logged; `GET /api/envs` redacts it) |
| `MAITU_CAPTCHA_CAM_SECRET_ID` | string | empty | Tencent only: CAM API SecretId signing `DescribeCaptchaResult`; required when the env snapshot is `tencent` |
| `MAITU_CAPTCHA_CAM_SECRET_KEY` | string | empty | Tencent only: CAM API SecretKey; never logged; `GET /api/envs` redacts it |
| `MAITU_CAPTCHA_V3_MIN_SCORE` | float | `0.5` | Minimum `recaptcha-v3` score; admin UI is read-only |
| `MAITU_DEFAULT_TIMEZONE` | IANA tz | `Asia/Shanghai` | Default timezone for display, scheduling, and harness (`cron_timezone` / `MAITU_CRON_TIMEZONE` still accepted) |
| `MAITU_CORS_ORIGINS` | comma-sep list | empty | Permitted CORS origins for the dashboard / external callers |
| `MAITU_ENABLE_DASHBOARD` | bool | `true` | Serve the built React SPA at `/` |
| `MAITU_ENABLE_API_DOCS` | bool | `false` | Expose Scalar API docs at `/api/docs` |
| `MAITU_REQUIRE_SETUP_PASSWORD` | bool | `true` | Require wizard password during initial setup |
| `MAITU_MAX_UPLOAD_MB` | int | `100` | Max upload size in MiB for chat attachments, IM inbound, and knowledge documents (1–1024) |
| `MAITU_DATABASE_URL` | string | empty | Full DSN — overrides the `MAITU_DATABASE_*` fields below |
| `MAITU_DATABASE_DRIVER` | `sqlite` \| `postgresql` | `sqlite` | Storage backend |
| `MAITU_DATABASE_SQLITE_PATH` | path | `maitu.db` | SQLite file path (relative to `MAITU_HOME` unless absolute) |
| `MAITU_DATABASE_HOST` | string | `127.0.0.1` | PostgreSQL host (when `driver=postgresql`) |
| `MAITU_DATABASE_PORT` | int | `5432` | PostgreSQL port |
| `MAITU_DATABASE_NAME` | string | `maitu` | PostgreSQL database name |
| `MAITU_DATABASE_USER` | string | `maitu` | PostgreSQL user |
| `MAITU_DATABASE_PASSWORD` | string | empty | PostgreSQL password (overrides file; prefer env in production) |
| `MAITU_ADMIN_USERNAME` | string | empty | Pre-fills the first-admin username in `maitu init` |
| `MAITU_ADMIN_PASSWORD` | string | empty | Pre-fills the first-admin password in `maitu init` |
| `MAITU_ADMIN_DISPLAY_NAME` | string | empty | Pre-fills the admin display name |
| `MAITU_USER` | string | empty | Default `--user` for CLI subcommands |
| `MAITU_AGENT` | string | empty | Default `--agent` for CLI subcommands |
| `MAITU_SERVICE_MODE` | `systemd` \| `launchd` | auto | Override the service backend (used by `maitu service`) |
| `MAITU_SERVICE_SCOPE` | `user` \| `system` | auto | systemd `--user` vs system unit (Linux only) |

Invalid integer values are logged and ignored — the on-disk default
remains in effect. `database_env_configured()` returns `True` when any
`MAITU_DATABASE_*` is set, which lets `麦途智造Server.start()` pick the
configured backend at boot.

**Docker Compose:** put `MAITU_DATABASE_*` in `docker/.env` *and* ensure
they are listed under `environment:` in `docker/docker-compose.yml`
(Compose uses `.env` for interpolation only; unset keys do not enter the
container). Writing the same keys to the mounted `~/.maitu/env` also works.

### Agent memory vs control plane

Control-plane `database` and agent memory are separate layers. Defaults:

- Control plane SQLite → agent memory stays `{workspace}/memory.sqlite`
  (or `{workspace}/.maitu/memory.sqlite` for new agents).
- Control plane PostgreSQL → agent memory **defaults to the same DSN**
  (harness-memory per-agent PG schema `agent_<id>`). Runtime also needs
  ``langgraph-checkpoint-postgres`` (pulled in via
  ``harness-memory[langgraph-postgres]``) so LangGraph checkpoints work.
  To keep file memory while the control plane is PG, set on the agent:

```json
"memory": { "backend": { "type": "sqlite" } }
```

Or point at another DSN with `"type": "postgres", "dsn": "…"`. There is
no automatic SQLite→PG memory data migration.

PostgreSQL **extensions** (e.g. `vector`) are instance-level ops, not
control-plane migrations. Local compose enables `vector` via
`docker/postgres/init-vector.sql`; managed databases need a DBA /
provider toggle. See [ADR 002](./adr/002-database-backends.md).

## First-boot wizard

The first request to a fresh install lands on the setup page. Greenfield
SQLite installs **defer** opening the control-plane DB until the database
step; password verification works without a pool. The modern flow uses
`/api/setup/*`:

1. `POST /api/setup/begin` (or `POST /api/setup/verify-password` when
   `require_setup_password=true`) — issues a short-lived wizard token.
2. `POST /api/setup/test-database` / `POST /api/setup/database` — choose
   SQLite or PostgreSQL, probe, persist `config.json`, bind pool + migrate.
3. `POST /api/setup/initial-admin` — creates the seed admin (requires DB).
4. `POST /api/setup/test-provider` — pings an optional provider draft.
5. `POST /api/setup/finish` — bootstraps default `main` agent and unlocks
   the rest of the API.

`GET /api/setup/status` returns `setup_required`, wizard password fields,
plus `database_bound` / `database_driver`. `setup_lockdown` middleware
blocks non-setup routes until the wizard completes.

For unattended installs, use `maitu init --yes` with
`MAITU_ADMIN_USERNAME` / `MAITU_ADMIN_PASSWORD` (and
`MAITU_REQUIRE_SETUP_PASSWORD=false` if the env-var path is used). This
runs the same migrations + admin creation without the HTTP wizard.

## Secrets

The JWT secret is generated on first start and stored in
`~/.maitu/secrets/jwt_secret`. Rotate it with:

```bash
maitu admin rotate-jwt-secret
```

Rotation invalidates every outstanding access token immediately. The
old secret is overwritten in place — no zero-downtime rotation today.

Per-agent provider credentials (e.g. API keys) live in the SQLite
`providers` table and are surfaced through
`infra/connectors/credential_crypto.py` for connector OAuth flows.

## 可选分段历史归档

`history_v2_enabled` 默认 `false`，可用 `MAITU_HISTORY_V2_ENABLED=true` 开启。
该开关只决定下一完整回合的写入格式，关闭后仍读取已经保存的新格式。
启用前请阅读 [分段历史归档与回退](versioned-history.md)，尤其是配套版本与备份恢复边界。

## Execution sandbox (native installs)

On POSIX source installs, agent `execute` runs inside a bubblewrap directory
jail rooted at the agent workspace. The jail mounts the platform Python
environment read-only (`sys.prefix` plus the real interpreter runtime behind
it, e.g. a uv-managed interpreter under `~/.local/share/uv`) and prepends its
`bin` to `PATH` / appends its `site-packages` to `PYTHONPATH`, so skill
scripts resolve the platform interpreter instead of the system one. Home,
project and license directories are never mounted. Host-rooted
(`root_dir: "/"`) or `virtual_mode: false` local shells keep executing on the
host by explicit choice.

### Diagnosis and installation

- `GET /api/filesystem/bwrap-status` (admin): read-only probe; never installs.
- `POST /api/filesystem/ensure-bwrap` (admin): explicit install attempt plus
  diagnosis. The server never installs packages on its own at startup.

Both return `{status, reason, detail, diagnosis, commands}`. `diagnosis` is
one of `missing_binary`, `install_permission_denied`, `userns_denied` (the
kernel / AppArmor / seccomp refused the user namespace — on Ubuntu 24.04+
seen as `bwrap: setting up uid map: Permission denied`), `runtime_failure`,
or `null` when healthy. `commands` lists minimal-privilege operator commands
(display only — nothing is executed automatically). The AppArmor templates
live in `scripts/security/`; `scripts/setup-host-sandbox.sh` applies and
reverts them.

### Fail-closed behavior

A scoped `local_shell` backend without a working `bwrap` fails explicitly —
at agent start and at execute time (`INSTANCE_DEPLOYMENT_RESTRICTED`). The
platform never silently downgrades a configured sandbox to host execution.
