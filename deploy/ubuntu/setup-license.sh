#!/usr/bin/env bash
# Configure a native MAITU installation to trust a license center.
#
# Usage:
#   sudo bash deploy/ubuntu/setup-license.sh \
#     --trusted-keys /root/license-trusted-keys.json \
#     --center-url https://license.example.com
#
# This only configures public trust material and the protected instance
# identity directory; it never asks for or stores the center's signing private
# key. Activation itself is done from the locked admin UI or with the
# `maitu license activate` CLI.
set -euo pipefail

TRUSTED_KEYS=""
CENTER_URL=""
STATE_DIR="/var/lib/maitu-license"
SERVICE_USER="maitu"
UNIT="maitu.service"
ALLOW_LOOPBACK=0
DRY_RUN=0
PORT="8088"

usage() {
  cat <<'EOF'
Usage: sudo bash deploy/ubuntu/setup-license.sh [options]

  --trusted-keys FILE     public keyring JSON exported by the center's
                          `license-center export-trust` command
  --center-url URL        license center root URL (must be HTTPS, e.g.
                          https://license.example.com)
  --state-dir DIR         protected instance identity/credential directory
                          (default: /var/lib/maitu-license)
  --service-user NAME     service account that owns the state (default: maitu)
  --unit NAME             systemd unit (default: maitu.service)
  --port PORT             platform HTTP port for the status hint (default: 8088)
  --allow-loopback        testing only: allow http://127.0.0.1 / http://localhost
  --dry-run               print the plan, change nothing
  -h, --help              show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --trusted-keys) TRUSTED_KEYS="${2:?}"; shift 2 ;;
    --center-url) CENTER_URL="${2:?}"; shift 2 ;;
    --state-dir) STATE_DIR="${2:?}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?}"; shift 2 ;;
    --unit) UNIT="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --allow-loopback) ALLOW_LOOPBACK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[0;32m[maitu]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[maitu]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[maitu]\033[0m %s\n' "$*" >&2; exit 1; }

[[ -n "$TRUSTED_KEYS" ]] || die "--trusted-keys is required"
[[ -f "$TRUSTED_KEYS" ]] || die "trusted keys file not found: $TRUSTED_KEYS"
[[ -n "$CENTER_URL" ]] || die "--center-url is required for an online license center"
[[ ! "$CENTER_URL" =~ [[:space:]] ]] || die "center URL must not contain whitespace"
[[ "$CENTER_URL" != */ ]] || die "center URL must be the root URL without a trailing slash"
[[ ! "$STATE_DIR" =~ [[:space:]] ]] || die "state dir must not contain whitespace"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "invalid port: $PORT"
if [[ "$DRY_RUN" -eq 0 && "${EUID}" -ne 0 ]]; then
  die "run this script as root"
fi
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required to validate the keyring JSON"
fi
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  die "service user does not exist: $SERVICE_USER (install the platform first)"
fi
if ! systemctl cat "$UNIT" >/dev/null 2>&1; then
  die "systemd unit not found: $UNIT (install the platform first)"
fi

if [[ "$CENTER_URL" =~ ^https:// ]]; then
  :
elif [[ "$ALLOW_LOOPBACK" -eq 1 && "$CENTER_URL" =~ ^http://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]+)?$ ]]; then
  warn "allowing a loopback HTTP center URL for testing only"
else
  die "center URL must use HTTPS (pass --allow-loopback only for local 127.0.0.1 testing)"
fi

python3 - "$TRUSTED_KEYS" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError) as exc:
    raise SystemExit(f"invalid trusted-keys JSON: {exc}")
if not isinstance(data, dict) or not data:
    raise SystemExit("trusted-keys JSON must be a non-empty object mapping KID to PEM")
for kid, pem in data.items():
    if not isinstance(kid, str) or not kid:
        raise SystemExit("trusted-keys contains an empty/non-string key id")
    if not isinstance(pem, str) or "BEGIN PUBLIC KEY" not in pem:
        raise SystemExit(f"trusted key {kid!r} is not a PEM public key")
print(f"trusted keyring OK: {len(data)} public key(s)")
PY

say "trusted keyring: $TRUSTED_KEYS"
say "license center: $CENTER_URL"
say "instance state: $STATE_DIR (owner $SERVICE_USER, mode 0700)"
say "systemd unit:   $UNIT"

if [[ "$DRY_RUN" -eq 1 ]]; then
  say "dry run: no changes made"
  exit 0
fi

install -d -m 0755 /etc/maitu/license
trusted_dest="/etc/maitu/license/trusted-keys.json"
if [[ "$(realpath "$TRUSTED_KEYS")" == "$(realpath -m "$trusted_dest")" ]]; then
  chown root:"$SERVICE_USER" "$trusted_dest"
  chmod 0640 "$trusted_dest"
else
  install -m 0640 -o root -g "$SERVICE_USER" "$TRUSTED_KEYS" "$trusted_dest"
fi
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"

dropin_dir="/etc/systemd/system/${UNIT}.d"
dropin="$dropin_dir/50-license.conf"
install -d -m 0755 "$dropin_dir"
if [[ -f "$dropin" ]]; then
  backup="$dropin.bak.$(date +%Y%m%d-%H%M%S)"
  warn "existing license drop-in found; backing up to $backup"
  cp -a "$dropin" "$backup"
fi
{
  echo "[Service]"
  echo "Environment=\"MAITU_LICENSE_TRUSTED_KEYS_FILE=$trusted_dest\""
  echo "Environment=\"MAITU_LICENSE_STATE_DIR=$STATE_DIR\""
  echo "Environment=\"MAITU_LICENSE_CENTER_URL=$CENTER_URL\""
  [[ "$ALLOW_LOOPBACK" -eq 1 ]] && echo 'Environment="MAITU_LICENSE_ALLOW_LOOPBACK=true"'
} > "$dropin"
chmod 0644 "$dropin"

say "reloading systemd and restarting $UNIT"
systemctl daemon-reload
systemctl restart "$UNIT"
systemctl is-active "$UNIT" >/dev/null || {
  systemctl status "$UNIT" --no-pager >&2 || true
  journalctl -u "$UNIT" -n 80 --no-pager >&2 || true
  die "service did not become active; license configuration may be invalid"
}

primary_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$primary_ip" ]] || primary_ip="<host-ip>"
cat <<EOF

License trust is configured and the service restarted.

  Center:     $CENTER_URL
  State dir:  $STATE_DIR
  Keyring:    $trusted_dest

Next: in the license center create the customer and a one-time activation code
(with license expiry and code validity), then open the platform's locked page
and enter the code:

  http://$primary_ip:$PORT/

The instance generates its own Ed25519 identity under the state dir and proves
possession to the center; the center only keeps the public key. Do not copy
the state dir to another host; use the center's host-transfer flow instead.
EOF
