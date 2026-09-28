#!/usr/bin/env bash
# tests/agentic_batch.sh <slug> ... — explore_agentic on each application in turn (one Chrome
# profile: one at a time). Log per application: data/agentic-<slug>.log
cd "$(dirname "$0")/.."
for slug in "$@"; do
  echo "=== $slug  $(date +%H:%M:%S)"
  PYTHONUNBUFFERED=1 timeout 1800 python3 -m jobpilot.apply.explore_agentic.session "$slug" --model=opus --effort=low > "data/agentic-$slug.log" 2>&1
  echo "=== $slug exit $?  $(date +%H:%M:%S)"
  grep "DONE:\|placeholder:\|the posting is closed\|Traceback" "data/agentic-$slug.log" | cut -c1-400
done
echo "=== ALL DONE"
