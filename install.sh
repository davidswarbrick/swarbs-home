#!/usr/bin/env bash
#
# install.sh — deploy swarbs-home on the NAS (Debian/Armbian).
# Run from a checkout of the repo, as root:
#
#     sudo ./install.sh
#
# Idempotent: safe to re-run after `git pull` to upgrade.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPX_HOME="/opt/pipx"
PIPX_BIN_DIR="/usr/local/bin"
CONF="/etc/swarbs-home.conf"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Please run as root (sudo ./install.sh)."
command -v apt-get >/dev/null || die "This installer expects a Debian/Armbian system (apt)."

# --- 1. system dependencies ------------------------------------------------
say "Checking system dependencies (flac, pipx, avahi, alsa-utils)…"
need_pkgs=()
command -v flac    >/dev/null || need_pkgs+=(flac)
command -v arecord >/dev/null || need_pkgs+=(alsa-utils)
command -v pipx    >/dev/null || need_pkgs+=(pipx)
command -v avahi-daemon >/dev/null || need_pkgs+=(avahi-daemon)
if [ "${#need_pkgs[@]}" -gt 0 ]; then
    say "Installing: ${need_pkgs[*]}"
    apt-get update -qq
    apt-get install -y "${need_pkgs[@]}"
else
    say "All system dependencies already present."
fi

# --- 2. python package via pipx (pins Flask etc. in an isolated venv) -------
say "Installing swarbs-home with pipx into ${PIPX_BIN_DIR}…"
export PIPX_HOME PIPX_BIN_DIR
pipx install --force "$REPO_DIR"
command -v swarbs-home >/dev/null || export PATH="$PIPX_BIN_DIR:$PATH"

# --- 3. config (don't clobber an existing one) -----------------------------
if [ -f "$CONF" ]; then
    warn "Keeping existing $CONF (see config/swarbs-home.conf.example for new keys)."
else
    say "Installing default config -> $CONF"
    install -m 0644 "$REPO_DIR/config/swarbs-home.conf.example" "$CONF"
    warn "Edit $CONF — check mixes_dir and the card URLs."
fi

# --- 4. systemd service ----------------------------------------------------
say "Installing systemd unit and enabling service…"
install -m 0644 "$REPO_DIR/config/swarbs-home.service" /etc/systemd/system/swarbs-home.service
systemctl daemon-reload
systemctl enable swarbs-home.service

# --- 5. avahi advertisement ------------------------------------------------
say "Advertising dashboard over mDNS…"
install -m 0644 "$REPO_DIR/config/avahi-swarbs-home.service" /etc/avahi/services/swarbs-home.service
systemctl reload avahi-daemon 2>/dev/null || systemctl restart avahi-daemon || true

# --- 6. OMV port move (manual, printed) ------------------------------------
cat <<'EOF'

------------------------------------------------------------------------
 ALMOST DONE — one manual step so the dashboard can own port 80.
------------------------------------------------------------------------
 OpenMediaVault currently serves its Workbench on :80. Move it to :8080:

   Option A (GUI):  OMV web UI -> System -> Workbench -> Port = 8080 -> Save/Apply.
   Option B (CLI):
       omv-confdbadm read conf.webadmin        # note current settings
       omv-confdbadm update conf.webadmin '{"port":8080,"sslport":8443,"enablessl":false,"forcesslonly":false}'
       omv-salt deploy run nginx

 After OMV frees :80, start swarbs-home:

       sudo systemctl start swarbs-home
       sudo systemctl status swarbs-home

 Then browse to:  http://nas.local/
 (Admin card -> http://nas.local:8080)

 NOTE: swarbs-home runs as root and has NO authentication — LAN use only.
------------------------------------------------------------------------
EOF

say "Install complete."
