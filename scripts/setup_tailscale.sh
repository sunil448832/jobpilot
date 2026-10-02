#!/usr/bin/env bash
# Tailscale — reach the review form from ANY network, not just home wifi.
#
# A private encrypted mesh between your own devices only. No port forwarding, no
# public exposure, works through CGNAT and mobile data. Run this on the laptop,
# then install the Tailscale app on your phone and sign in with the SAME account.
#
# Runs entirely as you: no sudo, no apt, no system service, no change to routing,
# DNS or the firewall. The official static binaries live in ~/softwares/tailscale,
# the daemon runs in userspace-networking mode (no network device; it hands the
# phone's connections to localhost), and its state + socket live in data/tailscale
# (gitignored, owner-only) so the login survives an OS reinstall that keeps ~/work.
#
#   ./jobpilot setup-tailscale     first run: download + log in; later: start if stopped
#
# Undo everything: pkill -u "$USER" -f "$TS_HOME/tailscaled"; rm -rf ~/softwares/tailscale data/tailscale
set -euo pipefail

TOOL="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
TS_HOME="$HOME/softwares/tailscale"
STATE="$TOOL/data/tailscale"
SOCK="$STATE/tailscaled.sock"
ts() { "$TS_HOME/tailscale" --socket "$SOCK" "$@"; }

if [ ! -x "$TS_HOME/tailscaled" ]; then
  echo "==> Downloading Tailscale (static binaries, checksum verified) to $TS_HOME"
  f=$(curl -fsSL "https://pkgs.tailscale.com/stable/?mode=json" \
      | python3 -c "import json,sys;print(json.load(sys.stdin)['Tarballs']['amd64'])")
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "https://pkgs.tailscale.com/stable/$f" -o "$tmp/$f"
  echo "$(curl -fsSL "https://pkgs.tailscale.com/stable/$f.sha256")  $tmp/$f" | sha256sum -c --quiet -
  mkdir -p "$TS_HOME" && tar -xzf "$tmp/$f" --strip-components=1 -C "$TS_HOME"
fi

mkdir -p "$STATE" && chmod 700 "$STATE"
if ! ts status >/dev/null 2>&1 && ! pgrep -u "$USER" -f "$TS_HOME/tailscaled" >/dev/null; then
  echo "==> Starting tailscaled as $USER (userspace networking)"
  nohup "$TS_HOME/tailscaled" --tun=userspace-networking --statedir="$STATE" --socket="$SOCK" \
        >>"$STATE/tailscaled.log" 2>&1 &
  for _ in $(seq 20); do [ -S "$SOCK" ] && break; sleep 0.5; done
fi

if ! ts status >/dev/null 2>&1; then
  echo "==> Logging in — open the URL it prints and sign in"
  ts up --hostname="$(hostname)"
fi

echo
echo "==> Your machine on the tailnet:"
ts status --json | python3 -c "import json,sys; print('   ', json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))"
echo
echo "NEXT:"
echo "  1. Install Tailscale on your phone, sign in with the SAME account."
echo "  2. Start the form server (./jobpilot serve); ./jobpilot link prints the 'anywhere' URL."
echo "  After a reboot, run ./jobpilot setup-tailscale again to start it (no login needed)."
