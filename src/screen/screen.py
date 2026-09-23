#!/usr/bin/env python3
"""
screen.py — Claude reviews the shortlist and throws out the false positives.

Keyword scoring cannot tell that a role which mentions RAG, Python and AWS is
still useless because it says "must already hold UK work authorization", or is a
manager posting dressed as an engineering one, or wants 10 years. Those survive
every regex and waste a tailoring session each.

So before anything gets tailored:

  1. GROUP BY COMPANY, keep that company's top 3 by score. One employer with 24
     open reqs should not own the shortlist.
  2. Take the top 30 overall. Note the per-company cap, not this number, is what
     usually binds: a round can return at most 3 x (companies holding an
     unscreened role), which collapsed to 9 by round 2 on a pool that was 81/88
     OpenAI and Anthropic. In --loop mode a short round therefore lowers the
     floor and re-draws instead of screening a stub batch.
  3. Send all 30 to `claude -p` in ONE call with the sponsorship-relevant and
     requirement-relevant parts of each JD, and get back keep/reject + a reason.
  4. Rejected roles are marked in state.db and never reach autotailor.py.

One Claude call for 30 jobs is far cheaper than tailoring even one role that was
never viable.

Usage:
    python jobs/screen.py                # screen the current shortlist
    python jobs/screen.py --top 30 --per-company 3
    python jobs/screen.py --dry-run      # show what would be sent
    python jobs/screen.py --loop --want-market non-usa --want 10
                                         # keep going until 10 non-US are usable;
                                         # non-US roles are drawn first, and the
                                         # floor relaxes to --floor-min to find them
    python jobs/screen.py --report       # verdicts already recorded
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
from jobpilot.core.config import cfg  # noqa: E402
DB = os.path.join(DATA, "state.db")

PROMPT_HEAD = """You are screening a job shortlist for Sunil Kumar Sharma before any
effort is spent tailoring a resume. Read jobs/POLICY.md first — section 1 for what
is true about him, section 4 for his markets and sponsorship situation.

The short version: Indian citizen, resident in India, ~5 years experience (3 in
GenAI/agentic/LLM), ex-Amazon Applied Scientist, M.Tech AI from IIT Jodhpur. He
NEEDS visa sponsorship to work outside India, or a fully-remote role that hires
from India. He does NOT have Apache Spark, Azure, GCP, Databricks platform,
JavaScript/TypeScript, or team management.

What he HAS, verbatim from the current resume's Skills section:
{skills}
Plus, from Experience/Projects: A2A multi-agent orchestrator + sandboxed
data-engineering agents (CrewAI/PydanticAI, bubblewrap, DuckDB streaming); an
agent evaluation service (28 graded tasks, F1 grader, LLM root-cause); vLLM
serving (Phi-3 Vision, Llama-3 FP8), Qdrant hybrid retrieval compressed 8x with
TurboQuant; RL post-training from scratch (GRPO + verifiable rewards, QLoRA on
Qwen3-4B, colocated vLLM rollouts); LLaVA LoRA / BLIP fine-tuning; FLAVA
multimodal classifier at Amazon; ArcFace face recognition.

Calibration, learned from a 193-role comparison against a stronger model:
  - "6+", "7+" or "8+ years" with his 5 is CREDIBLE — keep. Reject on years only
    at 10+ or at principal/staff scope. JD year counts are aspirational.
  - Silent on sponsorship, or simply "San Francisco" / "New York" with no
    "must be authorized" language, is NOT a reject. Silence is not a no.
  - "Large-scale LLM training" wanted: he has done RL post-training and LoRA
    fine-tuning on 4B-7B models and distributed training (DeepSpeed ZeRO-3) —
    adjacent, keep unless the JD is explicitly pretraining-at-scale only.
  - A Data Scientist title is a reject only if the work is pure product/BI
    analytics; DS roles on LLM evals, ML modelling or experimentation are keeps.

Below are candidate roles. For EACH one decide keep or reject.

REJECT when:
  - the JD says it will not sponsor, or requires existing work authorization in a
    country where he has none ("must be authorized to work in the US")
  - it is remote but locked to a country he cannot work from ("Remote - US only",
    "must reside in the UK")
  - it is not really an IC ML/AI engineering role — a manager, sales, recruiting,
    pure data-analyst or pure data-engineering posting
  - the core of the job is something he does not have (a Spark/Databricks
    platform role, a frontend role, an infra-only SRE role)
  - it demands substantially more experience than 5 years (10+, or "principal"
    scope) such that applying is not credible
  - it is an internship, new-grad, or contract-to-hire position

