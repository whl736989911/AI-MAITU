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

### Quick start

**Option 1: Compose (recommended)**

The source checkout may be named `/opt/maitu`. From that repository root:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

Open `http://localhost:8088`. If `OCTOP_DEFAULT_PASSWORD` is unset, a strong random password is generated on first init and written to `/data/.octop/credential.txt`; if it is set, it is used as-is (must be ≥8 characters with letters and digits; passwords rejected by the app password policy — e.g. common ones — fall back to a random one automatically). Change the password immediately after first login.

**Option 2: Build script**

```bash
bash docker/docker_build.sh
docker run -d \
  --name maitu \
  -p 8088:8088 \
  -v maitu-data:/data/.octop \
  -e HOME=/data \
  maitu:latest
```

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

### Upgrading an existing deployment

The default host directory has changed. Starting with the new default creates an empty instance, not a migration. Back up existing data, stop the old container, and reuse its original mount:

```bash
docker stop octop
OCTOP_DATA="$HOME/.octop" docker compose -f docker/docker-compose.yml up -d --build
```

Save the actual absolute `OCTOP_DATA` path in `docker/.env` for subsequent operations. Substitute the original custom directory if applicable. For an existing named volume, use `docker run --name maitu -v <original-volume>:/data/.octop -e HOME=/data -p 8088:8088 maitu:latest` instead of creating an empty volume. Do not delete old data, use `down -v`, or run both instances against the same data simultaneously.

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
