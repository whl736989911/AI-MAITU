#!/usr/bin/env bash
# Upgrade an existing native MAITU source installation on Ubuntu.
#
# Usage:
#   sudo bash /opt/maitu.new/deploy/ubuntu/upgrade-source.sh \
#     --source /opt/maitu.new --install-dir /opt/maitu
#
# The script stops the service, verifies and backs up the data home, moves the
# old source aside, installs the new source at the same path, re-syncs the venv,
# starts the service, and checks HTTP health. The old source directory and the
# data backup are kept for rollback.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="/opt/maitu"
DATA_HOME="/var/lib/maitu"
BACKUP_DIR="/var/backups/maitu"
SKIP_VERIFY=0
ASSUME_YES=0
DRY_RUN=0
PYTHON_INSTALL_DIR="/opt/maitu-python"
SERVICE_USER="maitu"
PORT="8088"
EXTRAS="office,knowledge-ocr"

usage() {
  cat <<'EOF'
Usage: sudo bash deploy/ubuntu/upgrade-source.sh [options]

Run from the NEW extracted source tree (the one containing pyproject.toml):
  --source DIR        new source tree (default: this script's repository root)
  --install-dir DIR   current installation path (default: /opt/maitu)
  --data-home DIR     current MAITU_HOME (default: /var/lib/maitu)
  --backup-dir DIR    backup destination (default: /var/backups/maitu)
  --service-user NAME systemd user/group (default: maitu)
  --port PORT         HTTP port for the health check (default: 8088)
  --extras LIST       comma-separated extras to sync (default: office,knowledge-ocr)
  --skip-verify       skip SOURCE-PACKAGE.json hash verification
  --yes               do not ask for confirmation
  --dry-run           print the plan and exit
  -h, --help          show this help

The service must be a native systemd unit named maitu.service. This script
does not restore a database automatically; keep the printed backup and read the
rollback notes printed on success/failure.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE_DIR="${2:?}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:?}"; shift 2 ;;
    --data-home) DATA_HOME="${2:?}"; shift 2 ;;
    --backup-dir) BACKUP_DIR="${2:?}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --extras) EXTRAS="${2:?}"; shift 2 ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

SOURCE_DIR="$(cd -- "$SOURCE_DIR" && pwd)"
INSTALL_DIR="${INSTALL_DIR%/}"
DATA_HOME="${DATA_HOME%/}"
BACKUP_DIR="${BACKUP_DIR%/}"
STAMP="$(date +%Y%m%d-%H%M%S)"
PREVIOUS_DIR="${INSTALL_DIR}.prev-${STAMP}"

say()  { printf '\033[0;32m[maitu]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[maitu]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[maitu]\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "$DRY_RUN" -eq 0 && "${EUID}" -ne 0 ]]; then
  die "run this upgrade as root"
fi
[[ -f "$SOURCE_DIR/pyproject.toml" ]] || die "new source has no pyproject.toml: $SOURCE_DIR"
[[ -f "$INSTALL_DIR/pyproject.toml" ]] || die "current install not found at $INSTALL_DIR"
[[ -d "$DATA_HOME" ]] || die "data home not found: $DATA_HOME"
systemctl cat maitu.service >/dev/null 2>&1 || die "maitu.service is not installed"
if [[ "$SOURCE_DIR" == "$INSTALL_DIR" ]]; then
  die "--source and --install-dir are the same; extract the new ZIP to a new directory first"
fi
case "$SOURCE_DIR/" in
  "$INSTALL_DIR/"*) die "--source must not be inside --install-dir" ;;
esac
case "$INSTALL_DIR/" in
  "$SOURCE_DIR/"*) die "--install-dir must not be inside --source" ;;
esac
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  die "invalid port: $PORT"
fi
if [[ ! -f "$SOURCE_DIR/src/octop/dashboard/index.html" ]]; then
  die "new source has no built dashboard; use a complete source ZIP"
