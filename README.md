# AI-MAITU · 麦途智造

A self-hosted enterprise AI workspace for manufacturing teams.
Built on [麦途智造](https://github.com/TencentCloud/麦途智造) — a multi-user, multi-agent AI assistant — and extended into a governed business-capability platform.

> **This is a fork.** We rebranded the product and added the enterprise modules described below. The upstream project, its design, and its engineering are the work of the 麦途智造 authors; see [License & attribution](#license--attribution).

---

## What this is

Employees open a workspace of **business capabilities** — "draft a quotation", "extract a customer BOM", "generate a machining process package" — fill in a form, and receive a working draft. Not a chat box with a system prompt glued on, but a governed capability with:

- an owner and a version
- a defined input form and output shape
- a permission boundary (which department, which role)
- an audit trail of every run

Built for manufacturers: the capabilities are process-heavy (multi-step, human-gated, document-producing), and the output feeds engineers, workshops, and machines.

## What we added on top of 麦途智造

| Module | What it does |
|---|---|
| **Brand layer** | One `brand.config.json` drives product name, logos, colors, PWA manifest. Re-runnable — change the config and re-run `scripts/apply_brand.py`; it remembers what it last wrote. |
| **Enterprise feature catalog** | Declarative business capabilities (`feature.json` on disk). Form schema + prompt + output shape. Adding a capability is editing a file, not shipping code. |
| **Three-tier permission model** | `admin` / `unit_admin` / `user` + organizational units. Effective permissions = role ∪ unit ∪ grants − denies. Deny always wins. |
| **Unified sharing & governance** | One access-control object for agents, connectors, knowledge bases and features. Widening to org-wide visibility requires approval; every change is versioned and rollback-able. |
| **Self-improvement loop** | Every run records the draft and the human's final edit. The diff is accumulated; the model proposes rules from real corrections; a human approves them; approved rules feed future runs. |
| **Data sources & KB scoping** | Data sources ingest into knowledge bases. Runtime scope is resolved **per caller** — the capability defines *how* to work, the caller's permissions decide *what data* it can see. |

## Architecture in one screen

```
A feature = an agent-backed task flow with owner, version, lifecycle and ACL

Definition
  ① Metadata   name / icon / owning unit / who may use it
  ② Capability the feature's own agent config (model, tools, skills,
               subagents, MCP, knowledge bases, persona files)
  ③ Task       form (input) + steps (see below) + output shape
  ④ Learning   rules (personal / department / global) + cases + diffs

Cross-cutting
  A  Config is shared; data access is per caller
  B  Runs are resumable state machines (continue / rewind / rewind-with-edits)
  C  Every run snapshots its config, rules, agent and decomposition
```

**Steps are a hybrid**: we declare the stable skeleton (so human gates, validation gates and rewind points can be attached), and inside a step the model decomposes the work itself and fans out to subagents in parallel. The platform supplies a concurrency ceiling and records the decomposition — it does not script the parallel plan.

## Quick start (from source)

Requirements: **Python ≥ 3.12**, [`uv`](https://docs.astral.sh/uv/), **Node.js** (to build the dashboard). On Ubuntu, install `build-essential python3-dev linux-libc-dev` before syncing: the current core `orcakit-harness-agent[all]` dependency pulls pynput/evdev even without selecting the desktop extra.

```bash
git clone https://github.com/whl736989911/AI-MAITU.git
cd AI-MAITU

# Backend deps
uv sync --inexact --no-dev --extra office --extra knowledge-ocr

# Dashboard (built output is served by the backend)
cd dashboard && npm ci && npm run build && cd ..

# Run
uv run --no-sync maitu run --port 8088
```

The first boot prints a one-time setup password and a path to `maitu-login.txt`. Open `http://127.0.0.1:8088`, paste the password, and the wizard will:
1. choose a database backend (SQLite or PostgreSQL),
2. create the first administrator,
3. optionally configure a model provider.

For local embedding models, add `--extra local-embedding` to the sync command, then download a model from the admin UI. Desktop control is optional (`--extra desktop`) and requires a compatible graphical session; omitting it currently does not remove the transitive evdev build prerequisite.

Always keep `--inexact` when synchronizing an existing deployment so previously installed optional packages are not pruned; repeat selected extras to update their dependencies. Release ZIP installations have no `.git`: follow the [native ZIP upgrade, dependency health checks, and no-make commands](scripts/README.md#原生-release-zip-升级不需要-git). That path retains the original environment and `MAITU_HOME`, prepares new source without overwriting old source, and requires manual service cutover. Do not regenerate config or license identity during upgrades.

### Native sandbox on Ubuntu (source installs)

Agent `execute` runs inside a bubblewrap jail. On Ubuntu 24.04+ AppArmor restricts unprivileged user namespaces, and the jail fails with `bwrap: setting up uid map: Permission denied`. Fix it with the minimal, reversible grant from this repository; do **not** disable the AppArmor restriction globally.

```bash
sudo bash scripts/setup-host-sandbox.sh              # bubblewrap + userns profile (only /usr/bin/bwrap)
sudo bash scripts/setup-host-sandbox.sh --status     # report current state
sudo bash scripts/setup-host-sandbox.sh --revert     # remove the profile again
```

The dashboard's admin execution-sandbox view shows the live state: `GET /api/filesystem/bwrap-status` diagnoses without installing; the explicit action `POST /api/filesystem/ensure-bwrap` installs and reports the failure class (`missing_binary`, `install_permission_denied`, `userns_denied`, `runtime_failure`) plus display-only operator commands. The server never installs packages on its own at startup. A scoped `local_shell` backend is fail-closed: without a working sandbox the agent refuses to start instead of silently executing on the host.

Skill scripts run against the platform Python environment inside the jail. Install office and OCR dependencies with `uv sync --inexact --no-dev --extra office --extra knowledge-ocr`.

Browser/visual channel (optional): Chromium is no longer started with `--no-sandbox`, so Playwright's Chromium needs its own userns profile:

```bash
sudo bash scripts/setup-host-sandbox.sh --with-browser  # Chromium (as your user) + profile
sudo bash scripts/setup-host-sandbox.sh --revert-browser
```

Run the browser step as the user that runs the service (systemd user units take `HOME`/`PATH` from that user); the profile only matches `~/.cache/ms-playwright/...` executables and keeps Chromium's own sandbox enabled. Adjust the glob in `scripts/security/apparmor-playwright-chromium` if the service `HOME` is not under `/home`.

### Licensed customer deployment (Ubuntu)

For an office platform connected to a separate headless license center, use the local deployment wizard on each Ubuntu host:

```bash
# License-center host: HTTPS, administrator/MFA, public connection bundle, encrypted backups
sudo bash ./deploy.sh center

# Office platform host: import the verified public bundle, preserve existing data, activate
sudo bash ./deploy.sh platform

# Either host: read-only configuration, TLS, runtime and license diagnostics
sudo bash ./deploy.sh check
```

The connection bundle carries only the center URL, public signing keys and optional CA certificate. Never transfer the center's signing/encryption private keys, administrator MFA material or SSH private key to the customer platform.

See the [deployment guide](docs/license-center-deployment.md#0-推荐入口两台主机各运行一次部署向导) and [license operations](docs/instance-license-operations.md) for prerequisites, adoption, renewal, recovery and unlocking.

### Development

```bash
make dev            # backend + dashboard dev servers
make dev-backend
make dev-frontend
make all            # ship bar: format (BE+FE) + lint + typecheck + test (BE+FE)
make test           # backend pytest suite
make test-frontend  # dashboard vitest suite
make precommit      # format, lint, typecheck, affected tests (BE + FE)
```

## Configuration

Data lives under `MAITU_HOME` (default `~/.maitu`; legacy `MAITU_HOME` is still honoured): database, agent workspaces, logs, plugins, embedding models.
Set `MAITU_HOME` before starting the server to run multiple instances side by side.

The brand is configured in `brand.config.json` and applied with:

```bash
uv run python scripts/apply_brand.py
```

Public login password-recovery guidance uses the current `/api/branding` display
name and directs users to their administrator. Host-side recovery commands stay
in the operational documentation rather than exposing a fixed vendor name on
the public login page.

## License & attribution

**This project is distributed under the MIT License. See [LICENSE](LICENSE).**

### Upstream

This is a fork of **[TencentCloud/麦途智造](https://github.com/TencentCloud/麦途智造)**, also MIT licensed.

The MIT License requires that the original copyright notice and permission notice be included in all copies or substantial portions of the software. **The upstream notice is preserved verbatim in [`LICENSE`](LICENSE)**:

```
Copyright (c) 2026 麦途智造
```

Our own modifications are recorded beneath it:

```
Copyright (c) 2026 Suzhou Maitou — modifications in this fork
```

`scripts/apply_brand.py` refuses to rewrite `LICENSE` (a guard in the code), so re-branding can never strip the upstream attribution by accident.

### What we changed

A non-exhaustive summary, so upstream and downstream are easy to tell apart:

- product name, logos, iconography, palette, PWA metadata
- removed the bundled mascot artwork and the desktop-shell chrome
- added the feature catalog, permission model, sharing/governance, self-improvement loop and data-source modules
- added database migrations `016`–`022`

Everything else is upstream 麦途智造. To pull in upstream changes, add it as a remote:

```bash
git remote add upstream https://github.com/TencentCloud/麦途智造.git
git fetch upstream
```

### Third-party components

This project bundles and depends on third-party software (Python and Node packages, fonts, icons, model weights). Each retains its own license; refer to the respective packages and their lockfiles (`uv.lock`, `dashboard/package-lock.json`). Redistributing a build of this project means redistributing those components too — check their terms.

---

## Acknowledgements

The foundation of this project — the agent runtime, the tool and skill system, the gateway, the multi-channel architecture and the dashboard — is the work of the [麦途智造](https://github.com/TencentCloud/麦途智造) authors and contributors. Thank you.
