# MAITU Docker Deployment

---

This directory contains the Docker build and deployment assets for MAITU Smart Manufacturing. Operational names use `maitu`; the Python package, `octop` CLI, `OCTOP_*` environment variables, and in-container `/data/.octop` remain unchanged.

### Files

| File | Description |
|------|-------------|
| `Dockerfile` | Multi-stage image definition (build context is the repo root) |
| `../.dockerignore` | Build context ignore rules (applies to both Podman and BuildKit) |
| `docker_build.sh` | Build image from source (BuildKit cache enabled by default) |
| `docker-compose.yml` | One-command local / self-hosted deployment |
| `docker-compose.postgres.yml` | PostgreSQL (+ pgvector) for dual-backend dev/tests |
| `postgres/init-vector.sql` | Instance-level `CREATE EXTENSION vector` (initdb.d; **not** Octop migrations) |
| `docker-entrypoint.sh` | Container entrypoint: first-run init + start server |
| `security/seccomp-bwrap.json` | Moby-derived default-deny policy permitting namespace sandbox setup; requires dropping all outer capabilities |
| `security/apparmor-maitu`, `docker-compose.apparmor.yml` | Dedicated user-namespace profile and overlay for AppArmor hosts |
| `docker-compose.docker-exec.yml` | Optional host-Docker API access for an explicitly configured Docker execution backend; not part of the default deployment |

### Quick start

**Option 1: Compose (recommended)**

The source checkout may be named `/opt/maitu`. From that repository root:

```bash
export OCTOP_DATA="${OCTOP_DATA:-$HOME/.maitu}"
# New directory only; back up existing instances and check UID/GID first.
sudo install -d -m 0700 -o 1000 -g 1000 "$OCTOP_DATA"
docker compose -f docker/docker-compose.yml up -d --build
```

Open `http://localhost:8088`. If `OCTOP_DEFAULT_PASSWORD` is unset, a strong random password is generated on first init and written to `/data/.octop/credential.txt`; if it is set, it is used as-is (must be ≥8 characters with letters and digits; passwords rejected by the app password policy — e.g. common ones — fall back to a random one automatically). Change the password immediately after first login.
The image now defaults to instance-license enforcement. Without the pinned public
key and a valid grant, identity/activation remains available but business is locked.
Customer deployments should add `docker-compose.managed.yml`, mounting trust,
instance identity, and the updater queue outside business data. Never ship the
center signing private key in the client image, workspace, or business backup.

Use the existing `brand.config.json` for deployment identity:

```bash
export MAITU_BRAND_CONFIG_HOST_FILE=/etc/maitu/brand.config.json
docker compose -f docker/docker-compose.yml \
  -f docker/docker-compose.branding.yml up -d --build
```

