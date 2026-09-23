#!/usr/bin/env bash
# Tailscale — reach the review form from ANY network, not just home wifi.
#
# A private encrypted mesh between your own devices only. No port forwarding, no
# public exposure, works through CGNAT and mobile data. Run this on the laptop,
# then install the Tailscale app on your phone and sign in with the SAME account.
#
#   ./jobpilot setup-tailscale
set -euo pipefail

echo "==> Installing Tailscale (Ubuntu 24.04 noble)"
curl -fsSL https://tailscale.com/install.sh | sh

echo
echo "==> Bringing it up — this prints a login URL, open it and sign in"
sudo tailscale up

echo
echo "==> Your machine on the tailnet:"
tailscale status --json | python3 -c "import json,sys; print('   ', json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))"

echo
echo "==> Optional: serve the form over real HTTPS with no port number"
echo "    sudo tailscale serve --bg 8765"
echo "    -> https://<your-machine>.<tailnet>.ts.net/"
echo
echo "NEXT:"
echo "  1. Install Tailscale on your phone, sign in with the SAME account."
echo "  2. Restart the form server:  systemctl --user restart jobpilot-form.service"
echo "     It detects Tailscale and prints the 'anywhere' URL."
