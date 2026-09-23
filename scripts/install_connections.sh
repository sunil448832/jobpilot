#!/usr/bin/env bash
# Find the LinkedIn Connections export in ~/Downloads and install it.
# Handles the zip or a bare Connections.csv, and verifies it parsed.
set -uo pipefail
TOOL="$(cd "$(dirname "$0")/.." && pwd)"
# data/ is where every machine-written file lives; referrals.py reads it from there.
DEST="$TOOL/data/connections.csv"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

FOUND=""
CSV="$(find ~/Downloads -maxdepth 2 -iname 'Connections.csv' -newermt '-30 days' 2>/dev/null | head -1)"
if [ -n "$CSV" ]; then
  FOUND="$CSV"
else
  for z in $(find ~/Downloads -maxdepth 2 \( -iname '*linkedin*.zip' -o -iname 'Basic_LinkedInDataExport*.zip' -o -iname 'Complete_LinkedInDataExport*.zip' \) -newermt '-30 days' 2>/dev/null); do
    unzip -o -q "$z" -d "$TMP" 2>/dev/null || continue
    C="$(find "$TMP" -iname 'Connections.csv' | head -1)"
    [ -n "$C" ] && { FOUND="$C"; break; }
  done
fi

if [ -z "$FOUND" ]; then
  echo "  No Connections export found in ~/Downloads."
  echo "  LinkedIn -> Settings & Privacy -> Data privacy -> Get a copy of your data"
  echo "  -> tick 'Connections' only -> Request archive (~10 min by email)"
  exit 1
fi

cp "$FOUND" "$DEST"
chmod 600 "$DEST"
echo "  installed: $FOUND"
echo "         -> $DEST"
python3 "$JOBS/referrals.py" --stats 2>&1 | head -12
