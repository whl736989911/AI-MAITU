#!/usr/bin/env bash
# Deploy MAITU with Docker Compose from an unpacked complete-source ZIP.
#
# Usage:
#   sudo bash deploy/ubuntu/install-docker.sh [options]
#
# This wrapper only orchestrates the repository's existing Compose files; it does
# not enable host Docker socket access. See docker/README_CN.md for the full
# security model.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="/var/lib/maitu-docker"
PORT="8088"
APPARMOR=0
SKIP_VERIFY=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: sudo bash deploy/ubuntu/install-docker.sh [options]

Run from an extracted maitu-source ZIP root. Requirements: Docker Engine with
the Compose v2 plugin. Options:
  --source DIR      source root (default: this script's repository root)
  --data-dir DIR    host data directory mounted at /data/.octop
                    (default: /var/lib/maitu-docker)
  --port PORT       host HTTP port (default: 8088)
  --apparmor        install/use docker/security/apparmor-maitu on Ubuntu 24.04+
  --skip-verify     skip SOURCE-PACKAGE.json hash verification
  --dry-run         print the plan and exit
  -h, --help        show this help

The container runs as UID/GID 1000 and does not receive the host Docker socket.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE_DIR="${2:?}"; shift 2 ;;
    --data-dir) DATA_DIR="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --apparmor) APPARMOR=1; shift ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

SOURCE_DIR="$(cd -- "$SOURCE_DIR" && pwd)"
DATA_DIR="${DATA_DIR%/}"

say()  { printf '\033[0;32m[maitu]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[maitu]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[maitu]\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "$DRY_RUN" -eq 0 && "${EUID}" -ne 0 ]]; then
  die "run this installer as root (sudo bash deploy/ubuntu/install-docker.sh)"
fi
[[ -f "$SOURCE_DIR/pyproject.toml" ]] || die "source root has no pyproject.toml: $SOURCE_DIR"
[[ -f "$SOURCE_DIR/docker/docker-compose.yml" ]] || die "docker/docker-compose.yml is missing"
[[ -f "$SOURCE_DIR/docker/Dockerfile" ]] || die "docker/Dockerfile is missing"
if [[ "$DRY_RUN" -eq 0 ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    die "Docker is not installed. Install Docker Engine + Compose v2 first, then rerun this script."
  fi
  if ! docker compose version >/dev/null 2>&1; then
    die "docker compose v2 is not available"
  fi
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  die "invalid port: $PORT"
fi
if [[ "$SKIP_VERIFY" -eq 0 ]]; then
  [[ -f "$SOURCE_DIR/SOURCE-PACKAGE.json" ]] || die "SOURCE-PACKAGE.json missing; pass --skip-verify only for a trusted tree"
  python3 "$SCRIPT_DIR/verify-source.py" "$SOURCE_DIR" || die "source package verification failed"
fi

overlay_args=()
if [[ "$APPARMOR" -eq 1 ]]; then
  [[ -f "$SOURCE_DIR/docker/security/apparmor-maitu" ]] || die "AppArmor policy file is missing"
  [[ -f "$SOURCE_DIR/docker/docker-compose.apparmor.yml" ]] || die "AppArmor Compose overlay is missing"
  overlay_args+=(-f "$SOURCE_DIR/docker/docker-compose.apparmor.yml")
fi

say "source:     $SOURCE_DIR"
say "data dir:   $DATA_DIR"
say "bind port:  $PORT"
say "compose:    docker/docker-compose.yml $([[ "$APPARMOR" -eq 1 ]] && echo '+ apparmor overlay' || true)"
say "first-run password: $DATA_DIR/credential.txt (if generated)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  say "dry run: would create $DATA_DIR (UID/GID 1000) and run:"
  printf '  OCTOP_DATA=%q OCTOP_PORT=%q docker compose -f %q %s up -d --build\n' \
    "$DATA_DIR" "$PORT" "$SOURCE_DIR/docker/docker-compose.yml" "${overlay_args[*]:-}"
  exit 0
fi

if [[ "$APPARMOR" -eq 1 ]]; then
  if ! command -v apparmor_parser >/dev/null 2>&1; then
    die "apparmor_parser is missing; install apparmor-utils or omit --apparmor"
  fi
  say "loading the MAITU sandbox AppArmor policy"
  install -m 0644 "$SOURCE_DIR/docker/security/apparmor-maitu" /etc/apparmor.d/maitu-sandbox
  apparmor_parser -r /etc/apparmor.d/maitu-sandbox
fi

# The image runs as UID/GID 1000; keep the bind-mounted data directory writable
# by that numeric identity even if no local account owns it.
install -d -m 0700 -o 1000 -g 1000 "$DATA_DIR"

say "building image and starting Compose project"
(
  cd "$SOURCE_DIR"
  OCTOP_DATA="$DATA_DIR" OCTOP_PORT="$PORT" \
    docker compose -f docker/docker-compose.yml "${overlay_args[@]}" up -d --build
)

say "waiting for HTTP health endpoint"
healthy=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done
if [[ "$healthy" -ne 1 ]]; then
  docker compose -f docker/docker-compose.yml ps >&2 || true
  docker compose -f docker/docker-compose.yml logs --tail=100 maitu >&2 || true
  die "container did not become healthy"
fi

primary_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$primary_ip" ]] || primary_ip="<host-ip>"
say "installation complete"
cat <<EOF

  Open:   http://$primary_ip:$PORT/
  Setup:  http://$primary_ip:$PORT/setup
  Data:   $DATA_DIR  (bind-mounted at /data/.octop)
  Logs:   docker compose -f docker/docker-compose.yml logs -f maitu
  Stop:   docker compose -f docker/docker-compose.yml down
  Status: docker compose -f docker/docker-compose.yml ps

If OCTOP_DEFAULT_PASSWORD was unset, the generated first-run password is in
$DATA_DIR/credential.txt. Complete the browser wizard, then configure an LLM
provider. Do not expose port $PORT to the internet without TLS/reverse proxy.
EOF
