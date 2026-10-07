#!/usr/bin/env python3
"""
tunnel.py — the review site on a public https link, on any network, no VPN on the phone.

  ngrok      when .env has NGROK_AUTHTOKEN and NGROK_DOMAIN: his free fixed address
             (https://<name>.ngrok-free.app) — the link to bookmark. ~/softwares/ngrok.
  cloudflare otherwise: a quick tunnel, no account (~/softwares/cloudflared), whose
             https://<random>.trycloudflare.com address changes each time it starts.

Every page and action still needs the FORM_TOKEN link (?t=...), so the address alone
opens nothing; with no FORM_TOKEN in .env the tunnel refuses to start.

The current address is kept in data/tunnel_url (removed when the tunnel stops); the
digest and `./jobpilot link` read it, and Telegram is told when it differs from the
last one told (data/tunnel_told) — once for a fixed address, on each start for a quick one.

Run by jobpilot-tunnel.service:  python -m jobpilot.review.tunnel
"""
import os
import re
import subprocess
import sys

from jobpilot.core.config import cfg
from jobpilot.core.paths import DATA

BIN = os.path.expanduser("~/softwares/cloudflared/cloudflared")
NGROK = os.path.expanduser("~/softwares/ngrok/ngrok")
URL_FILE = os.path.join(DATA, "tunnel_url")
TOLD_FILE = os.path.join(DATA, "tunnel_told")
URL_RX = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def current():
    """The tunnel's address while it runs, else None."""
    try:
        return open(URL_FILE).read().strip() or None
    except OSError:
        return None


def main():
    import signal
    from jobpilot.core import daily
    # systemd stops the service with SIGTERM: exit through `finally`, so the stopped
    # tunnel's address does not linger in data/tunnel_url
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    token = daily.env().get("FORM_TOKEN", "")
    if not token:
        sys.exit("FORM_TOKEN missing in .env: the site would be open to anyone — not starting")
    port = cfg("server.port", 8765)
    e = daily.env()
    domain = (e.get("NGROK_DOMAIN") or "").strip().removeprefix("https://").strip("/")
    if e.get("NGROK_AUTHTOKEN") and domain and os.access(NGROK, os.X_OK):
        # his fixed address: the token goes in the environment, never on the command line
        p = subprocess.Popen([NGROK, "http", str(port), f"--url=https://{domain}", "--log=stdout",
                              "--log-format=logfmt"], env={**os.environ, "NGROK_AUTHTOKEN": e["NGROK_AUTHTOKEN"]},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        url_rx, fixed = re.compile(r"https://" + re.escape(domain)), True
    else:
        if not os.access(BIN, os.X_OK):
            sys.exit(f"cloudflared not found at {BIN}")
        p = subprocess.Popen([BIN, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        url_rx, fixed = URL_RX, False
    told = None
    try:
        for line in p.stdout:
            print(line, end="", flush=True)
            m = url_rx.search(line)
            if m and m.group(0) != told and ("started tunnel" in line or not fixed):
                told = m.group(0)
                with open(URL_FILE, "w") as f:
                    f.write(told)
                link = f"{told}/?t={token}"
                print(f"  review site: {link}", flush=True)
                try:
                    last = open(TOLD_FILE).read().strip()
                except OSError:
                    last = ""
                if told != last:                         # a fixed address is told once, not on every start
                    daily.telegram(f"🔗 <b>Review link</b> (works on any network)\n\n{link}\n\n" + (
                        "<i>This address is fixed: bookmark it.</i>" if fixed else
                        "<i>It changes when the laptop restarts; the new one is sent here.</i>"))
                    with open(TOLD_FILE, "w") as f:
                        f.write(told)
    finally:
        try:
            os.remove(URL_FILE)
        except OSError:
            pass
        p.terminate()
    sys.exit(p.wait())


if __name__ == "__main__":
    main()
