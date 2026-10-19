#!/usr/bin/env bash
# Install MAITU from an unpacked complete-source ZIP on Ubuntu.
#
# Usage:
#   sudo bash deploy/ubuntu/install-source.sh [options]
#
# Run this from the extracted source root (the directory containing pyproject.toml).
# The script creates a dedicated 'maitu' service user, a Python 3.12 virtualenv,
# a systemd unit, and starts the platform on the requested bind address.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
DATA_HOME="/var/lib/maitu"
SERVICE_USER="maitu"
BIND_HOST="0.0.0.0"
PORT="8088"
EXTRAS="office,knowledge-ocr"
BUILD_FRONTEND=0
NO_APT=0
OPEN_FIREWALL=0
SKIP_VERIFY=0
FORCE=0
DRY_RUN=0
ASSUME_YES=0
PYTHON_INSTALL_DIR="/opt/maitu-python"

usage() {
  cat <<'EOF'
Usage: sudo bash deploy/ubuntu/install-source.sh [options]

Run from an extracted maitu-source ZIP root. Options:
  --source DIR          Source root (default: this script's repository root)
  --data-home DIR       MAITU_HOME data directory (default: /var/lib/maitu)
  --service-user NAME   systemd user/group (default: maitu)
  --host HOST           bind host (default: 0.0.0.0 for LAN access)
  --port PORT           HTTP port (default: 8088)
  --extras LIST         comma-separated extras (default: office,knowledge-ocr)
                        supported: office,knowledge-ocr,local-embedding,browser,desktop
  --build-frontend      install Node 22 and rebuild dashboard assets
  --no-apt              skip apt package installation (packages already present)
  --open-firewall       allow the port with ufw when ufw is active
  --skip-verify         skip SOURCE-PACKAGE.json hash verification
  --force               reinstall over an existing .venv/service unit
  --yes                 non-interactive apt (still no prompts in this script)
  --dry-run             validate and print the plan, change nothing
  -h, --help            show this help

After success open http://<host>:<port>/ and complete the first-run wizard.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE_DIR="${2:?}"; shift 2 ;;
    --data-home) DATA_HOME="${2:?}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    --host) BIND_HOST="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --extras) EXTRAS="${2:?}"; shift 2 ;;
    --build-frontend) BUILD_FRONTEND=1; shift ;;
    --no-apt) NO_APT=1; shift ;;
    --open-firewall) OPEN_FIREWALL=1; shift ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    --force) FORCE=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

SOURCE_DIR="$(cd -- "$SOURCE_DIR" && pwd)"
DATA_HOME="${DATA_HOME%/}"
UNIT_PATH="/etc/systemd/system/maitu.service"

