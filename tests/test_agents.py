"""tests/test_agents.py — the agents folder (src/agents) and the facts the form agent sees.

  1. every agent renders: no unfilled param; tools and flags resolve
  2. facts for one job: the answers file flattened, only this market's salary, no
     secrets, no "not written yet" markers (PER_COMPANY), the resume by name only
"""
import os
import sys

from jobpilot.core import agents
from jobpilot.core.paths import TRACKING
from jobpilot.core import answers as autofill
from jobpilot.apply.explore_agentic import facts

fails = []


def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c:
        fails.append(m)


print("1. agents render")
CASES = {
    "screen": dict(resume="R", policy="P", name="N", citizenship="C", country="K", years=5, max_years=7),
    "tailor": dict(appdir="/a", report="r", policy="p", jd="j", tracking=TRACKING),
    "draft_answers": dict(appdir="/a", qfile="/tmp/q.json", resume="/a/ats.md", answers_file="/c/answers.yaml",
                          policy="p", jd="j", tracking=TRACKING),
}
names = sorted(d for d in os.listdir(agents.ROOT) if os.path.isdir(os.path.join(agents.ROOT, d)))
check(names == sorted(CASES), f"one folder per agent: {names}")
for name, kw in CASES.items():
    ag = agents.get(name)
    p = ag.render(**kw)
    argv = ag.argv("claude", p, **kw)
    tools_ok = ("--allowedTools" not in argv) if not ag.spec.get("allowed_tools") else \
        ("{" not in argv[argv.index("--allowedTools") + 1])
    check(not any("{%s}" % x in p for x in ag.spec.get("params", [])) and tools_ok, f"{name}: renders, tools resolve")
try:
    agents.get("screen").render(resume="R")
    check(False, "a missing param is refused")
except KeyError:
    check(True, "a missing param is refused")

print("2. facts for one job")
answers = autofill.load("answers.yaml")
f = facts.job_facts(answers, {"market": "netherlands", "company": "Acme", "location": "Amsterdam"}, [],
                    resume="/x/sunil_resume.docx")
check(f.get("personal.first_name") and f.get("job.company") == "Acme" and f.get("job.market") == "netherlands",
      "the answers file flattened, plus the job itself")
check(any(k.startswith("compensation.by_market.netherlands.") for k in f)
      and not any(k.startswith("compensation.by_market.usa.") for k in f), "only this market's salary")
check(not any(v in ("PER_COMPANY", "TODO") for v in f.values()), "no 'not written yet' markers")
check(not any(s in k.lower() for k in f for s in ("password", "token", "secret")), "no secrets")
shown = facts.for_prompt(f)
check("file:resume: (the resume file)" in shown and "/x/sunil_resume.docx" not in shown, "the resume by name, not its path")

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
