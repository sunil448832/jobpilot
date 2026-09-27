#!/usr/bin/env python3
"""
rank.py — score and route the jobs intake.py found (Phase 4).

Cheap keyword scoring first, so only the best postings earn a full tailoring run.
Scoring mirrors targets.yaml: market priority, keyword overlap weighted toward the
recent GenAI work, explicit sponsorship language, and penalties for a years bar
well above 5.

    score = 40 x keyword fit
          + 20 x title fit
          + market priority bonus
          + boosts (sponsorship stated, fully remote, GenAI focus)
          - penalties (years required over the cap)

Usage:
    python jobs/rank.py                  # score everything marked 'new'
    python jobs/rank.py --top 10         # show the best, with why
    python jobs/rank.py --queue 3        # mark the top N ready for scaffolding
    python jobs/rank.py --show <key>
"""
import argparse
import os
import re
import sys

import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
from jobpilot.discover.intake import db, load_targets                      # noqa: E402
from jobpilot.rank import salary as sal                                      # noqa: E402
from jobpilot.rank import keywords as KW                                     # noqa: E402
from jobpilot.core.config import cfg                                    # noqa: E402
import yaml as _yaml                                      # noqa: E402

with open(os.path.join(CONFIG, "answers.yaml")) as _f:
    ANSWERS = _yaml.safe_load(_f)


def market_priority(t, market):
    for m in t["markets"]:
        if m["id"] == market:
            return m["priority"]
    return 9


