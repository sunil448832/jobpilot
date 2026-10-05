#!/usr/bin/env bash
# Plugged in, closing the lid does not suspend the laptop, so the review site, Tailscale
# and the scans stay reachable. On battery nothing changes: the lid suspends as before.
# A user-level inhibitor lock (systemd-inhibit), held only while on AC — no sudo, no
# system setting changed. Run by jobpilot-lid.service; stop it to undo.
set -u
ac() { cat /sys/class/power_supply/A{C,DP}*/online 2>/dev/null | grep -q 1; }
while true; do
  if ac; then
    # hold the lock until the charger is pulled
    systemd-inhibit --what=handle-lid-switch --who=jobpilot --mode=block \
      --why="plugged in: keep the review site and scans reachable" \
      bash -c 'while cat /sys/class/power_supply/A{C,DP}*/online 2>/dev/null | grep -q 1; do sleep 15; done'
  else
    sleep 15
  fi
done
