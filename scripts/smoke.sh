#!/usr/bin/env bash
# smoke.sh — is the tool healthy? No LLM calls, no network beyond localhost.
#   ./jobpilot check
set -uo pipefail
T="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"; PY="${JOBPILOT_PYTHON:-/home/sunil/miniconda3/bin/python3}"
fail=0; ok(){ printf "  %-34s %s\n" "$1" "$2"; }; bad(){ printf "  %-34s FAIL %s\n" "$1" "$2"; fail=1; }
n=$("$PY" - <<'PY' 2>&1
import importlib, pkgutil, jobpilot
bad=[];n=0
for pkg in ("core","discover","rank","screen","tailor","fill","review","outreach"):
    p=importlib.import_module(f"jobpilot.{pkg}")
    for m in pkgutil.iter_modules(p.__path__):
        n+=1
        try: importlib.import_module(f"jobpilot.{pkg}.{m.name}")
        except Exception as e: bad.append(f"{pkg}.{m.name}: {e}")
print(f"{n} modules" + (f"; FAILED {bad}" if bad else ""))
PY
); case "$n" in *FAILED*) bad "imports" "$n";; *) ok "imports" "$n";; esac
p=$("$T/jobpilot" paths | grep -c ' ok$'); [ "$p" = 10 ] && ok "paths" "10/10" || bad "paths" "$p/10"
"$PY" -c "import yaml;[yaml.safe_load(open('$T/config/'+f)) for f in ('config.yaml','targets.yaml','answers.yaml','learned.yaml','boards.yaml','keyword_denylist.yaml')]" 2>/dev/null && ok "config yaml" "6 files parse" || bad "config yaml" "parse error"
d=$("$T/jobpilot" dedupe --dry-run 2>&1 | tail -1); ok "dedupe --dry-run" "$d"
s=$(ls -d "$T"/applications/*/ | grep -v '/_' | head -1 | xargs basename); b=$("$PY" -m jobpilot.tailor.apply --build "$s" 2>&1 | grep -cE 'pdf\]|ats\]'); [ "$b" = 2 ] && ok "build (pdf + docx)" "$s" || bad "build" "$s: $b/2"
pg=$(pdfinfo "$T/applications/$s/sunil_resume.pdf" 2>/dev/null | awk '/Pages/{print $2}'); [ "${pg:-0}" -le 2 ] && ok "pages" "$pg" || bad "pages" "$pg (>2)"
a=$("$PY" -m jobpilot.tailor.autotailor --limit 1 --dry-run 2>&1 | grep -c 'would scaffold'); [ "$a" -ge 0 ] && ok "autotailor --dry-run" "$a candidate(s)"
sc=$("$PY" -m jobpilot.screen.screen --dry-run 2>&1 | head -1 | sed 's/^ *//'); ok "screen --dry-run" "$sc"
dg=$("$PY" -m jobpilot.core.daily --digest-only --no-telegram --no-submit 2>&1 | grep -oE 'digest-only: [0-9]+ pending'); [ -n "$dg" ] && ok "daily --digest-only" "$dg" || bad "daily --digest-only" "no digest line"
# the fill engine on local fixture forms, headless Chrome, no network: explore/replay of one
# page (placeholders, recipes, gate, untick) and a multi-page route (Apply -> Next -> Submit,
# guard, module learned from observation)
# In parallel: each test has its own scratch dir, fixture port and headless browser.
TD=$(mktemp -d)
for t in test_replay test_walk test_session test_submit test_mismatch; do
  ( timeout 400 "$PY" "$T/tests/$t.py" > "$TD/$t.log" 2>&1 ) &
done
wait
for t in test_replay test_walk test_session test_submit test_mismatch; do
  r=$(tail -1 "$TD/$t.log"); case "$r" in "ALL PASSED") ok "tests/$t.py" "$r";; *) bad "tests/$t.py" "$r";; esac
done
rm -rf "$TD"
# a failed submit must turn its missing required fields into questions, without a browser or an LLM
fq=$("$PY" - <<'PY' 2>&1
from jobpilot.fill.browser import questions_from_missing
miss=[{"label":"Current Location*","required":True,"kind":"dropdown","options":["Australia","India"],"reason":"x"},
      {"label":"Willing to relocate","required":True,"kind":"choice","options":[],"reason":"y"},
      {"label":"Website","required":False,"kind":"text","options":[],"reason":"z"},
      {"label":"Why us?","required":True,"kind":"text","options":[],"reason":"w"}]
old=[{"qid":"q1","label":"Current Location","status":"answered","selected":"Delhi","options":[]}]
qs,added=questions_from_missing(miss,old)
assert added==3 and len(qs)==3, (added,len(qs))
assert qs[0]["status"]=="open" and qs[0]["previous"]=="Delhi" and qs[0]["options"]==["Australia","India"]
assert qs[1]["options"]==["Yes","No"] and qs[2]["kind"]=="text"
print("3 questions from 4 missing (1 optional skipped, 1 reopened)")
PY
); case "$fq" in *Error*|*assert*) bad "failed-submit -> questions" "$fq";; *) ok "failed-submit -> questions" "$fq";; esac
if systemctl --user is-active jobpilot-form.service >/dev/null 2>&1; then
  tok=$(grep FORM_TOKEN ~/.config/jobbot/env 2>/dev/null | cut -d= -f2); codes=$(for u in "/" "/keywords" "/referrals"; do curl -s -m 5 -o /dev/null -w '%{http_code} ' "http://127.0.0.1:8765$u?t=$tok"; done)
  [ "$codes" = "200 200 200 " ] && ok "form routes" "$codes" || bad "form routes" "$codes"
else ok "form service" "not running (start: ./jobpilot services)"; fi
ok "claude cli" "$("$PY" -c 'from jobpilot.tailor import autotailor;print(autotailor.claude_bin() or "MISSING")')"
[ $fail = 0 ] && echo "  ALL OK" || { echo "  PROBLEMS ABOVE"; exit 1; }