Set `name` (platform), `full_name` (company), and `short_name` (short display name);
restart after file changes. Stored dashboard branding overrides these defaults;
resetting branding restores the deployment file. Images keep their separate
branding settings. Actual commands and protocol identifiers stay compatible.
See [runtime branding](../docs/configuration.md#deployment-brand-and-runtime-identity).


**Option 2: Build script**

```bash
bash docker/docker_build.sh
docker run -d \
  --name maitu \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --security-opt "seccomp=$(pwd)/docker/security/seccomp-bwrap.json" \
  -p 8088:8088 \
  -v maitu-data:/data/.octop \
  -e HOME=/data \
  maitu:latest
```

### Linux execution and browser sandboxes

The image runs as UID/GID `1000:1000`. Python lives under `/usr/local/maitu-venv`,
inside the execution jail's read-only system mounts; `python-docx`, PyMuPDF and
Chromium are installed. Compose drops all outer capabilities and uses
`no-new-privileges` plus a default-deny seccomp profile. Do not replace these with
`privileged`, `SYS_ADMIN`, `seccomp=unconfined` or browser `--no-sandbox`.
The default Compose configuration deliberately does **not** mount the host Docker
socket. `docker-compose.docker-exec.yml` is opt-in only: including it grants the
MAITU service Docker API access (a high-trust host privilege) for installations
whose trusted operator explicitly configures that isolated execution backend.
Never expose the socket to a job/container launched for a business agent.

On Ubuntu 24.04 or another host restricting user namespaces with AppArmor, load
the dedicated policy before adding the overlay:

```bash
sudo install -m 0644 docker/security/apparmor-maitu /etc/apparmor.d/maitu-sandbox
sudo apparmor_parser -r /etc/apparmor.d/maitu-sandbox
docker compose -f docker/docker-compose.yml \
  -f docker/docker-compose.apparmor.yml up -d --build
```

Keep managed/branding overlays and configure the host updater with the same
Compose file list. Direct `docker run` deployments also need
`--security-opt apparmor=maitu-sandbox` after loading the profile. Do not disable
AppArmor globally. The host must allow nonprivileged user namespaces and have
`user.max_user_namespaces > 0`; where present,
`kernel.unprivileged_userns_clone` must permit them. A container cannot override
a prohibition imposed by its host or parent container.

Readiness now runs an actual execution jail rather than checking only whether
`bwrap` is installed. Chromium comes from `/opt/maitu-browser`, and
`BROWSER_USE_NO_SANDBOX=0` makes unavailable browser confinement an explicit
failure instead of silently disabling it. Do not make an unknown `chrome-sandbox`
SUID. Browser location and sandbox settings are operator-managed and cannot be
overridden through the platform's environment editor.

For Ubuntu source installs outside Docker, see the native sandbox setup in the [host installation guide](../scripts/README.md#原生沙箱) and [configuration reference](../docs/configuration.md#execution-sandbox-native-installs).

The image-input checkbox and `input` metadata are hints, not a hard gate.
Image turns keep the usable selected model and send actual image content over
its configured OpenAI or Anthropic protocol. On a pre-output protocol failure,
try the other protocol with the same provider and model; return the first
successful result. Never silently remove images. If both fail, preserve both
causes rather than caching authentication/network failures as missing vision
support. Do not replay a failure after streaming has begun. DOCX/PDF still
require parsing or image extraction first.

DeepSeek capabilities are model-specific too. See its [vision guide](https://api-docs.deepseek.com/guides/vision/)
and [V4.1 Flash alias notice](https://api-docs.deepseek.com/news/news260910/).
The verified official `deepseek-v4-flash` entry can use
`input: ["text", "image"]` as a display hint, but missing metadata no longer
blocks an actual image request. No provider/default-model switch is required.
Independently verify third-party endpoints rather than trusting model names.

### Faster downloads (China mirrors)

Pass mirror env vars when building:

```bash
PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/ \
APT_MIRROR=mirrors.cloud.tencent.com \
bash docker/docker_build.sh
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOME` | `/data` | Must be `/data` so `~/.octop` maps to the data volume |
| `OCTOP_DATA` | `~/.maitu` | Compose host data directory; existing deployments must specify their original directory |
| `OCTOP_PORT` | `8088` | HTTP listen port |
| `OCTOP_DEFAULT_PASSWORD` | _(unset)_ | First-run admin password (≥8 chars, letters + digits). When unset, a random password is generated and written to `credential.txt` |
| `OCTOP_ADMIN_USERNAME` | `admin` | Initial admin username |
| `OCTOP_DATABASE_URL` | — | PostgreSQL DSN (or other `OCTOP_DATABASE_*`; see [configuration.md](../docs/configuration.md)) |
| `OCTOP_DATABASE_DRIVER` | — | `sqlite` \| `postgresql` when overriding defaults via env |
| `OPENAI_API_KEY` | — | OpenAI-compatible API key |
| `DASHSCOPE_API_KEY` | — | Alibaba DashScope API key |

For Compose, put these in `docker/.env`. Values only reach the container if listed under `environment:` in `docker-compose.yml` (Compose interpolates `.env`; it does not auto-export every key). Alternatively write the same keys into the mounted data dir as `~/.octop/env`.

### Data persistence

- Compose project, service, and container names are `maitu`; the image is `maitu:latest`. Host `~/.maitu` is mounted at container `/data/.octop` by default.
- The `docker run` example uses named volume `maitu-data`.
- First boot runs `octop init`; credentials are written to `/data/.octop/credential.txt`. With `OCTOP_DEFAULT_PASSWORD` unset a random password is generated; a specified password that the app password policy rejects falls back to a random one automatically (the container must never fail its first init because of a weak default).
- Provision new writable business, license identity and update-queue mounts for UID/GID `1000:1000`. Use `0700` for identity/queue directories and make the pinned public key readable by UID 1000. Back up and stop existing instances before reviewing/migrating their data ownership. Do not recursively chown unrelated paths or restore root execution to avoid migration.

### Upgrading an existing deployment

The default host directory has changed. Starting with the new default creates an empty instance, not a migration. Back up existing data, stop the old container, and reuse its original mount:

```bash
docker stop octop
OCTOP_DATA="$HOME/.octop" docker compose -f docker/docker-compose.yml up -d --build
```

Save the actual absolute `OCTOP_DATA` path in `docker/.env` for subsequent operations. Substitute the original custom directory if applicable. Retain existing named volumes and the capabilities/seccomp/AppArmor options shown above rather than creating empty volumes. Do not delete old data, use `down -v`, or run both instances against the same data simultaneously. This image now uses UID/GID `1000:1000`; review ownership before upgrading a former root-run instance.

Mobile Compose also uses `maitu` and `~/.maitu`; set `OCTOP_MOBILE_CONTAINER` to reuse an existing Android container name. The PostgreSQL helper container is now `maitu-postgres`; stop the old `octop-postgres` before upgrading. Its `octop_pgdata` volume, database user, and database name remain unchanged; retain the original Compose project name to reuse that volume.

This is operational branding, not source-origin concealment. Internal commands, tracebacks, and dependencies may still contain upstream identifiers. The image retains `LICENSE` and copyright notices.


### Health check

The image probes `GET /api/health`:

```bash
curl http://localhost:8088/api/health
```

### Operations

```bash
docker logs -f maitu
docker exec -it maitu octop --version
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml up -d --build
```
