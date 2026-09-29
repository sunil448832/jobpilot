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
from concurrent.futures import ThreadPoolExecutor

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)
from jobpilot.core.config import cfg  # noqa: E402
DB = os.path.join(DATA, "state.db")

# The screening prompt and tools: src/agents/screen/.


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


def build_prompt(rows):
    from jobpilot.core import agents
    from jobpilot.tailor import tex2md
    from jobpilot.tailor.autotailor import policy_sections
    # the whole base resume (rebuilt from the .tex when it changes) and the markets policy
    resume = open(tex2md.base_md(), encoding="utf-8").read().split("-->", 1)[-1].strip()
    # his facts come from answers.yaml and config.yaml, never from the prompt text
    from jobpilot.core.answers import load
    a = load("answers.yaml")
    years = int(str(a["experience_summary"]["total_years"]).strip("+ "))
    parts = [agents.get("screen").render(
        resume=resume, policy=policy_sections([4]), name=a["personal"]["full_name"],
        citizenship=a["work_authorization"]["citizenship"],
        country=a["work_authorization"]["current_work_country"],
        years=years, max_years=years + int(cfg("screen.years_margin", 2)))]
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


def _ask(cli, chunk, auto):
    """One claude -p call over one chunk of rows -> {row_index_in_chunk: verdict}."""
    from jobpilot.tailor import autotailor
    from jobpilot.core import agents
    ag = agents.get("screen")
    p = subprocess.run(ag.argv(cli, build_prompt(chunk), tracking=TRACKING),
                       cwd=auto, capture_output=True, text=True, timeout=ag.timeout(900))
    v = parse_verdicts(p.stdout or "", len(chunk))
    return v, (p.stdout or p.stderr)[-300:]


def screen_round(rows, con):
    """One round = one shortlist draw. The Claude work is split into chunks of
    screen.chunk roles and the chunks run CONCURRENTLY (screen.workers): three
    calls of 10 finish in about the time of one call of 10, not one of 30. Each
    chunk repeats the short preamble, which is cheap; the DB writes stay on this
    thread, in order, so nothing about verdict handling changes."""
    cli = claude_bin()
    if not cli:
        print("    claude CLI not found")
        return 0
    auto = os.path.join(DATA, ".auto")
    os.makedirs(auto, exist_ok=True)
    chunk_n = max(1, int(cfg("screen.chunk", 10)))
    workers = max(1, int(cfg("screen.workers", 3)))
    chunks = [rows[i:i + chunk_n] for i in range(0, len(rows), chunk_n)]
    print(f"    {len(rows)} roles as {len(chunks)} chunk(s) of <= {chunk_n}, {min(workers, len(chunks))} concurrent")
    verdicts = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda c: _ask(cli, c, auto), chunks))
    offset = 0
    for chunk, (v, tail) in zip(chunks, results):
        if not v:
            print(f"    chunk of {len(chunk)}: could not parse verdicts; raw tail:\n{tail}")
        else:
            for i, vv in v.items():
                verdicts[offset + i] = vv
        offset += len(chunk)
    if not verdicts:
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
