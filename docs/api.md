# API Reference

Every route is mounted under `/api`. JSON unless otherwise noted; SSE
endpoints emit `text/event-stream`; chat turns use a WebSocket
described in [Chat (WebSocket)](#chat-websocket).

> **Interactive docs** (Scalar) live at `/api/docs` when
> `enable_api_docs=true` (or `MAITU_ENABLE_API_DOCS=1`). The schema
> is at `/api/openapi.json`.

## Authentication

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <access_token>` from `POST /api/auth/login` |

Tokens expire after `MAITU_ACCESS_TOKEN_TTL` seconds (default 24 h).
Rotating the JWT secret (`maitu admin rotate-jwt-secret`) invalidates
every outstanding token immediately. Login attempts are rate-limited
(`MAITU_LOGIN_MAX_ATTEMPTS` / `MAITU_LOGIN_LOCKOUT_SECONDS`); the
admin can clear the lockout with `POST /api/users/{id}/unlock-login`.

### Auth column legend

- **public** — no token required.
- **user** — any logged-in account.
- **owner** — same user that owns the resource (or admin).
- **admin** — admin role required.

### Public endpoints (no token)

`/api/docs`, `/api/openapi.json`, `/api/health`, `/api/license/status`, `/api/setup/*`,
`/api/auth/login`, `/api/auth/captcha`, `/api/auth/oidc/status`, `/api/auth/oidc/start`,
`/api/auth/oidc/callback`, `/api/auth/oidc/exchange`, `/api/auth/oauth/status`,
`/api/auth/oauth/start`, `/api/auth/oauth/callback`, `/api/auth/oauth/exchange`,
`/api/connectors/oauth/callback`, and `/api/internal/mcp/*`. All other routes are JWT-gated by
`api/middleware/jwt_auth.py`; the setup lockdown middleware
(`api/middleware/setup_lockdown.py`) additionally blocks non-setup
routes until the wizard finishes.

Password login may require a vendor captcha token. `GET /api/auth/captcha`
returns `{provider: "slider"}` by default (dashboard slider only; no server
check). When a strong provider is active (`turnstile`, `hcaptcha`,
`recaptcha`, `recaptcha-v3`, `tencent`), `POST /api/auth/login` must include
`captcha_token` (max 4096 characters; for `tencent` the dashboard sends the
callback pair as `ticket:randstr`, verified via GET with the client IP).
Captcha failures do not increment
login lockout. OIDC login is unchanged. A request for an unknown username
with a garbage token still triggers one outbound siteverify request (bounded
by a 10s timeout and the token length limit); a known locked user is
rejected before siteverify.

Offline recovery: delete the `captcha.settings` row via local CLI /
`settings_repo` if a settings-sourced strong provider is unreachable. If
the process refused to start because `MAITU_CAPTCHA_PROVIDER` is strong
without both keys and no readable settings blob, edit `~/.maitu/env` on
disk — the dashboard and `PUT /api/envs` are unreachable until the
process starts. `GET /api/envs` redacts `MAITU_CAPTCHA_SECRET` as
`********`; sending that sentinel on PUT keeps the on-disk value.

If you serve the dashboard behind a CSP, allow
`challenges.cloudflare.com`; `js.hcaptcha.com`, `newassets.hcaptcha.com`,
`api.hcaptcha.com`; `www.google.com`, `www.gstatic.com`;
`turing.captcha.qcloud.com`, `captcha.qq.com`, `ssl.captcha.qq.com`. 麦途智造
does not set these headers itself.

## Instance licensing

Instance authorization is independent of login and account permissions. An expired,
revoked, unactivated, or otherwise invalid instance rejects business reads, writes,
streams, and tasks with HTTP `403` and `INSTANCE_LICENSE_LOCKED`, including requests
with an existing administrator JWT. Login, license recovery, and the minimal setup
and maintenance routes remain available. Finishing setup does not grant a license.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/license/status` | public | Minimal state, lock reason, deadlines, operator contact, and server timezone; no instance identity or credentials |
| `GET` | `/license/details` | system/enterprise admin | Public instance ID and key fingerprint, accepted revision, and latest sync attempt/result; never returns private keys or signed credentials |
| `POST` | `/license/activate` | system/enterprise admin or pending first-run wizard | Body `{activation_code}`; one-time center registration and signed response validation; rate-limited |
| `POST` | `/license/refresh` | system/enterprise admin or pending first-run wizard | Body `{}`; sync with the operator-controlled center; rate-limited and single-flight |

A successful status refresh with `business_allowed: true` clears the dashboard's
previous lock signal, including renewal performed in another tab or by the
operator. Later lock events still close the business surface.

Customer host capabilities remain restricted even with a valid license. This includes
host terminal/filesystem access, deployment environment changes, custom executable
plugins, stdio MCP servers, and arbitrary host workspace policies. Business permissions
cannot override this deployment floor. See [deployment and recovery operations](instance-license-operations.md)
for trusted-key configuration, activation, renewal, revocation, and backup boundaries.

## Setup & auth

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/setup/status` | public | `{setup_required, admin_created, wizard_password_required, wizard_password_exists, wizard_password_path, database_bound, database_driver}` |
| `GET`    | `/setup/presets` | public | Provider templates for the wizard |
| `POST`   | `/setup/begin` | public | Begin a wizard session (no password) |
| `POST`   | `/setup/verify-password` | public | Exchange the wizard password for a session token |
| `GET`    | `/setup/validate-token` | public | Check a wizard session token |
| `POST`   | `/setup/initial-admin` | public | body `{username, password, display_name?, email?}` → `201` |
| `POST`   | `/setup/test-provider` | public | Ping a provider draft (kind/base_url/api_key/model) |
| `POST`   | `/setup/finish` | wizard token | body `{provider_draft?}`; persist completion only after provider configuration and bootstrap succeed; an invalid instance license explicitly defers business bootstrap |
| `GET`    | `/auth/captcha` | public | `{provider, site_key?}` for the login widget; 503 before the initial administrator is created |
| `POST`   | `/auth/login` | public | body `{username, password, captcha_token?}` (`username` may be email) → `{access_token, role, user, ...}` |
| `GET`    | `/auth/oidc/status` | public | OIDC login availability and provider display name |
| `POST`   | `/auth/oidc/start` | public | body `{redirect_after?}` → identity-provider authorization URL |
| `GET`    | `/auth/oidc/callback` | public | Identity-provider callback; redirects to dashboard login completion |
| `POST`   | `/auth/oidc/exchange` | public | body `{code}` → same JWT response as `/auth/login` |
| `GET`    | `/auth/oidc/config` | admin | OIDC provider configuration and callback URL; client secret is omitted |
| `PUT`    | `/auth/oidc/config` | admin | Write OIDC provider configuration; `client_secret` is write-only |
| `POST`   | `/auth/oidc/config/test` | admin | Verify configured discovery metadata and JWKS endpoint |
| `GET`    | `/auth/oauth/status` | public | `{providers:[{kind, display_name, enabled}]}` |
| `POST`   | `/auth/oauth/start` | public | body `{kind, redirect_after?}` → authorization URL |
| `GET`    | `/auth/oauth/callback` | public | App OAuth callback (`code` or DingTalk `authCode`); same completion page as OIDC |
| `POST`   | `/auth/oauth/exchange` | public | Same one-time code exchange as `/auth/oidc/exchange` |
| `POST`   | `/auth/oauth/bind/start` | user | body `{kind, redirect_after?}` → bind the identity to the current user |
| `POST`   | `/auth/oauth/unbind` | user | Unlink one SSO identity by `kind` (requires a local password when it is the last login method) |
| `GET`    | `/auth/oauth/providers/{kind}` | admin | Provider config; `kind` is `oidc`, `feishu`, `dingtalk`, or `wecom` |
| `PUT`    | `/auth/oauth/providers/{kind}` | admin | Upsert provider config; `client_secret` is write-only; WeCom uses `extra.agent_id` |
| `POST`   | `/auth/oauth/providers/{kind}/test` | admin | Test provider credentials |
| `POST`   | `/auth/logout` | user | `204` |
| `GET`    | `/auth/me` | user | `{id, username, role, display_name, locale, ...}` |
| `PATCH`  | `/auth/me` | user | body `{display_name?, locale?, ...}` |
| `POST`   | `/auth/change-password` | user | body `{old_password, new_password}` → `204` |
| `GET`    | `/health` | public | `{status: "ok", version}` |

Setup endpoints validate wizard credentials inside their handlers. After creating
the initial administrator, the dashboard retains that administrator's JWT to
complete setup even if the earlier wizard token expires. There is no public
resume-token minting endpoint.

Without retained credentials, the dashboard repeats the configured bootstrap
password challenge (or `/setup/begin` when explicitly disabled), then resumes
the saved model/finish step instead of creating another administrator.

Creating the administrator does not complete installation. Before that account
exists, login and captcha requests return `503 {"setup_required": true}`. After
it exists, authentication can resume the wizard, but normal business endpoints
remain unavailable while `setup_required` is true. A failed finish preserves the
pending state and wizard credentials for retry; retrying a saved provider draft
updates the same provider instead of creating duplicates.

## Users (admin)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/users` | admin | `[{id, username, role, display_name, email, enabled, ...}]` |
| `POST`   | `/users` | admin | body `{username, password, role, display_name?, email?, permissions?, workspace_root_dir?, token_quota?}` → `201` |
| `GET`    | `/users/{id}` | admin | full user row |
| `PATCH`  | `/users/{id}` | scoped user manager (`users`) | body subset of `{role, display_name, email, disabled, permissions, org_unit, denied_permissions, workspace_root_dir, token_quota}`; all account, policy and audit writes commit together after the whole patch is validated |
| `POST`   | `/users/{id}/reset-password` | admin | body `{new_password}` → `204` |
| `POST`   | `/users/{id}/unlock-login` | admin | `204` (clears the lockout) |
| `DELETE` | `/users/{id}` | admin | `204` |

Agent personalization has separate `skills`, `subagents`, `tools`,
`agent_plugins` and `memory` module grants. Existing accounts retain their
previous surfaces during migration unless explicitly denied; new accounts
require these grants to be assigned. Module grants do not override resource
ownership, feature workflow/shared-memory rules, or the licensed deployment floor.

## Agents

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents` | user | `[{id, agent_id, name, persona_mbti, state, unread_count, ...}]` |
| `POST`   | `/agents` | user | body `{name, persona_mbti?, default_model?, system_prompt?, description?, icon?, template_name?, config?}` → `201` |
| `GET`    | `/agents/{id}` | owner | full agent row |
| `PATCH`  | `/agents/{id}` | owner | body subset of create body |
| `DELETE` | `/agents/{id}` | owner | `204` |
| `POST`   | `/agents/{id}/start` | owner | `204` |
| `POST`   | `/agents/{id}/stop` | owner | `204` |
| `POST`   | `/agents/{id}/reload` | owner | `204` (rebuild harness runtime) |
| `POST`   | `/agents/{id}/read` | owner | `204` (mark unread badge cleared) |
| `GET`    | `/agents/{id}/status` | owner | `{state, last_error?, ...}` |
| `POST`   | `/agents/from-expert/{expert_id}` | user | body `{name, ...}` → `201` (creates from bundled expert template) |
| `GET`    | `/agents/{id}/tool-settings` | owner | built-in + installed plugin tools with enable / disableable / available flags |
| `PUT`    | `/agents/{id}/tool-settings` | owner | body `{disabled_builtin: string[], plugins?}` — persists denylist + plugin flags (hot-sync, no reload) |
| `PATCH`  | `/agents/{id}/tool-settings/{tool_name}` | owner | body `{enabled, source, plugin_id?}` — toggle one tool (hot-sync) |

Agents, bundled/published experts and features accept a default
`conversation_mode` (`ask`, `plan`, `craft`). A turn resolves its explicit mode
first, then the thread's saved mode, then the agent default, finally Craft.
An unset thread mode remains unset in list/history responses so the dashboard
can follow a changed agent default. The business-operations, productivity,
design and engineering templates are available to experts and feature creation.

## Skills and skill packages

Skill copy operations create snapshots; they do not keep the source and
destination synchronized after the request completes.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/skill-packages?writable_only=true` | user (`skill_packages`) | List only packages the current user may modify; admins may modify all packages |
| `POST` | `/agents/{id}/skill-packages/{package_id}/copy` | owner (`skill_packages`) | body `{skill_slugs: string[], overwrite?: boolean}`; copy selected package skills into the workspace |
| `POST` | `/agents/{id}/skills/{slug}/push-to-package` | owner (`skill_packages`) | body `{package_id, overwrite?: boolean}`; copy a workspace skill into a package created by the user (or any package for admins) |
| `POST` | `/agents/{id}/skills/export-zip` | skills access | body `{slugs: string[]}`; selected custom workspace skills only, including feature workspaces |
| `POST` | `/agents/{id}/skills/import-zip` | skills write | body `{archive_base64, overwrite?: boolean}`; validates the archive and all name collisions before writing |
| `GET` | `/skill-packages/{package_id}/export` | `skill_packages` + download right | ZIP; creator/system-admin exception, otherwise `allow_download` must be true |
| `POST` | `/skill-packages/{package_id}/import-zip` | package creator/admin | body `{archive_base64, overwrite?: boolean}` |

Package creation/update persists `allow_download`. The switch also gates the
package-to-workspace copy endpoint; ordinary mounting and use are unchanged.
This is a platform transfer control, not DRM: mounted skills remain readable to
the model and it cannot prevent copying content that is already accessible.
ZIP handling rejects unsafe paths, symlinks and oversized content and excludes
credential/system files from workspace exports.

## Chat (WebSocket)

| Path | Auth | Notes |
|------|------|-------|
| `WS /agents/{id}/chat/ws?token=<jwt>` | owner | Primary dashboard turn endpoint. Send `{"type":"user_turn", ...}` frames; server replies with harness stream chunks ending in `{"type":"done"}` or `{"type":"error","message":"..."}`. `{"type":"ping"}` → `{"type":"pong"}`. `{"type":"subscribe","thread_id"}` → `{"type":"turn_status","thread_id","active"}` (attach to an in-flight turn without cancelling on disconnect). `{"type":"cancel","thread_id"}` stops the active turn (explicit stop; disconnect alone does **not** cancel). |
| `GET /agents/{id}/chat/welcome` | agent access | `{welcome_message, quick_prompts, task_examples}`; `task_examples` is `null` when the workspace field is absent |
| `POST /agents/{id}/chat/polish` | owner | body `{text, default_model?}` → `{text}` (one-shot prompt refinement) |
| `POST /agents/{id}/chat/hitl/resume` | thread owner | body `{thread_id, pending_id, decisions: [...]}` → SSE; the exact current approval identity is required; stale/missing identities return `409 HITL_APPROVAL_STALE` |
| `PATCH /agents/{id}/threads/{thread_id}` | thread owner | persist `conversation_mode: "ask" \| "plan" \| "craft"` and/or `hitl_policy: {mode: "ask" \| "allow_all" \| "allow_tools", tools?: string[]}` on the thread |

Dashboard `user_turn` frames may carry `conversation_mode` and `hitl_policy` to
apply a per-turn change. Ask is read-only, Plan writes plans under `plans/*.md`,
and Craft enables ordinary tools. HITL bypass is scoped to that thread;
`ask_user_question` still requires interaction even under `allow_all`.

While a turn is active, a `user_turn` can specify `delivery: "steer"` or
`"follow_up"`, `client_pending_id`, and `expected_turn_id`. Steering enters at the
next safe model boundary, never in the middle of an executing tool. Follow-ups
wait for logical completion. The server acknowledges delivery and reports
consumed, removed, expired or failed items; `steer_remove` withdraws an item that
has not drained. HITL parks the same logical turn and resume retains its verified
user, feature workflow/private-memory scope and steering context. A restored
checkpoint requires a newly reconstructed approval card; an old card cannot
authorize a different restored action.

### Feature runs over the chat socket

A turn submitted from a feature's **input card** carries the run as a field of its own
on the `user_turn` frame:

```jsonc
{"type": "user_turn", "thread_id": "…", "text": "▶ 运行",
 "feature_run": {"inputs": {"customer_name": "ACME"}, "attachments": ["inbound/quote.pdf"]}}
```

The platform records the run (its values and the definition in force — see
`GET /agents/{aid}/workflow/runs`) and injects the values into that turn's prompt. It
is a **typed frame field rather than free-form metadata** on purpose: the same turn
also carries keys the server decides (`user_is_admin`, the resolved model), so what a
client may write has to stay a closed set.

### Legacy SSE

The previous `POST /agents/{id}/chat/stream` is gone. The dashboard
streams turns over the WebSocket above; HITL resume stays on SSE
because each request is a one-shot continuation.

## Threads & history

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{id}/chat/sessions` | owner | `[{id, thread_id, title, archived, last_active, unread, ...}]` |
| `POST`   | `/agents/{id}/chat/sessions` | owner | body `{session_key?}` → `{thread_id, session_key}` |
| `PATCH`  | `/agents/{id}/chat/sessions/{thread_id}` | owner | body `{title?, pinned?}` → updated row |
| `DELETE` | `/agents/{id}/chat/sessions/{thread_id}` | owner | `204` (archives the active row) |
| `GET`    | `/agents/{id}/chat/sessions/{thread_id}/history` | owner | paginated message history; `turn_active` tells a reconnecting client whether to re-`subscribe` over the chat WebSocket |
| `POST`   | `/agents/{id}/threads/{thread_id}/fork` | thread owner | body selects an assistant answer or a user turn; returns `201` with `{thread_id, session_key, source_thread_id, copied_messages, title, ...}` |

For an assistant-answer fork, use `message_id`, `content`, or
`assistant_turns_from_end` (1 is latest); the selected answer is included. For
editing or regeneration, use `user_message_id`, `user_message_content`, or
`user_turns_from_end`; only messages **before** that user turn are copied.
Assistant and user selectors cannot be mixed. The first user turn therefore
produces an empty branch. Before-user forks return 409 while the agent is busy
or the source has pending human approval. The source history is unchanged.

The dashboard loads the copied branch history, then sends the selected text and
original attachments to its new thread. A history-resume probe preserves an
existing connecting or open socket for that thread, so it cannot discard the
first outgoing turn. A rejected send retains the composer draft and attachments;
workflow cards are marked submitted only after the send is accepted. Branching
changes checkpoint history, not the separate reference-memory retrieval policy.

### Trajectory ledger

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory` | owner | paginated event summaries; use `before_seq` for older rows |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/events/{event_id}` | owner | full event payload |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/metrics` | owner | aggregated turn, timing, and token metrics |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/stream` | owner | live SSE events; resume with `after_seq` or `Last-Event-ID` |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/export` | owner | full ledger download as JSONL (default) or JSON |

## Channels

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{aid}/channels` | owner | list of channel rows |
| `POST`   | `/agents/{aid}/channels` | owner | body `{kind, name, config}` → `201` |
| `GET`    | `/agents/{aid}/channels/{cid}` | owner | channel row |
| `PATCH`  | `/agents/{aid}/channels/{cid}` | owner | body subset → updated row |
| `DELETE` | `/agents/{aid}/channels/{cid}` | owner | `204` |
| `POST`   | `/agents/{aid}/channels/{cid}/test` | owner | `{ok, error?}` (instantiate → start → stop) |
| `POST`   | `/agents/{aid}/channels/probe` | owner | `{ok, reason?, detail?}` — preflight a candidate config |
| `POST`   | `/agents/{aid}/channels/{platform}/qrcode/generate` | owner | platform-specific bot creator (wecom, weixin, feishu, yuanbao) |
| `POST`   | `/agents/{aid}/channels/{platform}/qrcode/poll` | owner | poll bot creator state |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/start` | owner | start a bot-creator flow |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/poll` | owner | poll progress |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/stop` | owner | stop an in-flight bot creator |

## Cron

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/settings/timezone` | user | process-level `{timezone}` from `default_timezone` |
| `GET`    | `/settings/upload` | user | `{max_upload_mb, max_upload_bytes}` from `max_upload_mb` |
| `GET`    | `/settings/captcha` | `captcha` | `{active, available, providers, source, v3_min_score}`; secrets omitted |
| `PUT`    | `/settings/captcha` | `captcha` | merge `{active?, providers?}`; empty secret keeps ciphertext; `null` removes a pair |
| `GET`    | `/cron/settings` | user | compat alias of `/settings/timezone` |
| `GET`    | `/agents/{aid}/cron/examples` | agent access | `{task_examples}` from workspace `.maitu/manifest.json` (`{zh,en}` string arrays, display-normalized to 3 or 6); `null` if the field is absent (dashboard keeps default cards). Prefer `GET /agents/{aid}/chat/welcome` which includes the same field. |
| `GET`    | `/agents/{aid}/cron` | owner only | list cron rows; non-owners (including admin) get `[]` |
| `POST`   | `/agents/{aid}/cron` | owner only | body `{name?, trigger, prompt, session_key?, fresh_thread?, enabled?, model?, task_type?}` → `201` |
| `GET`    | `/agents/{aid}/cron/{cid}` | owner only | cron row |
| `PATCH`  | `/agents/{aid}/cron/{cid}` | owner only | body subset → updated row |
| `DELETE` | `/agents/{aid}/cron/{cid}` | owner only | `204` |
| `POST`   | `/agents/{aid}/cron/{cid}/run-now` | owner only | `204` (fire immediately, off-schedule) |

`task_type` is `"text"` (push prompt directly to the session) or
`"agent"` (run the prompt through the LLM and push the reply).
Default: `"agent"`. `trigger` accepts cron expressions
(`"0 9 * * *"`) plus the `interval:N` / `date:ISO8601` aliases
documented in `infra/cron/trigger.py`. `prompt` must be non-empty and
≤ 2000 characters. `name` is an optional display label; when omitted,
the server derives one from `prompt`.

Date jobs are one-shot: an atomic persisted claim prevents scheduled/manual
races and restart replay. Success and failure both disable the job while
retaining the outcome. Missed date windows are not replayed at startup.

A supplied `session_key` must belong to the task's user and agent, both when
creating and updating the task. Execution checks that ownership before a
`fresh_thread` reset; an invalid binding cannot reset another user's thread.

## Providers

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/providers` | user | providers visible to the user (own + shared) |
| `POST`   | `/providers` | user | body `{name, kind, base_url?, api_key?, model?, ...}` → `201` |
| `PATCH`  | `/providers/{id}` | owner | body subset → updated row |
| `DELETE` | `/providers/{id}` | owner | `204` (refuses if any agent references it) |
| `POST`   | `/providers/{id}/test` | user | `{ok, latency_ms?, error?}` (one-token ping with 10 s timeout) |
| `POST`   | `/admin/providers` | admin | same body as user POST; row has `user_id = NULL` |
| `PATCH`  | `/admin/providers/{id}` | admin | as user PATCH but works on shared rows |
| `DELETE` | `/admin/providers/{id}` | admin | `204` |

Provider updates distinguish an omitted `api_key` (keep the stored credential),
JSON `null` (clear it), and a replacement string (replace it).

## Models

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/models/presets` | user | provider templates from `harness-agent` |
| `GET` | `/models` | user | resolved models across enabled providers |
| `GET` | `/models/active` | user | `{provider_name, model}` |
| `PUT` | `/models/active` | admin | body `{provider_name, model}` |


Automatic chat routing tracks model health and may try another eligible,
configured model before any visible output. Explicit per-turn, thread and agent
model choices are not silently switched. Fallback preserves provider/model
allowlists and vision requirements; a failure after visible streaming does not
replay the answer.

### Media generation models

These instance-wide endpoints require the `providers` permission (administrators bypass
permission checks). The Ark API key is write-only and encrypted at rest. Saving settings
reloads running agents so the image and video tools receive the new configuration.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/admin/media-generation` | providers | Return Volcengine Ark image/video settings; never returns the API key |
| `PUT` | `/admin/media-generation` | providers | Save enabled tools and Seedream/Seedance model IDs; an included API key is verified before saving |
| `POST` | `/admin/media-generation/test` | providers | Test credentials or a selected image/video model; model tests submit real, potentially billable requests |

## Voice

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`  | `/voice/presets` | user | voice provider presets |
| `GET`  | `/voice/providers` | user | user's voice providers |
| `GET`  | `/voice/active` | user | active TTS / STT configuration |
| `PUT`  | `/voice/active` | user | update active voice configuration |
| `POST` | `/voice/stt` | user | body `{audio, format?, language?}` → `{text, segments?}` |
| `POST` | `/voice/tts` | user | body `{text, voice?, format?}` → audio bytes |
| `GET`/`POST`/`PATCH`/`DELETE` | `/admin/voice/providers` | admin | admin voice provider CRUD |

## MBTI & personas

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/mbti/codes` | user | `[{code: "INTJ", ...}, ...]` (16 codes + `_default`) |
| `GET`    | `/mbti/codes/{code}` | user | full profile (dimensions, behaviour, UI metadata) |
| `GET`    | `/mbti/preview/{code}` | user | rendered persona template (legacy `/api/personas/{code}`) |
| `PUT`    | `/agents/{aid}/mbti` | owner | body `{code}` → apply persona and reload |
| `GET`    | `/personas` | user | `[{code}, ...]` (compat shim) |
| `GET`    | `/personas/{code}` | user | rendered template (compat shim) |

Persona content lives in `src/octop/infra/agents/mbti_profiles.py` —
see [Personas](./personas.md).

## Experts

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/experts` | user | bundled expert catalog (includes `task_examples` `{zh,en}` when present) |
| `GET`    | `/experts/{expert_id}` | user | full expert template (SOUL.md, skills, files, `task_examples`) |
| `POST`   | `/agents/from-expert/{expert_id}` | user | body `{name, locale?, ...}` → `201` |

Create-from-expert also accepts `file_overrides: [{name, content}]`,
`omit_files: string[]`, `hub_skills: [{skill_name, ...}]`, and
`copy_skills: [{agent_id, slug}]`. Overrides are applied to the new
agent's workspace after its bundled template is seeded.

Bundled experts live in `src/octop/infra/agents/experts/library/`
(en/zh divisions); the catalog is locale-aware via
`Accept-Language` / user preference.

## Teams

A team is a host agent (`kind = 'team'`) with a workspace roster in
`.maitu/manifest.json`. The host delegates to at least two enabled,
accessible expert agents. Team management requires both the independent `teams`
permission and effective `experts` permission; a teams grant is ineffective
when experts is absent or explicitly denied. Individual team reads and writes
also require ownership (or admin).

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/teams` | teams + experts | teams owned by the caller |
| `GET` | `/teams/template` | teams + experts | packaged host Markdown files (`[{name, content}]`) |
| `POST` | `/teams` | teams + experts | body `{name, member_ids, description?, default_model?, welcome_message?}` → `201`; members must be accessible experts |
| `GET` | `/teams/{team_id}` | teams + experts + owner | host metadata and visible roster (`member_ids`, `members`) |
| `PATCH` | `/teams/{team_id}` | teams + experts + owner | replace `member_ids` or update host metadata; busy members cannot be removed |
| `DELETE` | `/teams/{team_id}` | teams + experts + owner | `204`; refused while a member has an in-flight job |

## Feature workflow

A feature is an agent (`kind = 'feature'`); its workflow is a document in that
agent's workspace (`.maitu/workflow.json`) declaring the input form, the fixed
steps, the deliverables and the soft rules its runs follow. An unpublished
`draft` is visible to its author and administrators of that author's enterprise,
but only the author may train it. On the first activation, ownership moves from
the author's account to the enterprise administrator role: the author's account
can no longer change the feature, and deletion of that account does not remove
the feature. Administrators of the owning enterprise and system administrators
manage it thereafter, even if the definition is returned to `draft` or removed.
An `active` definition is readable by anyone who may reach the feature and
renders their input card. Workflow endpoints refuse experts with
`WORKFLOW_NOT_A_FEATURE`.

Existing active workflows are adopted on server startup, including stopped or
disabled feature agents; an unreadable workspace is logged for retry on the
next startup rather than silently treating the feature as a draft.

The default `/agents` list includes every enabled draft feature in the
administrator's enterprise even when its author has not shared it.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{aid}/workflow` | agent access | `{workflow, error}` — the author's or in-scope enterprise administrator's draft, or a published definition; `null` when none exists or the caller cannot see an unpublished draft; `error` when an accessible stored file cannot be read as one |
| `PUT`    | `/agents/{aid}/workflow` | draft author; published enterprise/system administrator (`configuration`) | body `{workflow}`; `draft` is shape-checked, `active` must stand on its own **and** may only reference skills/subagents the feature actually has (the refusal lists what it does have); **every** problem is reported at once (`WORKFLOW_INVALID`) and nothing is written; `null` removes it |
| `DELETE` | `/agents/{aid}/workflow` | draft author; published enterprise/system administrator (`configuration`) | same as `PUT` with `null` |
| `GET`    | `/agents/{aid}/workflow/runs` | agent access | the caller's own runs, newest first: `{runs: [{id, created_at, thread_id, inputs}]}` |
| `POST`   | `/agents/{aid}/workflow/changes` | draft author or published enterprise/system administrator for `definition`; caller for `overlay` | body `{target, summary, items: [{path, before, after}], run_id?}` → the applied change. Each item's `before` must still be the current value, or the whole batch is refused (`WORKFLOW_CHANGE_CONFLICT`, 409, nothing written); a definition change that would not validate is refused too |
| `GET`    | `/agents/{aid}/workflow/changes` | agent access | the caller's own changes, newest first; an in-scope enterprise administrator also sees all definition changes, but not another caller's overlay edits |
| `POST`   | `/agents/{aid}/workflow/changes/{change_id}/revert` | draft author or published enterprise/system administrator for `definition`; caller for own `overlay` | undoes it item by item; an item since edited elsewhere is reported (`WORKFLOW_CHANGE_CONFLICT`) instead of overwritten. Reverting twice is a no-op |
| `GET`    | `/agents/{aid}/workflow/overlay` | agent access | the caller's own overlay text: `{overlay}` or `null` |
| `PUT`    | `/agents/{aid}/workflow/overlay` | agent access | body `{overlay}` (at most 4000 chars); blank or `null` removes it |

An **overlay** is explicitly written by the calling user, never generated from
memory. It is injected after the feature definition and takes precedence over
both that definition and retrieved memories. It changes nobody else's feature
and is not visible to another caller.

`{{inputs_json}}` in a workflow prompt expands to the actual run inputs as JSON
(including nested values and Unicode); absent inputs expand to `{}`.

Definition saves, changes, reverts and deletion are serialized per feature.
First activation stages the new workspace document before transferring ownership,
then installs it. A failed publication or installation restores the previous
document and compensates the ownership and ACL changes made by that attempt.

## Resource sharing approvals

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/sharing/acl/{type}/{id}` | ACL manager | Request an access change; response `status` distinguishes `pending` from `applied` |
| `GET` | `/sharing/changes` | scoped user | Change history or approval queue; includes before/after ACL snapshots and `rollback_of` for reversals |
| `POST` | `/sharing/changes/{change_id}/approve` | admin | Apply a pending request only when its baseline ACL version and effective state still match; stale requests return 409 |
| `POST` | `/sharing/changes/{change_id}/rollback` | ACL manager | Create a new reversal request and return its outcome, not the original change |

A reversal follows the same widening/approval rules as a normal access change.
Pending reversals do not alter the live ACL or mark the original change rolled
back. That happens only when the reversal is applied; duplicate pending reversals
are rejected. Approval checks and ACL writes share one database transaction.

## Feature business data

A feature may also keep generic business records (a supplier ledger, a controlled
document register, equipment, contracts) under its own agent. The object/field
schema is a workspace document (`.maitu/feature-data/schema.json`); records and
audit rows are database rows, and attachment versions live in the feature
workspace. Suppliers and controlled documents are configurations, not code paths.

Records created while a feature is `draft` are private to their creator. After
activation new records follow the feature ACL; a draft record stays private until
its creator publishes it. Record edits use an `expected_revision` optimistic lock
(409 on a stale revision). Shared records can be assigned to a responsible user;
handover preserves `created_by` and private records remain creator-only. File fields
never enter `values`: each upload creates a new version and the previous current
version is retained.
Published-feature enterprise managers use the same caller-based record policy
through HTTP and Agent tools; manager status does not grant access to another
user's private draft.

List queries accept `q`, `limit`, `offset`, and optional structured filters:

```json
{"filters":[{"field":"weight","op":"gte","value":100},
            {"field":"made_on","op":"lte","value":"2026-10-23"}],
 "sort_by":"made_on","sort_order":"desc","limit":50,"offset":0}
```

Filters use AND semantics, support up to 20 conditions, and validate each field
and operator against the declared type. Operators are `eq`, `ne`, `contains`,
`gt`, `gte`, `lt`, `lte`, `in`, and `is_empty`; sorting accepts a declared field
or `created_at`, `updated_at`, or `title`, with `asc` or `desc`. Results retain
the filtered total and stable ordering. Agent `feature_data_list` supports the
same filter/sort contract through `filters_json`, `sort_by`, and `sort_order`.

`record_ref` fields store a public record ID and may target an object in the same
feature or a selected agent. Targets must exist, match the declared object, be
undeleted, and be currently readable. `knowledge_ref` fields store
`{kb_id, document_id, version?, page?, layout?}`; an omitted version is pinned
at creation. Reads resolve references using current ACLs. Reference metadata is
returned only while authorized; inaccessible targets are redacted from record
values and history. A stale reference means the current original bytes no longer
match the pinned SHA-256; it does not imply that historic bytes are retained.
Reference resolution is shared by HTTP and Agent tools.
Versioned original links include `version=<SHA-256>`. Downloads verify the actual
bytes and serve a request-lifetime temporary snapshot; an external source change
returns HTTP 409 / `KNOWLEDGE_SOURCE_VERSION_CHANGED` even before the next scan.
Unversioned downloads still serve the current authorized original.


Record responses add a `references` map keyed by field. Each entry reports
`available`, `stale`, and `kind`; authorized entries may also include title/path,
record or file IDs, and page/layout locator details. With no current access,
protected metadata and the raw reference value are omitted.

Date fields may declare a safe `computed` operation (`add_days`, `add_months`,
or `add_years`) with a date `source_field` and either an integer `amount` or an
integer `amount_field`. Dependencies are validated; cycles and invalid dates are
rejected, calendar month/year ends clamp to the last valid day, and missing
inputs produce no derived date. Clients cannot submit computed values; updates
recompute them.

Optional object `reminder_recipients` selects `creator`, `owner`, `responsible`,
`user_fields`, `unit_fields`, and existing `role_keys` (`admin`,
`enterprise_admin`, `unit_admin`, `user`). Without it, the existing creator and
owner behavior is retained. Unit resolution includes child units and is shared
with job recipient resolution. Delivery includes only active users who can
currently read both the feature and record. Scheduling uses the server timezone.
Feature managers can hand over shared records with
`POST /agents/{aid}/feature-data/handover` body
`{from_user_id,to_user_id,object_key?}`; only active authorized recipients and
shared records are eligible. Creator identity and private drafts are preserved;
handover moves shared-record editing to the new responsible user.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/agents/{aid}/feature-data/schema` | agent access | `{schema, error}`; `schema` is the manager-authored object document |
| `PUT` | `/agents/{aid}/feature-data/schema` | feature manager (`configuration`) | body `{schema}`; validates object/field keys, types, computed dependencies and references, then writes atomically |
| `GET` | `/agents/{aid}/feature-data/{object}/records` | agent access | `{records, total}`; accepts `q`, `limit`, `offset`, `filters`, `sort_by`, `sort_order`; private records are visible only to their creator |
| `GET` | `/agents/{aid}/feature-data/references/records` | agent access | query `target_agent_id`, `object_key`, `q`; authorized choices only |
| `POST` | `/agents/{aid}/feature-data/{object}/records` | agent access | body `{values}`; draft feature → private, active → feature visibility; rejects caller-supplied computed values |
| `GET/PATCH/DELETE` | `/agents/{aid}/feature-data/records/{rid}` | record read; update/delete current responsible user or feature manager, private drafts creator-only | `PATCH` body `{values, expected_revision}`; delete is a soft delete; responses include authorized reference resolution |
| `POST` | `/agents/{aid}/feature-data/records/{rid}/publish` | creator or manager; active feature | private → feature visibility |
| `POST` | `/agents/{aid}/feature-data/handover` | feature manager | body `{from_user_id,to_user_id,object_key?}` → `{transferred_count,record_ids}`; shared records only |
| `GET` | `/agents/{aid}/feature-data/records/{rid}/history` | record read | field/file/action audit, newest first; reference values are redacted when targets are inaccessible |
| `GET/POST` | `/agents/{aid}/feature-data/records/{rid}/files[/{field}]` | record read / write | list versions or upload a new version (`file`, `note`) |
| `GET` | `/agents/{aid}/feature-data/records/{rid}/files/{fid}/download` | record read | version bytes |
| `GET` | `/agents/{aid}/feature-data/notifications` | agent access | current caller's in-app reminders; `unread_only` |
| `POST` | `/agents/{aid}/feature-data/notifications/{id}/read` | caller | mark read |
| `POST` | `/agents/{aid}/feature-data/reminders/{id}/complete` | record creator or feature manager | stop further reminder delivery |


## Workspace, skills, subagents, memory, files

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{aid}/workspace/tree` | owner | list dir (default `.`) |
| `GET`    | `/agents/{aid}/workspace/file` | owner | read a content file |
| `PUT`    | `/agents/{aid}/workspace/file` | owner | write a content file (via `BackendWorkspace`) |
| `DELETE` | `/agents/{aid}/workspace/file` | owner | delete a content file |
| `POST`   | `/agents/{aid}/workspace/rename` | owner | rename / move |
| `POST`   | `/agents/{aid}/workspace/upload` | owner | multipart upload → backend (not `max_upload_mb`) |
| `GET`    | `/agents/{aid}/workspace/download` | owner | download a file |
| `GET`    | `/agents/{aid}/workspace/glob` | owner | glob backend paths |
| `GET`    | `/agents/{aid}/workspace/grep` | owner | grep backend files |
| `GET`    | `/agents/{aid}/workspace/...` | owner | see `api/routers/workspace.py` |
| `GET`    | `/agents/{aid}/skills` | owner | list installed skills |
| `PUT`    | `/agents/{aid}/skills/{slug}` | owner | enable / disable a skill |
| `GET`    | `/agents/{aid}/skills/hub/search` | user | Skill Hub search |
| `GET`    | `/agents/{aid}/skills/hub/rankings` | user | Skill Hub rankings |
| `POST`   | `/agents/{aid}/skills/hub/install` | owner | body `{slug, version?}` → `201` |
| `GET`    | `/subagent-catalog/divisions` | user | bundled subagent divisions |
| `GET`    | `/subagent-catalog` | user | bundled subagent catalog |
| `GET`    | `/subagent-catalog/{slug}` | user | full subagent definition |
| `GET`    | `/agents/{aid}/subagents` | owner | installed subagents for an agent |
| `POST`   | `/agents/{aid}/subagents` | owner | install a bundled subagent |
| `GET`    | `/agents/{aid}/heartbeat-config` | owner | read heartbeat YAML |
| `PUT`    | `/agents/{aid}/heartbeat-config` | owner | write heartbeat YAML |
| `GET`    | `/agents/{aid}/memory/access` | memory + agent access | `{stage, default_scope, shared_writable, private_writable, configuration_writable}` for the current caller |
| `GET`    | `/agents/{aid}/memory/daily` | owner, or active-feature caller | list shared daily memory files |
| `GET`    | `/agents/{aid}/memory/daily/{filename}` | owner, or active-feature caller | read one shared daily memory |
| `DELETE` | `/agents/{aid}/memory/daily/{filename}` | owner, draft only for features | delete one daily memory |
| `GET`/`POST` | `/agents/{aid}/memory/...` | agent access | scoped memory API; `scope=shared|private` on memory operations, with omitted scope defaulting to shared in draft and private when active |
| `GET`/`PUT` | `/agents/{aid}/memory/context/{name}` | scoped memory read/write | `USER.md` or `MEMORY.md`; PUT `{text, expected_snapshot}`, scope query; stale saves return 409 |
| `POST` | `/agents/{aid}/memory/review` | scoped memory read | `{scope?}`; configured-model semantic review, bounded coverage and a caller/agent/scope-bound receipt; no mutation |
| `POST` | `/agents/{aid}/memory/review/apply` | scoped memory write | `{scope?, receipt, fact_ids}`; revalidate documents/facts and deactivate only the selected reviewed contradictions |
| `GET`/`POST` | `/agents/{aid}/memory/backups` | scoped memory access | GET scope query; POST `{scope, confirmed:true}` creates a caller-owned archive |
| `GET` | `/agents/{aid}/memory/backups/{backup_id}/download` | same caller/agent/scope | ZIP download; shared-scope archives are also caller-bound |
| `DELETE` | `/agents/{aid}/memory/backups/{backup_id}` | same caller/agent/scope | `scope` and `confirmed=true` query parameters |
| `POST` | `/agents/{aid}/memory/restore` | scoped memory write | `{scope, backup_id, confirmed:true}`; creates a safety backup before restoring |

For a feature, its author may edit shared memory while the workflow is a draft;
in-scope enterprise administrators may inspect the draft and training memory.
After publication, shared memory (including workspace files and the SQLite store)
is read-only to everyone. Each authenticated caller has a separate writable private
memory; automatic capture and extraction deposit only there, never into shared
memory. Shared-feature callers can edit their private memory and overlay from
Personalization without permission to manage the feature itself.

Manual documents are limited to 65536 UTF-8 bytes. They are snapshotted on a
thread's first use and remain stable for that conversation; new threads read
later edits. Stale model file writes are refused. A clinical-profile-generated
`USER.md` remains script-owned and read-only in the manual editor. Semantic review
does not rewrite manual text; unavailable/invalid model reviews change nothing,
and an explicit confirmation is required before deactivating selected facts.

Current-agent backups include the selected namespace's tree, structured facts,
episodes, digests and metadata, manual documents, and only verified caller-owned
checkpoints/pending writes. SQLite and PostgreSQL are supported; disabled-memory
agents include their SQLite fallback checkpointer. Restore is a replacement,
not a merge of post-backup rows, and never copies a whole multi-user database.
It reserves storage, pauses a previously running agent, closes owned handles,
restores, then restarts only if it was running. Active feature shared memory
remains read-only; private restore cannot target another caller.
Read access still permits creating, downloading and deleting the caller's own
archives of that shared scope. Those archive operations do not modify the live
shared namespace and do not grant restore permission.

Restore invalidates the affected caller's old approvals, paused invocation
context, queued steering and session approval bypass, and refreshes the history
projection. Immutable archived transcripts remain an audit trail; restoring
runtime checkpoints does not undo external tool effects or rewrite that trail.
Extraction settings include `extract_max_tokens`, `extract_light_timeout_s`,
`extract_heavy_timeout_s` and `extract_extra_body`; they are applied to live
expert and feature extraction clients, not merely stored in configuration.

Personalization APIs additionally require their module key (`skills`,
`subagents`, `tools`, `agent_plugins`, `memory`). These grants do not replace
resource ACL, feature author/stage restrictions, or the licensed deployment floor.
Upgrade backfill preserves explicit denies and does not grant restricted
executable-plugin capabilities on managed customer installations.

Workspace path arguments are limited to the authenticated agent's workspace, including
`file://` URLs and host-absolute paths sent with `from_workspace=false`.
Set `from_workspace=true` to treat a leading `/` as workspace-relative.
Tool-generated files outside the workspace must be saved inside it before download
or preview; other agents' files and shared temporary directories are not accessible.

## ACP (Agent Client Protocol)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/acp` | user | current user's global runner list |
| `PUT`    | `/acp` | user | replace global runners |
| `GET`    | `/acp/{runner_name}` | user | one runner |
| `PUT`    | `/acp/{runner_name}` | user | upsert one runner |
| `DELETE` | `/acp/{runner_name}` | user | delete a custom runner (built-ins are protected) |
| `GET`    | `/agents/{aid}/acp` | owner | global runners + this agent's `tool_enabled` flag |
| `PUT`    | `/agents/{aid}/acp` | owner | update `tool_enabled` and optionally the global list |
| `PUT`    | `/agents/{aid}/acp/tool` | owner | toggle the `acp_runner` tool only |

See [ACP integration](./acp.md) for the runner object schema and the
Zed setup example.

## Knowledge-base indexing and permissions

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/knowledge-bases/enterprise` | user | the deployment's enterprise knowledge space |
| `GET` | `/knowledge-bases/{kb}/documents` | reader | only documents in the caller's effective folder/file ACL scope |
| `GET` | `/knowledge-bases/{kb}/coverage` | reader | counts only readable files, not hidden folders or siblings |
| `GET` | `/knowledge-bases/{kb}/search?q=…` | reader | lexical/optional vector search filtered by the same effective ACL |
| `GET` | `/knowledge-bases/{kb}/documents/{doc}/preview` | reader | up to 200,000 characters from the completed index plus per-segment `blocks` with their source locators; verifies the original hash, never repeats OCR |
| `GET` | `/knowledge-bases/{kb}/documents/{doc}/segments/{segment}` | reader | revalidates the file and its permission before returning verified evidence |
| `GET`/`PUT` | `/knowledge-bases/{kb}/permissions` | reader / ACL manager | effective scope metadata for the base and accessible documents; changes use existing approval/audit |
| `GET`/`PUT` | `/knowledge-bases/{kb}/documents/{doc}/permissions` | reader / ACL manager | effective document scope, inheritance and read/write; only managers see grant details |
| `GET` | `/knowledge-bases/{kb}/permission-principals` | ACL manager | safe user, department and role choices without requiring the users module |
| `GET` | `/knowledge-bases/{kb}/documents/{doc}/file` | reader | authorized uploaded or connected original; optional `version` requires matching actual SHA-256 and returns 409 if changed; does not require a completed index |
| `GET` | `/knowledge-bases/processors` | user | available built-in processors and optional CAD CLI capability; unavailable formats remain archive-only |

Folder ACLs intersect with ancestor restrictions by default. Only a matching
explicit user, department, or role grant on a single file can bypass ancestor
folder restrictions; the file ACL and knowledge-base ACL remain mandatory.
Making a file public is not such an exception. Read and write access are separate:
library-level write access does not override a read-only file. An explicitly
granted file retains its actual path; denied folder rows, siblings and their
counts remain hidden.

`blocks` reports each indexed segment in reading order with the position it came
from (`{line}`, `{paragraph}`, `{page}` + `image_ocr`, `{slide}`, `{sheet, row}`,
`{table, row}`, or a CAD `{entity, handle}`). A format that cannot map extracted
text to a smaller location reports a document-level locator instead of inventing
one, so a reader can always tell how precisely a block is located.

For previews, unindexed local Markdown/plain-text documents can be read within
the character limit. Other documents whose index is unfinished, unavailable or
stale return HTTP 409 with `KNOWLEDGE_INDEX_NOT_READY`. Indexing scans all PDF
pages sequentially with bounded OCR rendering and one heavy ingestion task per
process. DOCX parsing does not load unrelated embedded images or OCR them.

### Upload destinations

The dashboard upload button and file drop both ask where to save the original.
Platform storage remains the default. Uploading from a source's virtual tree to
platform storage uses the platform root, not the source's reserved namespace.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/knowledge-bases/{kb}/documents` | writer | existing platform upload; multipart `upload` and optional knowledge-base-relative `path` |
| `GET` | `/knowledge-bases/{kb}/upload-targets` | writer | writable local/SMB sources as `{id, name, folders: string[]}`; only authorized indexed folders, no physical addresses or credentials |
| `POST` | `/knowledge-bases/{kb}/data-sources/{source}/documents` | writer | multipart `upload`, optional source-root-relative `path` including filename; `201` means the source original is saved and indexing is queued |
| `GET` | `/knowledge-bases/{kb}/upload-permissions` | reader with knowledge module | query `folder` and optional `data_source_id`; returns safe `options` and the exact maximum `default` for that destination |

These routes require the `knowledge_bases` module permission and knowledge-base
write access. Source uploads additionally check every existing destination
ancestor's write ACL, the source's `read_only` flag, file type/size and
include/exclude rules. Folder choices are relative to the configured source root;
`""` means root. Sync a source to discover newly added subfolders. Uploads only
write into existing physical folders; they never create parent directories.

Source uploads return the document's `data_source_id`, `source_path`,
`has_original` and indexing `status`. The source is the original's only permanent
storage; parser staging is temporary. New documents use the same
source-ID/path identity as scans, so later syncs update the document rather than
create a second copy. Parsing failures retain the saved original and expose the
failure on the document; they do not undo a successful upload.

Sources default to read-only. Only the knowledge-base owner or system
administrator may create a writable folder source, enable uploads, or change
connection settings on an existing writable source. The dashboard exposes an
administrator-controlled **Allow uploads** switch. Ordinary writers can upload
to an authorized source without receiving permission to reconfigure its physical
address. The connecting OS/SMB account must also have storage write access.

Uploads publish staged bytes without overwriting an existing name, including a
collision created during the upload. Temporary `.octop-upload-` files are
excluded from scans. Source failures never silently fall back to platform
storage. `DATA_SOURCE_READ_ONLY` is HTTP 403, `DATA_SOURCE_UPLOAD_CONFLICT`
is 409, and unsafe/excluded destinations use `DATA_SOURCE_UPLOAD_INVALID` (400).
Direct SMB still requires the optional `smbprotocol` dependency. Filesystems
must support a safe no-replace publication primitive; unsupported storage fails
closed rather than replacing a file.

### Data-source configuration and scan runs

`PATCH /api/data-sources/{ds_id}` updates a registered source, including
`folder.scan_interval_seconds` in the replacement `folder` settings: `0` disables
scheduled scans; a positive value sets the minimum interval between attempts.
Local and SMB sources are eligible. Due sources are checked every 15 seconds;
new or changed files must pass the existing 30-second stability window before
entering the background index queue. Scans create ordinary run records with a
scheduled trigger and their actual result, including failures. Inspect them with
`GET /api/data-sources/{ds_id}/runs`. Shutdown waits for scans and active indexing
workers before closing the database; queued documents resume at startup.

Sources are `read_only` by default. To upload to a source, an administrator
must explicitly configure it as writable (`read_only: false`) and enable source
uploads; otherwise upload is refused. Source upload permission does not make the
source writable by default.

Both upload endpoints accept optional multipart `access_scope`, `access_permission`
and `access_unit_key`. Scopes are `company`, `department`, `personal` and
`authorized`; permissions are `read` or `write`. Scope and permission must be
supplied together, and only a department choice accepts its offered unit key.
If all fields are omitted, the server recomputes the maximum default and stores
it with the new document. The initial ACL and document row commit together.

Choices intersect the knowledge-base and destination-folder boundaries using the
uploader's actual company, department or role authorization. An individual grant
alone offers only personal access, even when other individuals were also granted
the folder. `authorized` represents a custom group boundary, not unrestricted
access; its current audience is snapshotted at upload time. A later parent
widening does not widen a file's selected initial scope. The upload rechecks the
choice against current permissions before writing an original: stale or forged
widening returns HTTP 403 `KNOWLEDGE_UPLOAD_PERMISSION_FORBIDDEN`, without saving
the file or a document row.

### Individual delegation and revocation notifications

Individual `user` ACL grants accept `can_delegate`, default `false`. Department
and role grants cannot enable this flag. Content-write permission does not grant
ACL-management permission: only the knowledge-base owner or system administrator
may edit root grants. A recipient cannot enable its own root flag.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/knowledge-bases/{kb}/documents/{doc}/delegations` | manager / delegator | own outgoing edges, or the complete document graph for a manager; eligible active knowledge-base readers only |
| `POST` | `/knowledge-bases/{kb}/documents/{doc}/delegations` | delegator | `{grantee_user_id, can_delegate?: false}`; creates or updates only the caller's own edge; `201` listing |
| `DELETE` | `/knowledge-bases/{kb}/documents/{doc}/delegations/{id}` | issuer / manager | `204`; revokes the edge and unsupported downstream edges |
| `GET` | `/knowledge-access-notifications` | user | own persistent inbox; `unread_only=false`, `limit=50` (1–100), response `{items, unread_count}` |
| `POST` | `/knowledge-access-notifications/{id}/read` | user | `204`; idempotent for the caller's own notice, missing or foreign IDs return 404 |

Every delegation hop is limited by its grantor's current read/write and delegation
rights. Knowledge-base access remains mandatory; self-grants, cycles and attempts
to delegate without authority return `KNOWLEDGE_DELEGATION_FORBIDDEN` (403).
Delegations apply to one document, never sibling files; a cross-document edge ID
returns 404. Derived edges are not serialized as independent root ACL grants.

Revoking a root grant or disabling its delegation flag deletes unsupported
downstream edges in the same transaction. Independent valid authorization remains;
re-enabling a root does not resurrect deleted edges. Removal of an original
non-delegable individual grant also produces a notification. No-op or rejected
requests, and database transactions that fail or roll back, leave no notices.

Notices identify the previously authorized logical resource and whether valid
read access remains (`access_retained`). They survive logout and loss of the
knowledge module or resource access. The inbox requires authentication and the
instance license gate, not continued knowledge-base access; it never restores
access. Revocation cannot recall copies already downloaded outside the platform.


### Source-authorized two-document collections

Collections follow their target original's current ACL, not their creator's
identity. Each explicitly selected file or folder gets an independent collection;
selecting a parent and child preserves both. Descendants included by a folder do
not get extra collections.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/knowledge-bases/{kb}/collections` | reader | collections whose target the caller may read; coverage excludes inaccessible sources |
| `POST` | `/knowledge-bases/{kb}/collections` | reader | body `{document_ids: string[], prompt?: string, name?: string}` → `201` list; no original parsing or automatic organization |
| `PATCH` | `/knowledge-bases/{kb}/collections/{collection}` | source writer / manager | body `{prompt?: string, name?: string}`; changing the prompt invalidates old topical results |
| `POST` | `/knowledge-bases/{kb}/collections/{collection}/generate` | source writer / manager | explicit prompt-directed organization; prompt required; concurrent run returns `409 KNOWLEDGE_COLLECTION_GENERATING` |
| `GET` | `/knowledge-bases/{kb}/collections/{collection}/documents` | reader | `{collection, topic_document, reading_document, storage_path}`, filtered to current readable sources |
| `DELETE` | `/knowledge-bases/{kb}/collections/{collection}` | source writer / manager | `204`; deletes derived artifacts, never originals |

An empty prompt is valid for reading accumulation. Prompts are at most 2,000
characters; names must be nonempty and at most 200 characters. Metadata includes
the stable target ID/path, prompt, `coverage` (`total`, `read`, `generated`,
`pending`, `failed`) and explicit `generation` state
(`idle`, `running`, `ready`, `partial`, `error`), error, model and completion time.
An HTTP success does not imply complete organization: inspect generation and
coverage for extraction/model failures.

`topic-index.md` contains prompt-directed results with evidence citations;
`reading-index.md` accumulates actual source reads and separately labeled visual
observations. Each item retains the relative file path, source version and exact
line/page/sheet/image locator. Model conclusions are not verified transcriptions.
Only metadata lookup or exporting an original does not count as reading it.
One successful source read updates all containing collections without re-parsing
the same source for each collection. New evidence supplements or flags existing
topic results; ordinary reading does not automatically rescan the whole folder.

Agent tools browse and locate authorized originals without requiring vectors or
completed indexes. Related collection Markdown must be read before source content.
Receipt state is server-stamped for each turn, not trusted from client config.
The original export tool binds approval to the actual source SHA-256; shared
workspaces require explicit, non-bypassable confirmation because an exported copy
can be read by other workspace users and is no longer protected by the source ACL.
Shared originals must be exported through an initialized main conversation;
child graphs and incomplete bootstrap cannot bypass that approval. A parent can
approve a copy before delegating work on it to a child task.

Artifacts are caller-filtered materializations under
`<MAITU_HOME>/knowledge-collections/<collection_id>/<user_id>/`; database evidence
is canonical. Revoked or old-version sources do not reappear through cached
Markdown. Renames preserve stable targets; same-path replacement does not
retarget an existing collection. Knowledge backups include the derived artifacts.

Permission updates take `{expected_version, visibility, unit_key?, permission,
grants?, reason?}`. `visibility` is `public` (company), `unit` (department), or
`private` (personal; private with grants is custom). `permission` is `read` or
`write`; grants identify users, units or roles. Responses include the effective
permission and applied/pending change metadata. Stale versions return `409`.
The knowledge-base owner and system administrator manage ACLs; content write
permission alone does not grant ACL management. Sharing retains approvals/audit,
but the knowledge page is the configuration entry point.

Old personal keyword/search-rule endpoints and runtime policies are removed.
Ordinary filename/index search and historical citation rendering remain.

## Storage backends

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`POST`/`PATCH`/`DELETE` | `/storage-backends` | user | per-user remote backend connections |
| `GET`/`POST`/`PATCH`/`DELETE` | `/admin/storage-backends` | admin | admin-managed backends |

## Host filesystem (dashboard)

Browse the host OS directory tree when configuring a local backend
`root_dir` (`local_shell` / `filesystem`). Authenticated users only;
sensitive mounts (`/proc`, `/sys`, `/dev`, `/etc`, `/root` on POSIX)
are rejected, except the process home and its subdirectories (so a
server running as root may use `/root` as the default `root_dir`).
Listing is single-level and capped; write probe runs only
for non-`/` paths.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/filesystem/dirs?path=<abs>` | user | `{path, entries: [{path, name}]}` — one directory level |
| `POST`   | `/filesystem/probe` | user | body `{path}` → `{ok, path?}` or `{ok: false, code, detail?}` (`not_directory`, `permission_denied`, `write_failed`, `not_allowed`) |
| `POST`   | `/filesystem/mkdir` | user | body `{path, base_name?}` → `{path, name}` — create child dir (`base_name` defaults to `New Folder`; collisions become `Name (2)`, …) |
| `POST`   | `/filesystem/rename` | user | body `{path, new_name}` → `{path, name}` — rename basename only |

## Connectors & OAuth

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/connectors/catalog` | user | connector catalog (Notion, Figma, …) |
| `GET`    | `/connectors/test-credentials` | user | preflight credentials |
| `GET`    | `/connector-instances` | user | list instances |
| `POST`   | `/connector-instances` | user | create instance |
| `GET`/`PATCH`/`DELETE` | `/connector-instances/{id}` | user | CRUD on an instance |
| `POST`   | `/connector-instances/{id}/test` | user | test a configured instance |
| `POST`   | `/connector-instances/{id}/refresh` | user | refresh OAuth tokens |
| `GET`    | `/connectors/auth/{kind}/info` | user | auth flow info |
| `GET`    | `/connectors/auth/{kind}/authorize-url` | user | build the authorize URL |
| `POST`   | `/connectors/auth/{kind}/exchange-code` | user | exchange auth code |
| `POST`   | `/connectors/oauth/start` | user | start OAuth (catalog or custom MCP via `target`) |
| `POST`   | `/connectors/oauth/{kind}/start` | user | legacy catalog OAuth start |
| `PUT`    | `/connectors/custom-mcp` | user | save custom MCP server map |
| `PATCH`  | `/connectors/custom-mcp/servers/{name}` | user | patch `enabled` / `default_open` on one server |
| `POST`   | `/connectors/custom-mcp/test` | user | probe a custom MCP server (inline spec or saved name) |
| `GET`    | `/connectors/oauth/callback` | public | OAuth redirect target |
| `GET`    | `/connectors/oauth/pending/{state_id}` | user | poll the OAuth result |

Custom MCP OAuth (streamable HTTP, public HTTPS URL only): 麦途智造 discovers the authorization
server from the MCP URL (401 / RFC 9728 protected-resource metadata), requires dynamic client
registration (DCR), stores encrypted tokens in the custom MCP spec, and injects `Authorization:
Bearer` when loading tools. Loopback MCP URLs do not use remote OAuth discovery.

## Internal MCP (harness agents)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST`/`GET`/… | `/internal/mcp/*` | public (mTLS / network-isolated) | MCP gateway used by harness agents (not the dashboard) |

## Observability & security

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`PUT` | `/admin/observability` | admin | Langfuse configuration (host, project, env) |
| `GET`    | `/admin/security` | admin | global security policy |
| `PUT`    | `/admin/security` | admin | update global policy |
| `GET`    | `/admin/security/tool-guard/rules` | admin | active command guard rules |
| `GET`    | `/admin/security/tool-guard/rules/raw` | admin | editable YAML |
| `PUT`    | `/admin/security/tool-guard/rules/raw` | admin | save YAML |
| `POST`   | `/admin/security/tool-guard/rules/reset` | admin | reset to shipped defaults |
| `GET`    | `/admin/security/defaults` | admin | defaults + rule catalogs |

## TLS (Let's Encrypt)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`  | `/admin/tls/status` | admin | current cert + issuance task status |
| `POST` | `/admin/tls/preflight` | admin | preflight (port 80, DNS) |
| `POST` | `/admin/tls/issue` | admin | start HTTP-01 issuance |

`GET /.well-known/acme-challenge/{token}` is the HTTP-01 challenge
endpoint (public, mounted directly in `api/app.py`).

## Browser, terminal, uploads

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `WS`/`POST`/`GET`/… | `/agents/{aid}/terminal` | owner | AI-assisted remote PTY |
| `GET` | `/agents/{aid}/terminal/context` | owner | recent terminal context for the AI helper |
| `WS`/`POST`/`GET`/… | `/browser/...` | user | harness-browser sessions, live stream, record/replay |
| `POST` | `/browser/shutdown` | user | stop the current user's 麦途智造-managed Chrome |
| `POST` | `/agents/{aid}/upload` | user | multipart upload → `{workspace}/inbound/` |
| `POST` | `/agents/{aid}/files/access-urls` | user | refresh inbound media URLs (signed) |
| `GET`  | `/agents/{aid}/files/{path}` | owner | read an inbound file |

## Updates, ollama, i18n, plugins, slash, preferences

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`POST` | `/update/status`, `/check`, `/upgrade`, `/progress`, `/restart` | admin | in-place server update flow |
| `GET`/`POST`/`DELETE` | `/ollama/...` | user | Ollama model discovery + downloads |
| `GET` | `/i18n/tools` | user | server-owned tool display names (locale-aware) |
| `GET` | `/i18n/locales` | public | available locales + fallback chain |
| `GET` | `/i18n/locales/{locale}/{namespace}` | public | one namespace bundle (errors, tools, channel, slash) |
| `GET`/`PATCH` | `/preferences` | user | per-account preferences, including `sidebar: {order: string[], hidden: string[]}` |
| `GET` | `/slash/commands` | user | slash command catalog for the composer menu |
| `GET`/`POST` | `/plugins` | user | installed plugin list / install flow |
| `POST` | `/plugins/reload` | admin (`plugins`) | reload plugins from disk into process |
| `PATCH` | `/plugins/{id}` | admin (`plugins`) | enable/disable plugin (`{ "enabled": bool }`) |
| `GET` | `/plugins/{id}/ui/{path}` | user | serve prebuilt plugin UI assets (`ui/dist/…`) |
| `DELETE` | `/plugins/{id}` | admin (`plugins`) | uninstall plugin |

## Usage & admin

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/usage/summary` | user | token usage summary for the current user |
| `GET`    | `/admin/usage` | admin | global token usage summary |
| `GET`    | `/admin/overview` | admin | `{user_count, agent_count, ...}` |
| `GET`    | `/admin/audit-log` | admin | recent audit rows |
| `GET`    | `/admin/metrics` | admin | `{messages_total, stream_errors_total, cron_runs_total, cron_errors_total, agent_active, ...}` |

## Envs

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`PUT` | `/agents/{aid}/envs` | owner | env-var preset for an agent's tool calls |
| `GET`/`POST`/`DELETE` | `/envs/presets` | user | reusable presets |

## Error envelope

Every non-2xx JSON response uses:

```json
{ "error": { "code": "AGENT_NOT_FOUND", "message": "...", "details": { } } }
```

`code` matches the `ErrorCode` enum in `octop.infra.errors`; the
server-localized `message` is rendered by `麦途智造Error.to_envelope`
with the locale from `Accept-Language` (falling back to `en`). The
dashboard mirrors every code under `apiErrors.*` in
`dashboard/src/locales/{en,zh}.json`.

| Code | HTTP | Meaning |
|------|------|---------|
| `AUTH_FAILED` | 401 | Bad credentials |
| `TOKEN_EXPIRED` | 401 | JWT past its TTL |
| `LOGIN_LOCKED` | 423 | Too many failed attempts — wait `login_lockout_seconds` or call `/users/{id}/unlock-login` |
| `SETUP_REQUIRED` | 409 | Initial admin not yet created (or wizard not finished) |
| `FORBIDDEN` | 403 | Authenticated but not allowed |
| `NOT_FOUND` | 404 | No such row / route |
| `USER_DISABLED` | 403 | Account flag flipped off |
| `USERNAME_TAKEN` | 409 | Conflict on `users.username` |
| `AGENT_NOT_FOUND` | 404 | Agent row missing or owned by another user |
| `AGENT_FAILED` | 500 | Runtime errored during a call |
| `AGENT_BUSY` | 409 | Operation refused while another is in flight |
| `PROVIDER_NAME_TAKEN` | 409 | Conflict on `providers.name` |
| `PROVIDER_NOT_VISIBLE` | 400 | Agent config references a provider the user can't see |
| `PROVIDER_REFERENCED` | 409 | Delete blocked because agents still reference the row |
| `PROVIDER_TEST_FAILED` | 400 | `/providers/{id}/test` failed |
| `CHANNEL_KIND_UNSUPPORTED` | 400 | `kind` not in registered builders |
| `CHANNEL_INVALID_CREDENTIALS` | 400 | Channel config rejected by the platform |
| `CHANNEL_PROBE_INCOMPLETE` | 400 | Probe couldn't reach the platform |
| `CRON_TRIGGER_INVALID` | 400 | Trigger string did not parse |
| `CRON_PROMPT_INVALID` | 400 | Empty or too-long prompt |
| `SLASH_UNKNOWN` | 400 | `/<cmd>` is not a registered handler |
| `SLASH_BAD_ARGS` | 400 | Slash handler rejected its arguments |
| `ATTACHMENT_UNSUPPORTED_TYPE` | 400 | Chat / inbound attachment rejected (media type not allowed) |
| `ATTACHMENT_TOO_LARGE` | 413 | Chat / inbound attachment exceeds the configured size limit (`max_mb` in details) |
| `WORKSPACE_PATH_INVALID` | 400 | Path outside the agent's workspace |
| `STORAGE_BACKEND_UNREACHABLE` | 502 | Remote backend connect / list failed |
| `CONNECTOR_OAUTH_FAILED` | 400 | OAuth flow could not complete |
| `INTERNAL_ERROR` | 500 | Unhandled exception (logged with traceback) |
| `CHECKPOINT_DELETE_FAILED` | 500 | Durable conversation cleanup failed; conversation metadata is retained for retry |
| `MEMORY_DOCUMENT_CONFLICT` | 409 | Manual memory snapshot changed; reload and review before saving |
| `HITL_APPROVAL_STALE` | 409 | Approval identity is missing, expired, resolved or invalidated by restore; reload the current approval card |
| `MODEL_UNAVAILABLE` | 503 | No eligible healthy model, or streaming failed without safe replay |
