#!/usr/bin/env bash
# smoke.sh — is the tool healthy? No LLM calls, no network beyond localhost.
#   ./jobpilot check
set -uo pipefail
T="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"; PY="${JOBPILOT_PYTHON:-/home/sunil/softwares/miniconda3/bin/python3}"
fail=0; ok(){ printf "  %-34s %s\n" "$1" "$2"; }; bad(){ printf "  %-34s FAIL %s\n" "$1" "$2"; fail=1; }
n=$("$PY" - <<'PY' 2>&1
import importlib, pkgutil, jobpilot
bad=[];n=0
for pkg in ("core","discover","rank","screen","tailor","apply","apply.platforms","apply.explore_agentic","apply.draft","review","outreach"):
    p=importlib.import_module(f"jobpilot.{pkg}")
    for m in pkgutil.iter_modules(p.__path__):
        n+=1
        try: importlib.import_module(f"jobpilot.{pkg}.{m.name}")
        except Exception as e: bad.append(f"{pkg}.{m.name}: {e}")
print(f"{n} modules" + (f"; FAILED {bad}" if bad else ""))
PY
); case "$n" in *FAILED*) bad "imports" "$n";; *) ok "imports" "$n";; esac
p=$("$T/jobpilot" paths | grep -c ' ok$'); [ "$p" = 10 ] && ok "paths" "10/10" || bad "paths" "$p/10"
"$PY" -c "import yaml;[yaml.safe_load(open('$T/config/'+f)) for f in ('config.yaml','targets.yaml','answers.yaml','boards.yaml','keyword_denylist.yaml')]" 2>/dev/null && ok "config yaml" "5 files parse" || bad "config yaml" "parse error"
d=$("$T/jobpilot" dedupe --dry-run 2>&1 | tail -1); ok "dedupe --dry-run" "$d"
s=$(ls -d "$T"/applications/*/ | grep -v '/_' | head -1 | xargs basename); b=$("$PY" -m jobpilot.tailor.scaffold --build "$s" 2>&1 | grep -cE 'pdf\]|ats\]'); [ "$b" = 2 ] && ok "build (pdf + docx)" "$s" || bad "build" "$s: $b/2"
pg=$(pdfinfo "$T/applications/$s/sunil_resume.pdf" 2>/dev/null | awk '/Pages/{print $2}'); [ "${pg:-0}" -le 2 ] && ok "pages" "$pg" || bad "pages" "$pg (>2)"
a=$("$PY" -m jobpilot.tailor.autotailor --limit 1 --dry-run 2>&1 | grep -c 'would scaffold'); [ "$a" -ge 0 ] && ok "autotailor --dry-run" "$a candidate(s)"
sc=$("$PY" -m jobpilot.screen.screen --dry-run 2>&1 | head -1 | sed 's/^ *//'); ok "screen --dry-run" "$sc"
dg=$("$PY" -m jobpilot.core.daily --digest-only --no-telegram --no-submit 2>&1 | grep -oE 'digest-only: [0-9]+ pending'); [ -n "$dg" ] && ok "daily --digest-only" "$dg" || bad "daily --digest-only" "no digest line"
# the agents folder and the job facts the form agent sees.
TD=$(mktemp -d)
for t in test_agents; do
  ( timeout 400 "$PY" "$T/tests/$t.py" > "$TD/$t.log" 2>&1 ) &
done
wait
for t in test_agents; do
  r=$(tail -1 "$TD/$t.log"); case "$r" in "ALL PASSED") ok "tests/$t.py" "$r";; *) bad "tests/$t.py" "$r";; esac
done
rm -rf "$TD"
if systemctl --user is-active jobpilot-form.service >/dev/null 2>&1; then
  tok=$(grep FORM_TOKEN "$T/.env" 2>/dev/null | cut -d= -f2); codes=$(for u in "/" "/keywords" "/referrals"; do curl -s -m 5 -o /dev/null -w '%{http_code} ' "http://127.0.0.1:8765$u?t=$tok"; done)
  [ "$codes" = "200 200 200 " ] && ok "form routes" "$codes" || bad "form routes" "$codes"
else ok "form service" "not running (start: ./jobpilot services)"; fi
ok "claude cli" "$("$PY" -c 'from jobpilot.tailor import autotailor;print(autotailor.claude_bin() or "MISSING")')"
[ $fail = 0 ] && echo "  ALL OK" || { echo "  PROBLEMS ABOVE"; exit 1; }
