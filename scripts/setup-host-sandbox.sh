#!/usr/bin/env bash
# Native-install sandbox helper for Ubuntu 24.04+ (source installs).
#
# Installs / reverts ONLY the minimal host grants the platform sandbox needs:
#   1. the bubblewrap package plus an AppArmor profile that grants the
#      user-namespace permission to /usr/bin/bwrap alone, and
#   2. optionally, Playwright Chromium plus a precise executable profile that
#      keeps Chromium's own browser sandbox enabled.
#
# Nothing here disables the AppArmor userns restriction globally and no other
# permission is loosened. Every step is reversible.
#
# Usage (from the repository root):
#   sudo bash scripts/setup-host-sandbox.sh                  # bwrap + profile
#   bash  scripts/setup-host-sandbox.sh --browser            # Chromium as the
#                                                            # current (service) user
#   sudo bash scripts/setup-host-sandbox.sh --with-browser   # both, using SUDO_USER
#   bash  scripts/setup-host-sandbox.sh --status             # report only
#   sudo bash scripts/setup-host-sandbox.sh --revert         # remove bwrap profile
#   sudo bash scripts/setup-host-sandbox.sh --revert-browser # remove Chromium profile
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BWRAP_PROFILE_SRC="$REPO_ROOT/scripts/security/apparmor-bwrap-userns"
BWRAP_PROFILE_DST="/etc/apparmor.d/maitu-bwrap-userns"
CHROME_PROFILE_SRC="$REPO_ROOT/scripts/security/apparmor-playwright-chromium"
CHROME_PROFILE_DST="/etc/apparmor.d/maitu-playwright-chromium"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "error: this step needs root (run with sudo)" >&2
    exit 1
  fi
}

install_bwrap() {
  need_root
  if ! command -v bwrap >/dev/null 2>&1; then
    apt-get install -y bubblewrap
  fi
  if ! command -v apparmor_parser >/dev/null 2>&1; then
    apt-get install -y apparmor
  fi
  if [ -e /etc/apparmor.d/bwrap ]; then
    echo "Existing /etc/apparmor.d/bwrap is not overwritten. Review its userns grant and run the platform sandbox diagnosis." >&2
    return 1
  fi
  install -m 0644 "$BWRAP_PROFILE_SRC" "$BWRAP_PROFILE_DST"
  apparmor_parser -r "$BWRAP_PROFILE_DST"
  echo "ok: bwrap userns profile installed at $BWRAP_PROFILE_DST"
}

# Runs Playwright's official downloader as the CURRENT user so Chromium lands
# in that user's ~/.cache/ms-playwright (the directory the AppArmor profile
# glob covers). Under `sudo --with-browser` the original SUDO_USER is used.
install_browser() {
  local run_as="${SUDO_USER:-$(id -un)}"
  local python_bin="$REPO_ROOT/.venv/bin/python"
  if [ ! -x "$python_bin" ]; then
    echo "error: $python_bin not found — run 'uv sync --inexact --no-dev --extra office --extra knowledge-ocr' in the repository first" >&2
    exit 1
  fi
  if [ "$(id -un)" = "root" ] && [ -n "${SUDO_USER:-}" ]; then
    "$python_bin" -m playwright install-deps chromium
    sudo -u "$SUDO_USER" -H "$python_bin" -m playwright install chromium
  else
    "$python_bin" -m playwright install chromium
  fi
  echo "ok: Playwright Chromium installed for user '$run_as'"
}

install_chrome_profile() {
  need_root
  install -m 0644 "$CHROME_PROFILE_SRC" "$CHROME_PROFILE_DST"
  apparmor_parser -r "$CHROME_PROFILE_DST"
  echo "ok: Chromium userns profile installed at $CHROME_PROFILE_DST"
}

revert_bwrap() {
  need_root
  apparmor_parser -R "$BWRAP_PROFILE_DST" 2>/dev/null || true
  rm -f "$BWRAP_PROFILE_DST"
  echo "ok: bwrap profile removed (bubblewrap package kept)"
}

revert_browser_profile() {
  need_root
  apparmor_parser -R "$CHROME_PROFILE_DST" 2>/dev/null || true
  rm -f "$CHROME_PROFILE_DST"
  echo "ok: Chromium profile removed (browser files kept; remove with"
  echo "    .venv/bin/python -m playwright uninstall chromium if desired)"
}

status() {
  echo "== bubblewrap =="
  if command -v bwrap >/dev/null 2>&1; then
    bwrap --version
  else
    echo "bwrap: NOT installed"
  fi
  if [ -f "$BWRAP_PROFILE_DST" ]; then
    echo "profile present: $BWRAP_PROFILE_DST"
  else
    echo "profile absent:  $BWRAP_PROFILE_DST"
  fi
  echo "== browser channel =="
  if [ -f "$CHROME_PROFILE_DST" ]; then
    echo "profile present: $CHROME_PROFILE_DST"
  else
    echo "profile absent:  $CHROME_PROFILE_DST"
  fi
  if [ -d "$HOME/.cache/ms-playwright" ] && [ -n "$(ls -A "$HOME/.cache/ms-playwright" 2>/dev/null)" ]; then
    echo "playwright cache: $HOME/.cache/ms-playwright (populated)"
  else
    echo "playwright cache: $HOME/.cache/ms-playwright (empty)"
  fi
  echo "== kernel =="
  sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null ||
    echo "kernel.apparmor_restrict_unprivileged_userns: unknown (pre-24.04 kernel?)"
}

case "${1:-}" in
  "") install_bwrap ;;
  --with-browser)
    install_bwrap
    install_browser
    install_chrome_profile
    ;;
  --browser)
    install_browser
    echo "next: sudo bash scripts/setup-host-sandbox.sh --install-chrome-profile"
    ;;
  --install-chrome-profile) install_chrome_profile ;;
  --status) status ;;
  --revert) revert_bwrap ;;
  --revert-browser) revert_browser_profile ;;
  *)
    echo "usage: $0 [--with-browser|--browser|--install-chrome-profile|--status|--revert|--revert-browser]" >&2
    exit 2
    ;;
esac