say()  { printf '\033[0;32m[maitu]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[maitu]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[maitu]\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "$DRY_RUN" -eq 0 && "${EUID}" -ne 0 ]]; then
  die "run this installer as root (sudo bash deploy/ubuntu/install-source.sh)"
fi
if [[ ! -f "$SOURCE_DIR/pyproject.toml" ]]; then
  die "--source does not contain pyproject.toml: $SOURCE_DIR"
fi
if [[ ! -f "$SOURCE_DIR/uv.lock" ]]; then
  die "uv.lock is missing; use the complete source ZIP, not an incomplete copy"
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  die "invalid port: $PORT"
fi
if [[ ! "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
  die "invalid service user: $SERVICE_USER"
fi
if [[ "$SOURCE_DIR" =~ [[:space:]] ]]; then
  die "source path must not contain whitespace: $SOURCE_DIR"
fi
if [[ "$DATA_HOME" =~ [[:space:]] ]]; then
  die "data-home path must not contain whitespace: $DATA_HOME"
fi

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    die "this installer targets Ubuntu; detected ID=${ID:-unknown}. Use --force? No: edit the script only if you know the distro differences."
  fi
  case "${VERSION_ID:-}" in
    22.04|24.04|26.04) ;;
    *) warn "tested on Ubuntu 22.04/24.04/26.04; detected ${VERSION_ID:-unknown}" ;;
  esac
else
  die "cannot read /etc/os-release; this installer targets Ubuntu"
fi

if [[ "$SKIP_VERIFY" -eq 0 ]]; then
  if [[ -f "$SOURCE_DIR/SOURCE-PACKAGE.json" ]]; then
    if command -v python3 >/dev/null 2>&1; then
      python3 "$SCRIPT_DIR/verify-source.py" "$SOURCE_DIR" || die "source package verification failed"
    else
      warn "python3 not found; skipping manifest verification (install apt first)"
    fi
  else
    die "SOURCE-PACKAGE.json is missing; use the complete source ZIP or pass --skip-verify"
  fi
fi

if [[ -e "$SOURCE_DIR/.venv" && "$FORCE" -eq 0 ]]; then
  die "$SOURCE_DIR/.venv already exists. Use deploy/ubuntu/upgrade-source.sh for an upgrade, or pass --force to reinstall."
fi
if systemctl list-unit-files maitu.service >/dev/null 2>&1 && systemctl is-enabled maitu.service >/dev/null 2>&1 && [[ "$FORCE" -eq 0 && "$DRY_RUN" -eq 0 ]]; then
  die "maitu.service is already installed. Use deploy/ubuntu/upgrade-source.sh, or pass --force to rewrite it."
fi

validate_extras() {
  local extra
  IFS=',' read -ra _extra_list <<< "$EXTRAS"
  for extra in "${_extra_list[@]}"; do
    case "$extra" in
      office|knowledge-ocr|local-embedding|browser|desktop) ;;
      "") ;;
      *) die "unsupported extra: $extra (use office,knowledge-ocr,local-embedding,browser,desktop)" ;;
    esac
  done
}
validate_extras

if [[ ! -f "$SOURCE_DIR/src/octop/dashboard/index.html" && "$BUILD_FRONTEND" -eq 0 ]]; then
  die "built dashboard is missing from this ZIP; rebuild it or pass --build-frontend"
fi

say "source:      $SOURCE_DIR"
say "data home:   $DATA_HOME"
say "service:     systemd unit maitu.service (User=$SERVICE_USER)"
say "listen:      http://$BIND_HOST:$PORT"
say "extras:      $EXTRAS"
say "frontend:    $([[ "$BUILD_FRONTEND" -eq 1 ]] && echo 'rebuild with Node 22' || echo 'use built assets from ZIP')"

if [[ "$DRY_RUN" -eq 1 ]]; then
  say "dry run: no changes made"
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 && -t 0 ]]; then
  read -r -p "Install system packages, create '$SERVICE_USER' and start maitu.service? [y/N] " answer
  [[ "$answer" =~ ^[yY]$ ]] || die "cancelled"
fi

run() {
  printf '  + %s\n' "$*"
  "$@"
}

if [[ "$NO_APT" -eq 0 ]]; then
  say "installing OS build/runtime dependencies (apt)"
  run env DEBIAN_FRONTEND=noninteractive apt-get update
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl unzip build-essential python3 python3-venv pkg-config \
    libssl-dev libffi-dev zlib1g-dev libjpeg-dev libfreetype6-dev libtiff-dev \
    libwebp-dev liblcms2-dev libopenjp2-7-dev libsqlite3-dev libxml2-dev \
    libxslt1-dev linux-libc-dev libasound2-dev
else
  for command in curl unzip python3 gcc pkg-config; do
    command -v "$command" >/dev/null 2>&1 || die "--no-apt but $command is missing"
  done
fi

if ! command -v uv >/dev/null 2>&1; then
  say "installing uv into /usr/local/bin"
  run bash -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh'
fi
run uv --version

say "installing uv-managed Python 3.12 under $PYTHON_INSTALL_DIR"
run install -d -m 0755 "$PYTHON_INSTALL_DIR"
run env UV_PYTHON_INSTALL_DIR="$PYTHON_INSTALL_DIR" uv python install 3.12
run chmod -R go+rX "$PYTHON_INSTALL_DIR"

if ! getent group "$SERVICE_USER" >/dev/null 2>&1; then
  run groupadd --system "$SERVICE_USER"