fi
if [[ "$SKIP_VERIFY" -eq 0 ]]; then
  [[ -f "$SOURCE_DIR/SOURCE-PACKAGE.json" ]] || die "SOURCE-PACKAGE.json missing from new source"
  python3 "$SCRIPT_DIR/verify-source.py" "$SOURCE_DIR" || die "new source verification failed"
fi

say "new source:   $SOURCE_DIR"
say "current dir:  $INSTALL_DIR"
say "data home:    $DATA_HOME"
say "backup dir:   $BACKUP_DIR"
say "old source:   $PREVIOUS_DIR"

if [[ "$DRY_RUN" -eq 1 ]]; then
  say "dry run: no changes made"
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 && -t 0 ]]; then
  read -r -p "Stop the service, back up data, and switch to the new source? [y/N] " answer
  [[ "$answer" =~ ^[yY]$ ]] || die "cancelled"
fi

install -d -m 0700 "$BACKUP_DIR"
say "stopping maitu.service"
systemctl stop maitu.service

switched=0
rollback() {
  trap - ERR
  set +e
  if [[ "$switched" -eq 1 ]]; then
    systemctl stop maitu.service 2>/dev/null
    if [[ -d "$INSTALL_DIR" ]]; then
      failed_dir="${INSTALL_DIR}.failed-${STAMP}"
      mv -- "$INSTALL_DIR" "$failed_dir"
      warn "new source kept at $failed_dir"
    fi
    if [[ -d "$PREVIOUS_DIR" ]]; then
      mv -- "$PREVIOUS_DIR" "$INSTALL_DIR"
      systemctl daemon-reload
    fi
  fi
  # Restart the old source (or whatever is currently at INSTALL_DIR).
  systemctl start maitu.service
  systemctl status maitu.service --no-pager || true
  set -e
  die "upgrade failed; old source restored. Data backup: $backup_file (not restored automatically; restore it yourself if the new version migrated the database)."
}
trap 'rollback' ERR

say "backing up data home to $BACKUP_DIR"
data_parent="$(dirname -- "$DATA_HOME")"
data_name="$(basename -- "$DATA_HOME")"
backup_file="$BACKUP_DIR/maitu-home-${STAMP}.tar.gz"
tar --numeric-owner -C "$data_parent" -czf "$backup_file" "$data_name"
say "data backup: $backup_file"

say "moving current source to $PREVIOUS_DIR"
mv -- "$INSTALL_DIR" "$PREVIOUS_DIR"
switched=1
say "installing new source at $INSTALL_DIR"
mv -- "$SOURCE_DIR" "$INSTALL_DIR"

say "syncing Python dependencies in $INSTALL_DIR"
extra_args=()
IFS=',' read -ra _extras <<< "$EXTRAS"
for extra in "${_extras[@]}"; do
  case "$extra" in
    office|knowledge-ocr|local-embedding|browser|desktop) extra_args+=(--extra "$extra") ;;
    "") ;;
    *) die "unsupported extra: $extra" ;;
  esac
done
(
  cd "$INSTALL_DIR"
  UV_PROJECT_ENVIRONMENT="$INSTALL_DIR/.venv" \
  UV_PYTHON_INSTALL_DIR="$PYTHON_INSTALL_DIR" \
    uv sync --frozen --inexact --no-dev --python 3.12 "${extra_args[@]}"
)
chown -R root:"$SERVICE_USER" "$INSTALL_DIR"
chmod -R go+rX "$INSTALL_DIR"

say "reloading and starting maitu.service"
systemctl daemon-reload
systemctl start maitu.service

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
  rollback
fi
trap - ERR

say "upgrade complete; health check OK"
cat <<EOF

  Previous source: $PREVIOUS_DIR
  Data backup:     $backup_file
  Keep both until you have verified users, agents, channels and data.

  Rollback (source only; database may have been migrated):
    sudo systemctl stop maitu.service
    sudo mv $INSTALL_DIR ${INSTALL_DIR}.failed-\$(date +%Y%m%d-%H%M%S)
    sudo mv $PREVIOUS_DIR $INSTALL_DIR
    sudo systemctl start maitu.service

  If the new version migrated the database and you must go back fully,
  stop the service and restore the data backup before starting old code.
EOF