KEEP when it is a genuine IC ML/AI/data-science/forward-deployed role he could
plausibly do, AND either sponsorship is possible or it is remote-from-India
eligible. When the JD is silent on sponsorship, KEEP it — silence is not a no.

ALSO give each role a FIT score, 0-100: how well Sunil's actual profile (the
skills and experience above) matches what the role really requires — the way a
hiring manager reads it, not a keyword counter:
  - Weigh the must-haves, including ABSTRACT ones ("embedded in the research
    community", "publications", "fintech domain") and not only tool names.
  - A requirement phrased "at least one of A, B, C" is fully satisfied if he has
    any one of them. Missing nice-to-haves cost little; missing must-haves cost a lot.
  - 80+ strong match; 60-79 plausible, worth tailoring; 40-59 a stretch;
    below 40 do not bother. Keep/reject is about eligibility; fit is about match —
    a role can be keep with fit 35.

Reply with ONLY a JSON array, no prose, no markdown fence:
[{"id": "<the id given>", "verdict": "keep"|"reject", "fit": <0-100>, "reason": "<12 words max>"}]

Roles:
"""


def shortlist(top=30, per_company=3, floor=50.0, unscreened_only=True, market=None):
    con = sqlite3.connect(DB)
    q = ("SELECT key, company, title, location, market, score, url, jd FROM jobs "
         "WHERE status='new' AND score >= ? AND url != '' ")
    args = [floor]
    if unscreened_only:
        q += "AND screen IS NULL "     # so a loop moves DOWN the pool
    # When looping FOR a market, sort that market to the front. Without this the
    # global score order handed all 3 rounds to OpenAI and Anthropic US roles
    # while every unscreened UAE posting sat untouched — the loop burned its
    # rounds on exactly the market it was not counting.
    if market == "non-usa":
        q += "ORDER BY (market != 'usa') DESC, score DESC"
    elif market:
        q += "ORDER BY (market = ?) DESC, score DESC"
        args.append(market)
    else:
        q += "ORDER BY score DESC"
    rows = con.execute(q, args).fetchall()
    # The same role posted in several cities arrives as several rows (Databricks
    # has one FDE req 7 times). Keep the best-market copy so a shortlist slot is
    # not spent screening the same JD repeatedly.
    seen, picked, roles = {}, [], set()
    for r in rows:
        co, title = r[1], (r[2] or "").strip().lower()[:60]
        if (co, title) in roles:
            continue
        roles.add((co, title))
        if seen.get(co, 0) >= per_company:
            continue
        seen[co] = seen.get(co, 0) + 1
        picked.append(r)
        if len(picked) >= top:
            break
    return picked


VISA_RX = re.compile(
    r"[^.\n]{0,160}(sponsor\w*|visa|work authoriz\w*|work permit|right to work|"
    r"eligible to work|must reside|based in|relocat\w*|citizen|clearance)"
    r"[^.\n]{0,160}", re.I)
REQ_RX = re.compile(
    r"[^.\n]{0,140}(\d+\+?\s*years|experience (with|in)|proficien|expertise|"
    r"required|must have|you have)[^.\n]{0,140}", re.I)


def digest_jd(jd, limit=1100):
    """The parts a screener actually needs: eligibility + requirements."""
    from jobpilot.rank import keywords as KW
    t = KW.normalize(jd or "")
    visa = [m.group(0).strip() for m in VISA_RX.finditer(t)][:4]
    reqs = [m.group(0).strip() for m in REQ_RX.finditer(t)][:6]
    out = ""
    if visa:
        out += "ELIGIBILITY: " + " | ".join(visa) + "\n"
    if reqs:
        out += "REQUIREMENTS: " + " | ".join(reqs) + "\n"
    if not out:
        out = (t[:600] + "\n")
    return out[:limit]


def skills_text():
    """The resume's Skills section as plain text, so the screener judges against
    what is actually on the resume today rather than a summary that drifts."""
    import glob, re
    try:
        t = open(os.path.join(RESUME, "sections", "skills.tex")).read()
    except OSError:
        return "(skills.tex not found)"
    t = re.sub(r"(?m)^\s*%.*$", "", t)                       # comments
    t = re.sub(r"\\skills\{([^}]*)\}", r"\1", t)           # \skills{Label:} -> Label:
    t = re.sub(r"\\(par|vspace\{[^}]*\}|noindent|textbf|emph)", "", t)
    t = re.sub(r"[{}]", "", t)
    t = t.replace("\\&", "&")
    keep = [l.strip() for l in t.splitlines()
            if l.strip() and not l.strip().startswith(("\\documentclass", "\\usepackage",
                                                      "\\begin", "\\end"))]
    return "\n".join("  " + l for l in keep)


def build_prompt(rows):
    parts = [PROMPT_HEAD.replace("{skills}", skills_text())]
    for i, (key, co, title, loc, market, score, url, jd) in enumerate(rows, 1):
        parts.append(
            f"\n--- id: {i} ---\n"
            f"{title} @ {co}\n"
            f"location: {loc}   (classified market: {market}, score {score})\n"
            f"{digest_jd(jd)}")
    return "".join(parts)


def claude_bin():
    from jobpilot.tailor import autotailor
    return autotailor.claude_bin()


def parse_verdicts(text, n):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out = {}
    for d in data:
        try:
            i = int(str(d.get("id")).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= i <= n:
            try:
                fit = max(0, min(100, int(float(d.get("fit")))))
            except (TypeError, ValueError):
                fit = None
            out[i] = (str(d.get("verdict", "")).lower(), str(d.get("reason", ""))[:90], fit)
    return out


def count_usable(market=None):
    con = sqlite3.connect(DB)
    q = "SELECT COUNT(*) FROM jobs WHERE screen LIKE 'keep%' AND status='new'"
    args = []
    if market == "non-usa":
        q += " AND market != 'usa'"
    elif market:
        q += " AND market = ?"
        args.append(market)
    return con.execute(q, args).fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=cfg("screen.top", 30))
    ap.add_argument("--loop", action="store_true",
                    help="keep screening deeper batches until --want are usable")
    ap.add_argument("--want", type=int, default=cfg("screen.want_usable", 10))
    ap.add_argument("--max-rounds", type=int, default=cfg("screen.max_rounds", 3))
    ap.add_argument("--want-market", default=cfg("screen.want_market", ""),
                    help="count only this market toward --want, e.g. non-usa")
    ap.add_argument("--per-company", type=int, default=cfg("screen.per_company", 3))
    ap.add_argument("--floor", type=float, default=cfg("screen.floor", 50.0))
    ap.add_argument("--floor-step", type=float, default=cfg("screen.floor_step", 5.0),
                    help="when looping, drop the floor by this to fill a short round")
    ap.add_argument("--floor-min", type=float, default=cfg("screen.floor_min", 40.0))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--include-queued", action="store_true",
                    help="with --backfill: also score roles already queued (verdict kept as is)")
    ap.add_argument("--backfill", action="store_true",
                    help="score fit for already-kept roles that have none")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    con = sqlite3.connect(DB)
    for ddl in ("ALTER TABLE jobs ADD COLUMN screen TEXT", "ALTER TABLE jobs ADD COLUMN fit INTEGER"):
        try:
            con.execute(ddl); con.commit()
        except sqlite3.OperationalError:
            pass

    if a.backfill:
        # Fit scores for keeps screened before the score existed. Same prompt,
        # same screener; verdicts may be refreshed too.
        statuses = "('new','queued')" if a.include_queued else "('new')"
        rows = con.execute("SELECT key, company, title, location, market, score, url, jd FROM jobs "
                           f"WHERE status IN {statuses} AND screen LIKE 'keep%' AND fit IS NULL AND url != '' "
                           "ORDER BY score DESC").fetchall()
        print(f"  backfill: {len(rows)} kept roles without a fit score")
        for i in range(0, len(rows), a.top):
            batch = rows[i:i + a.top]
            print(f"  batch {i // a.top + 1}: {len(batch)} roles")
            screen_round(batch, con)
        return

    if a.report:
        for st, n in con.execute("SELECT status, COUNT(*) FROM jobs "
                                 "WHERE screen IS NOT NULL GROUP BY status"):
            print(f"  {st:<12} {n}")
        print("  fit distribution of kept roles (status new):")
        for lo, hi in ((80, 101), (60, 80), (40, 60), (0, 40)):
            n = con.execute("SELECT COUNT(*) FROM jobs WHERE status='new' AND screen LIKE 'keep%' "
                            "AND fit >= ? AND fit < ?", (lo, hi)).fetchone()[0]
            print(f"    fit {lo:>2}-{hi - 1:<3} {n}")
        print()
        for co, t, s_, sc in con.execute(
                "SELECT company,title,screen,score FROM jobs WHERE screen LIKE 'reject%' "
                "ORDER BY score DESC LIMIT 20"):
            print(f"  {sc:5.1f}  {co[:14]:<16} {t[:38]:<40} {s_[7:]}")
        return

    rounds = a.max_rounds if a.loop else 1
    want_m = a.want_market or None
    floor, step, fmin = a.floor, a.floor_step, a.floor_min
    for rnd in range(1, rounds + 1):
        have = count_usable(want_m)
        # `have >= want` must not gate the FIRST round. count_usable() counts the
        # accumulated backlog, so once it passed --want the loop printed "101
        # usable already — stopping" and screened nothing, every run, forever.
        # Combined with pipeline.require_screen that permanently locked newly
        # sourced roles out of tailoring: a fresh intake put OpenAI Applied AI
        # Engineer (Dublin, 94.4) top of the ranking and it could never be
        # tailored, because nothing would ever give it a verdict. New arrivals
        # always get one round; --want governs whether to go DEEPER than that.
        if a.loop and rnd > 1 and have >= a.want:
            print(f"  {have} usable already"
                  + (f" in {want_m}" if want_m else "") + " — stopping")
            break
        rows = shortlist(a.top, a.per_company, floor, market=want_m)
        # A round is capped at 3 x (companies still holding an unscreened role),
        # so once the top 3 of every company are done the pool collapses — rounds
        # 2 and 3 returned 9 roles each, not 30, and the loop "finished" with the
        # deeper pool untouched. If the round cannot fill its slots, lower the
        # floor and look again rather than spending a round on a stub batch.
        while a.loop and len(rows) < a.top and round(floor - step, 2) >= fmin:
            floor = round(floor - step, 2)
            rows = shortlist(a.top, a.per_company, floor, market=want_m)
        if not rows:
            print(f"  no unscreened roles left at or above floor {fmin}")
            break
        label = f"round {rnd}/{rounds}: " if a.loop else ""
        print(f"  {label}{len(rows)} roles "
              f"(top {a.per_company}/company, floor {floor}"
              + (f", {want_m} first" if want_m else "")
              + f"; {have} usable so far)")
        if a.dry_run:
            print(build_prompt(rows)[:1200])
            break
        n = screen_round(rows, con)
        if n == 0:
            print("  no verdicts parsed — stopping")
            break
    if a.loop:
        print(f"\n  final: {count_usable(want_m)} usable"
              + (f" in {want_m}" if want_m else ""))
    return


def screen_round(rows, con):
    prompt = build_prompt(rows)
    print(f"    prompt: {len(prompt)} chars")

    cli = claude_bin()
    if not cli:
        print("    claude CLI not found")
        return 0
    auto = os.path.join(DATA, ".auto")
    os.makedirs(auto, exist_ok=True)
    prompt = prompt.replace("jobs/POLICY.md", POLICY)   # it lives in the tool now
    from jobpilot.tailor import autotailor
    p = subprocess.run([cli, "-p", prompt, *autotailor.llm_flags("screen"),
                        "--add-dir", TRACKING, "--add-dir", TOOL,
                        "--allowedTools", "Read", "--output-format", "text"],
                       cwd=auto, capture_output=True, text=True, timeout=900)
    verdicts = parse_verdicts(p.stdout or "", len(rows))
    if not verdicts:
        print(f"    could not parse verdicts; raw tail:\n{(p.stdout or p.stderr)[-300:]}")
        return 0

    kept = rejected = 0
    for i, (key, co, title, loc, market, score, url, jd) in enumerate(rows, 1):
        v = verdicts.get(i)
        if not v:
            continue
        verdict, reason, fit = v
        cur_status = con.execute("SELECT status FROM jobs WHERE key=?", (key,)).fetchone()
        if cur_status and cur_status[0] == "queued":
            # Already tailored and in Sunil's queue: record the fit, leave the verdict.
            con.execute("UPDATE jobs SET fit=COALESCE(?, fit) WHERE key=?", (fit, key))
            kept += 1
            continue
        if verdict.startswith("reject"):
            # "Demands 8+ years" is true of the role in New York AND in San
            # Francisco. Propagate to every location copy, or the sibling row
            # survives and gets tailored anyway.
            cur = con.execute(
                "UPDATE jobs SET status='rejected', screen=?, fit=COALESCE(?, fit) "
                "WHERE company=? AND lower(title)=lower(?)",
                (f"reject: {reason}", fit, co, title))
            rejected += cur.rowcount
            print(f"  ✗ {score:5.1f} fit {fit if fit is not None else '?':>3} {co[:14]:<16} {title[:34]:<36} {reason}")
        else:
            con.execute("UPDATE jobs SET screen=?, fit=COALESCE(?, fit) WHERE company=? AND lower(title)=lower(?)",
                        (f"keep: {reason}", fit, co, title))
            kept += 1
    con.commit()
    print(f"    kept {kept}, rejected {rejected} of {len(rows)}")
    return kept + rejected


if __name__ == "__main__":
    main()