fi
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  say "creating service account $SERVICE_USER"
  run useradd --system --home-dir "$DATA_HOME" --create-home \
    --shell /usr/sbin/nologin --gid "$SERVICE_USER" "$SERVICE_USER"
fi
run install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_HOME"

if [[ "$BUILD_FRONTEND" -eq 1 ]]; then
  if ! command -v node >/dev/null 2>&1 || [[ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]]; then
    say "installing Node.js 22 from NodeSource"
    run bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash -'
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  fi
  say "building dashboard assets"
  run npm ci --prefix "$SOURCE_DIR/dashboard"
  run npm run build --prefix "$SOURCE_DIR/dashboard"
fi

say "installing Python dependencies and the project into $SOURCE_DIR/.venv"
extra_args=()
IFS=',' read -ra _extras <<< "$EXTRAS"
for extra in "${_extras[@]}"; do
  [[ -n "$extra" ]] && extra_args+=(--extra "$extra")
done
(
  cd "$SOURCE_DIR"
  UV_PROJECT_ENVIRONMENT="$SOURCE_DIR/.venv" \
  UV_PYTHON_INSTALL_DIR="$PYTHON_INSTALL_DIR" \
    uv sync --frozen --inexact --no-dev --python 3.12 "${extra_args[@]}"
)
[[ -x "$SOURCE_DIR/.venv/bin/maitu" ]] || die "uv sync finished but .venv/bin/maitu is missing"
[[ -f "$SOURCE_DIR/src/octop/dashboard/index.html" ]] || die "dashboard assets are missing after setup"

say "setting read-only source permissions for the service account"
run chown -R root:"$SERVICE_USER" "$SOURCE_DIR"
run chmod -R go+rX "$SOURCE_DIR"

if [[ -f "$UNIT_PATH" ]]; then
  backup="$UNIT_PATH.bak.$(date +%Y%m%d-%H%M%S)"
  warn "existing unit found; backing up to $backup"
  run cp -a "$UNIT_PATH" "$backup"
fi

say "writing systemd unit $UNIT_PATH"
cat > "$UNIT_PATH" <<EOF
[Unit]
Description=MAITU AI Server
Documentation=https://github.com/whl736989911/AI-MAITU
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
Environment="HOME=$DATA_HOME"
Environment="MAITU_HOME=$DATA_HOME"
Environment="PYTHONDONTWRITEBYTECODE=1"
Environment="PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ExecStart=$SOURCE_DIR/.venv/bin/maitu run --host $BIND_HOST --port $PORT
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$UNIT_PATH"

run systemctl daemon-reload
run systemctl enable maitu.service
run systemctl restart maitu.service

say "waiting for the HTTP health endpoint"
healthy=0
for _ in $(seq 1 45); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done
if [[ "$healthy" -ne 1 ]]; then
  systemctl status maitu.service --no-pager >&2 || true
  journalctl -u maitu.service -n 80 --no-pager >&2 || true
  die "service did not become healthy; inspect logs above and /var/lib/maitu/logs/"
fi

if [[ "$OPEN_FIREWALL" -eq 1 ]] && command -v ufw >/dev/null 2>&1; then
  if ufw status | grep -qi active; then
    say "allowing TCP $PORT through ufw"
    run ufw allow "$PORT/tcp"
  else
    warn "ufw is not active; skipping firewall rule"
  fi
fi

primary_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$primary_ip" ]] || primary_ip="<host-ip>"
say "installation complete"
cat <<EOF

  Open:        http://$primary_ip:$PORT/
  Setup:       http://$primary_ip:$PORT/setup
  Data home:   $DATA_HOME  (mode 0700, owner $SERVICE_USER)
  Logs:        journalctl -u maitu.service -f
               $DATA_HOME/logs/
  Service:     systemctl status maitu.service
               systemctl restart maitu.service

Next: complete the first-run wizard in a browser, then configure an LLM
provider under Settings -> Models. The service runs as '$SERVICE_USER', not root.
For LAN access, also allow TCP $PORT in the host firewall / cloud security group.
EOF
