#!/usr/bin/env bash
# tests/agent_poc_batch.sh <slug> ... — the one-agent POC on each application in turn
# (one Chrome profile: one at a time). Log per application: data/agent-poc-<slug>.log
cd "$(dirname "$0")/.."
for slug in "$@"; do
  echo "=== $slug  $(date +%H:%M:%S)"
  PYTHONUNBUFFERED=1 timeout 1500 python3 tests/agent_poc.py "$slug" --model=opus --effort=low > "data/agent-poc-$slug.log" 2>&1
  echo "=== $slug exit $?  $(date +%H:%M:%S)"
  grep "DONE:\|placeholder:\|the posting is closed\|Traceback" "data/agent-poc-$slug.log" | cut -c1-300
done
echo "=== ALL DONE"
