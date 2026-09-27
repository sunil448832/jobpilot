#!/usr/bin/env python3
"""
llm_eval.py — is a cheaper model good enough for JD screening?

Re-runs screen.py's exact prompt over roles that ALREADY carry a verdict, on the
model you name, and compares. Nothing is written to state.db. The stored verdicts
came from whatever model the CLI defaulted to when they were made (Fable 5.1 for
the first 227), so "agreement" means agreement with that model, not with truth —
the disagreements are the rows worth a human look, and they are listed in full.

    ./jobpilot llm-eval haiku            # one model
    ./jobpilot llm-eval haiku sonnet     # several, same batches
    ./jobpilot llm-eval haiku --limit 60 # first 60 labelled roles only

Results: data/llm_eval/<model>-<timestamp>.json plus a printed report.
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time

from jobpilot.core.paths import DATA, TOOL  # noqa: E402
from jobpilot.screen import screen  # noqa: E402
from jobpilot.tailor import autotailor  # noqa: E402

DB = os.path.join(DATA, "state.db")
OUT = os.path.join(DATA, "llm_eval")


def labelled(limit=None):
    con = sqlite3.connect(DB)
    q = ("SELECT key, company, title, location, market, score, url, jd, screen FROM jobs "
         "WHERE screen IS NOT NULL AND screen NOT LIKE '%market removed%' "
         "AND url != '' AND jd IS NOT NULL AND LENGTH(jd) > 200 "
         "ORDER BY score DESC")
    rows = con.execute(q).fetchall()
    # one row per company+title, like shortlist() does
    seen, out = set(), []
    for r in rows:
        k = (r[1], (r[2] or "").strip().lower()[:60])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out[:limit] if limit else out


def run_model(model, batch, effort="low"):
    prompt = screen.build_prompt([r[:8] for r in batch])
    cli = autotailor.claude_bin()
    # the screen agent's own settings (src/agents/screen/tools.yaml), with this model and effort
    from jobpilot.core import agents
    ag = agents.get("screen")
    cmd = [cli, "-p", prompt, "--model", model, "--effort", effort,
           "--max-turns", str(ag.spec.get("max_turns") or 1), "--output-format", "text"]
    auto = os.path.join(DATA, ".auto"); os.makedirs(auto, exist_ok=True)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=auto, capture_output=True, text=True, timeout=900)
    secs = time.time() - t0
    verdicts = screen.parse_verdicts(p.stdout or "", len(batch)) or {}
    return verdicts, secs, len(prompt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=30)
    ap.add_argument("--effort", default="low")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    os.makedirs(OUT, exist_ok=True)

    rows = labelled(a.limit)
    batches = [rows[i:i + a.batch] for i in range(0, len(rows), a.batch)]
    n_keep = sum(1 for r in rows if r[8].startswith("keep"))
    print(f"  {len(rows)} labelled roles ({n_keep} keep / {len(rows) - n_keep} reject) "
          f"in {len(batches)} batches of {a.batch}")

    stamp = dt.datetime.now().strftime("%m%d-%H%M")
    for model in a.models:
        print(f"\n  ===== {model} (effort {a.effort}) =====")
        results, tot_secs, tot_chars = [], 0.0, 0
        for bi, batch in enumerate(batches, 1):
            verdicts, secs, chars = run_model(model, batch, a.effort)
            tot_secs += secs; tot_chars += chars
            got = sum(1 for i in range(1, len(batch) + 1) if i in verdicts)
            print(f"    batch {bi}/{len(batches)}: {got}/{len(batch)} verdicts in {secs:.0f}s")
            for i, r in enumerate(batch, 1):
                stored = r[8]
                v = verdicts.get(i)
                results.append({
                    "key": r[0], "company": r[1], "title": r[2], "market": r[4],
                    "score": r[5], "stored": stored,
                    "stored_verdict": "keep" if stored.startswith("keep") else "reject",
                    "model_verdict": (v[0] if v else None),
                    "model_reason": (v[1] if v else None),
                    "model_fit": (v[2] if v else None),
                })
        # ---- report
        scored = [x for x in results if x["model_verdict"] in ("keep", "reject")]
        agree = [x for x in scored if x["model_verdict"] == x["stored_verdict"]]
        false_keep = [x for x in scored if x["stored_verdict"] == "reject" and x["model_verdict"] == "keep"]
        false_rej = [x for x in scored if x["stored_verdict"] == "keep" and x["model_verdict"] == "reject"]
        unparsed = len(results) - len(scored)
        print(f"\n  {model}: {len(agree)}/{len(scored)} agree ({100 * len(agree) / max(1, len(scored)):.0f}%), "
              f"{unparsed} unparsed; wall {tot_secs:.0f}s; ~{tot_chars // 4:,} prompt tokens")
        print(f"    would KEEP what Fable rejected  : {len(false_keep):3d}  (each costs a wasted tailoring session)")
        print(f"    would REJECT what Fable kept    : {len(false_rej):3d}  (each is a silently lost role)")
        if false_rej:
            print("\n    REJECTED by this model, kept by Fable — check these:")
            for x in sorted(false_rej, key=lambda x: -x["score"]):
                print(f"     {x['score']:5.1f} {x['company'][:12]:<13} {x['title'][:34]:<35} {x['market']:<9}")
                print(f"           model : {x['model_reason']}")
                print(f"           fable : {x['stored'][6:][:80]}")
        if false_keep:
            print("\n    KEPT by this model, rejected by Fable:")
            for x in sorted(false_keep, key=lambda x: -x["score"]):
                print(f"     {x['score']:5.1f} {x['company'][:12]:<13} {x['title'][:34]:<35} {x['market']:<9}")
                print(f"           model : {x['model_reason']}")
                print(f"           fable : {x['stored'][8:][:80]}")
        path = os.path.join(OUT, f"{model}-{stamp}.json")
        json.dump({"model": model, "effort": a.effort, "n": len(results),
                   "agree": len(agree), "false_keep": len(false_keep),
                   "false_reject": len(false_rej), "unparsed": unparsed,
                   "wall_s": tot_secs, "prompt_tokens_est": tot_chars // 4,
                   "rows": results}, open(path, "w"), indent=1)
        print(f"\n    saved {os.path.relpath(path, TOOL)}")


if __name__ == "__main__":
    main()