def score_job(job, t, con=None):
    """Return (score, reasons[]). Deterministic and cheap — no LLM, no network."""
    title, jd, loc = job["title"] or "", job["jd"] or "", job["location"] or ""
    blob = f"{title}\n{jd}".lower()
    reasons = []

    # Synonym-aware: a JD saying "Retrieval-Augmented Generation" must match the
    # resume's "RAG", and "parameter efficient fine tuning" must match "LoRA".
    strong = t["keywords"]["strong"]
    recent = t["keywords"]["strong_recent"]
    nblob = KW.normalize(f"{title}\n{jd}")
    hit_s = [k for k in strong if KW.hit(k, nblob)]
    hit_r = [k for k in recent if KW.hit(k, nblob)]
    # The recent GenAI/agentic set is the differentiator, so it carries more.
    kw = (len(hit_s) / max(len(strong), 1)) * 0.4 + (len(hit_r) / max(len(recent), 1)) * 0.6
    # Learned bucket: promoted weekly from applied JDs (keyword_learn.py). Its own
    # small weight, so a new keyword refines ranking without diluting the two
    # hand-curated buckets calibrated against 8,804 postings.
    learned = (t["keywords"].get("learned") or []) + (t["keywords"].get("confirmed") or [])
    if learned:
        hit_l = [k for k in learned if KW.hit(k, nblob)]
        kw += (len(hit_l) / len(learned)) * cfg("keywords_learn.learned_weight", 0.1)
        if hit_l:
            reasons.append(f"learned: {', '.join(hit_l[:3])}")
    # Interest bucket: gaps Sunil ticked on the Sunday page. Surfaces roles that
    # ask for what he is learning; never evidence for a claim (see optimize.py).
    interest = t["keywords"].get("interest") or []
    if interest:
        hit_i = [k for k in interest if KW.hit(k, nblob)]
        kw += (len(hit_i) / len(interest)) * cfg("keywords_learn.interest_weight", 0.15)
        if hit_i:
            reasons.append(f"interest: {', '.join(hit_i[:3])}")
    score = kw * 40
    if hit_r:
        reasons.append(f"GenAI: {', '.join(hit_r[:4])}")
    if hit_s:
        reasons.append(f"core: {', '.join(hit_s[:4])}")

    tl = title.lower()
    if any(x.lower() in tl for x in t["titles"]["primary"]):
        score += 20
        reasons.append("primary title")
    elif any(x.lower() in tl for x in t["titles"]["also_consider"]):
        score += 10
        reasons.append("adjacent title")

    pri = market_priority(t, job["market"])
    bonus = (t["scoring"].get("market_bonus") or {1: 22, 2: 18, 3: 12, 4: 2}).get(pri, 0)
    score += bonus
    reasons.append(f"{job['market']} (priority {pri})")

    b = t["scoring"]["boost"]
    if re.search(r"sponsor\w*|visa support|relocation (package|assistance|support)", blob):
        if not re.search(r"(cannot|unable to|do not|will not|no)\s+sponsor", blob):
            score += b["sponsorship_explicit"]
            reasons.append("sponsorship mentioned")
    if re.search(r"fully remote|work from anywhere|remote.{0,12}(global|worldwide|anywhere)", blob):
        score += b["fully_remote_global"]
        reasons.append("fully remote")
    if re.search(r"\bagent|\bllm\b|generative ai|genai|\brag\b", blob):
        score += b["genai_agentic_focus"]
        reasons.append("GenAI-focused role")

    # Posted pay, in whatever currency, converted and compared to the ask.
    # Company scale — see targets.yaml for why this matters for sponsorship.
    reqs = job.get("board_size") or 0
    staff = job.get("team_size") or 0
    cs = t["scoring"].get("company_scale") or {}
    tiers = cfg("scale_tiers", {}) or {}
    tier, pts = "small", cs.get("small", 0)
    for name in ("enterprise", "large", "mid"):
        th = tiers.get(name) or {}
        if reqs >= th.get("reqs", 10**9) or staff >= th.get("staff", 10**9):
            tier, pts = name, cs.get(name, 0)
            break
    if pts:
        score += pts
        reasons.append(f"{tier} employer ({reqs} open reqs)"
                       if reqs else f"{tier} employer ({staff} staff)")

    pref = {p.lower() for p in (t["scoring"].get("preferred_list") or [])}
    if (job.get("company") or "").lower() in pref:
        score += t["scoring"].get("preferred_companies", 0)
        reasons.append("preferred employer")

    if job.get("yc"):
        score += t["scoring"]["boost"].get("yc_startup", 0)
        reasons.append(f"YC {job['yc']}")

    pay = sal.assess(jd, job["market"], ANSWERS,
                     company=job.get("company"), con=con)
    hi, want = pay.get("hi_usd"), pay.get("want_usd")
    if hi and want:
        if pay.get("posted"):
            shown = f"{pay['currency']} {pay['min']:,.0f}-{pay['max']:,.0f}"
            if hi >= want:
                score += t["scoring"]["boost"]["pay_above_ask"]
                reasons.append(f"pay {shown} = ${hi:,.0f} ≥ ask")
            elif hi < want * 0.85:
                score += t["scoring"]["penalty"]["pay_below_ask"]
                reasons.append(f"pay {shown} = ${hi:,.0f} < ask ${want:,.0f}")
            else:
                reasons.append(f"pay {shown} = ${hi:,.0f} (near ask)")
        else:
            # An ESTIMATE is an inference, so it moves the score at half weight
            # and is always labelled. Never treated as a posted figure.
            w = 0.5 if pay.get("confidence") == "medium" else 0.3
            if hi >= want:
                score += t["scoring"]["boost"]["pay_above_ask"] * w
                reasons.append(f"est. ${hi:,.0f} ≥ ask ({pay.get('confidence')})")
            elif hi < want * 0.85:
                score += t["scoring"]["penalty"]["pay_below_ask"] * w
                reasons.append(f"est. ${hi:,.0f} < ask ({pay.get('confidence')})")

    yrs = [int(y) for y in re.findall(r"(\d{1,2})\+?\s*years", blob) if int(y) <= 20]
    if yrs:
        need = max(yrs)
        if need > t["seniority"]["max_years_required_soft_cap"]:
            score += t["scoring"]["penalty"]["years_required_over_8"]
            reasons.append(f"asks {need}y (you have 5)")

    return round(min(score, 100), 1), reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--per-company", type=int, default=2,
                    help="max roles shown per company (0 = no cap)")
    ap.add_argument("--market", help="only this market, e.g. uae / netherlands")
    ap.add_argument("--queue", type=int, metavar="N")
    ap.add_argument("--all", action="store_true", help="rescore everything, not just new")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    t = load_targets()
    con = db()
    where = "" if a.all else "WHERE status='new'"
    rows = list(con.execute(
        f"SELECT key,company,title,location,market,url,jd,yc,"
        f"COALESCE(board_size,0),COALESCE(team_size,0) FROM jobs {where}"))
    if not rows:
        print("  nothing to rank — run: python jobs/intake.py")
        return

    scored = []
    for k, co, title, loc, market, url, jd, yc, bsize, tsize in rows:
        job = {"title": title, "jd": jd or "", "location": loc, "market": market,
               "yc": yc, "board_size": bsize, "team_size": tsize}
        job["company"] = co
        s, why = score_job(job, t, con)
        con.execute("UPDATE jobs SET score=?, reason=? WHERE key=?",
                    (s, "; ".join(why)[:400], k))
        scored.append((s, co, title, loc, market, url, why, k))
    con.commit()
    scored.sort(reverse=True, key=lambda x: x[0])

    floor = t["scoring"]["min_score_to_queue"]
    if a.market:
        scored = [r for r in scored if r[4] == a.market]
    # One company with nine near-identical postings should not own the shortlist.
    if a.per_company:
        per, shown = {}, []
        for r in scored:
            co = r[1]
            if per.get(co, 0) >= a.per_company:
                continue
            per[co] = per.get(co, 0) + 1
            shown.append(r)
        scored = shown
    print(f"  ranked {len(rows)} jobs · queue floor {floor} · "
          f"max {a.per_company or '∞'} per company\n")
    for s, co, title, loc, market, url, why, k in scored[:a.top]:
        flag = "▲" if s >= t["scoring"]["auto_queue_above"] else (" " if s >= floor else "·")
        print(f"  {flag} {s:5.1f}  {co[:16]:<18} {title[:44]:<46} {loc[:26]}")
        print(f"           {'; '.join(why)[:110]}")

    if a.queue:
        picked = [r for r in scored if r[0] >= floor][:a.queue]
        for s, co, title, loc, market, url, why, k in picked:
            con.execute("UPDATE jobs SET status='queued' WHERE key=?", (k,))
        con.commit()
        print(f"\n  queued {len(picked)} for tailoring:")
        for s, co, title, loc, market, url, why, k in picked:
            print(f"    {s:5.1f}  {co} — {title}\n           {url}")
        print("\n  next:  ./jobpilot apply <url>")


if __name__ == "__main__":
    main()
