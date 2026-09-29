#!/usr/bin/env python3
"""
keyword_learn.py — let targets.yaml learn from the JDs you actually applied to.

The application history IS the counter: every applications/<slug>/JD.md is a JD
that passed screening and was worth tailoring. Once a week this mines them —
document frequency of hard-skill keywords, using the same extractor ats_score.py
already runs — and promotes the top few that targets.yaml does not yet cover.

Two rules keep it honest and free:
  * TRUTH GATE — targets.yaml is the boundary of what Sunil HAS (its own header
    says never add a keyword that is not true). A candidate is promoted only if
    optimize.is_safe() finds the same fact already on the BASE resume in other
    words. Everything else is a GAP: recorded, reported on Sunday, never added.
  * NO LLM — pure Python over files already on disk. Zero tokens.

    ./jobpilot keywords              # what the history says (report)
    ./jobpilot keywords --dry-run    # what a promotion would do, without doing it
    ./jobpilot keywords --promote    # do it (daily.py runs this on Sundays)
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

from jobpilot.core.paths import SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY, RESUME, APPLICATIONS, MEMORY  # noqa: E402,F401
from jobpilot.core.config import cfg  # noqa: E402
from jobpilot.tailor import ats_score as A  # noqa: E402
from jobpilot.rank import keywords as KW  # noqa: E402
from jobpilot.tailor import optimize  # noqa: E402

STATE = os.path.join(DATA, "keyword_learn.json")
DENY = os.path.join(CONFIG, "keyword_denylist.yaml")
GAPS = os.path.join(DATA, "keyword_gaps.md")          # machine-written, for Sunil to read
TARGETS = os.path.join(CONFIG, "targets.yaml")


def load_state():
    try:
        st = json.load(open(STATE))
    except (OSError, json.JSONDecodeError):
        st = {}
    for k, v in (("promoted", {}), ("gaps", {}), ("pending", {}), ("interest", {}),
                 ("confirmed", {}), ("dismissed", {}), ("last_promoted", None)):
        st.setdefault(k, v)
    return st


def denylist():
    import yaml
    try:
        return {str(x).lower() for x in (yaml.safe_load(open(DENY)) or [])}
    except OSError:
        return set()


def company_names():
    """Employers are not skills. 'anthropic' and 'openai' topped the first dry run
    because 40 of 67 applied JDs were theirs."""
    import sqlite3
    names = set()
    try:
        con = sqlite3.connect(os.path.join(DATA, "state.db"))
        for (c,) in con.execute("SELECT DISTINCT company FROM jobs"):
            c = (c or "").lower().strip()
            if c:
                names.add(c)
                names.add(re.sub(r"[^a-z0-9]+", "", c))
    except Exception:
        pass
    return names


def specific_enough(term, deny, companies, dismissed):
    t = term.lower().strip()
    if t in deny or t in dismissed:
        return False
    if t in companies or re.sub(r"[^a-z0-9]+", "", t) in companies:
        return False
    return True


def save_state(s):
    json.dump(s, open(STATE, "w"), indent=1)


def targets_keywords():
    import yaml
    t = yaml.safe_load(open(TARGETS))
    k = t.get("keywords", {})
    return [x for b in ("strong", "strong_recent", "learned") for x in (k.get(b) or [])]


def base_resume_norm():
    text = []
    for f in sorted(glob.glob(os.path.join(RESUME, "sections", "*.tex"))):
        text.append(open(f, encoding="utf-8").read())
    return A.normalize(A.strip_markup("\n".join(text)))


def mine():
    """{term: {'docs', 'category', 'display', 'slugs'}} over every applied JD."""
    df = {}
    files = [f for f in glob.glob(os.path.join(APPLICATIONS, "*", "JD.md"))
             if not os.path.basename(os.path.dirname(f)).startswith("_")]
    for f in files:
        slug = os.path.basename(os.path.dirname(f))
        try:
            jd = A.normalize(A.strip_markup(open(f, encoding="utf-8").read()))
        except OSError:
            continue
        for term, info in A.extract_jd_keywords(jd).items():
            if info["category"] not in ("hard", "soft"):
                continue
            d = df.setdefault(term, {"docs": 0, "category": info["category"],
                                     "display": info["display"], "slugs": []})
            d["docs"] += 1
            d["slugs"].append(slug)
    return df, len(files)


def covered(term, existing):
    """Already matched by a targets.yaml keyword (canonical or alias)?"""
    tn = KW.normalize(term)
    if term.lower() in {e.lower() for e in existing}:
        return True
    return any(KW.hit(e, tn) for e in existing)


def candidates(min_docs=3):
    df, n = mine()
    existing = targets_keywords()
    resume_norm = base_resume_norm()
    st = load_state()
    deny, companies = denylist(), company_names()
    dismissed = set(st["dismissed"]) | set(st["interest"]) | set(st["confirmed"]) | set(st["promoted"])
    out = []
    for term, d in sorted(df.items(), key=lambda kv: (-kv[1]["docs"], kv[0])):
        if d["docs"] < min_docs or covered(term, existing):
            continue
        if not optimize.worth_adding(term, d["category"]):      # hard skills only
            continue
        if not specific_enough(term, deny, companies, dismissed):
            continue
        ok, why = optimize.is_safe(term, resume_norm, resume_norm, "")
        out.append(dict(term=term, docs=d["docs"], category=d["category"],
                        safe=ok, why=why, slugs=d["slugs"]))
    return out, n


def week():
    return dt.date.today().strftime("%G-W%V")


def promote(dry_run=False):
    top_n = cfg("keywords_learn.top_n", 5)
    min_docs = cfg("keywords_learn.min_docs", 3)
    period = cfg("keywords_learn.period_days", 7)
    st = load_state()
    if st.get("last_promoted") and not dry_run:
        last = dt.date.fromisoformat(st["last_promoted"])
        if (dt.date.today() - last).days < period:
            print(f"  promoted {st['last_promoted']}; next run after {period} days")
            return [], []
    cands, n = candidates(min_docs)
    picks = cands[:top_n]
    safe = [c for c in picks if c["safe"]]
    gaps = [c for c in picks if not c["safe"]]
    print(f"  {n} applied JDs mined; {len(cands)} uncovered hard skills with >= {min_docs} JDs; top {top_n}:")
    for c in picks:
        print(f"    {c['docs']:3d} JDs  {c['term']:<28} {'SAFE -> add' if c['safe'] else 'GAP  (not on resume)'}   {c['why'][:50]}")
    if dry_run:
        return safe, gaps
    if safe:
        add_to_targets([c["term"] for c in safe], {c["term"]: c["docs"] for c in safe})
    if gaps:
        with open(GAPS, "a", encoding="utf-8") as g:
            g.write(f"\n## {week()} — asked for by the market, not on the resume\n")
            for c in gaps:
                g.write(f"- **{c['term']}** — {c['docs']} of {n} applied JDs\n")
    for c in safe:
        st["promoted"][c["term"]] = {"week": week(), "docs": c["docs"]}
    for c in gaps:
        st["gaps"][c["term"]] = {"week": week(), "docs": c["docs"]}
        st["pending"][c["term"]] = {"week": week(), "docs": c["docs"], "slugs": c["slugs"][:6]}
    st["last_promoted"] = dt.date.today().isoformat()
    save_state(st)
    return safe, gaps


def pending():
    """Gaps awaiting Sunil's tick, newest week first."""
    st = load_state()
    return sorted(({"term": t, **v} for t, v in st["pending"].items()),
                  key=lambda x: (x["week"], -x["docs"]), reverse=True)


def add_interest(terms):
    """Sunil ticked these. They go to targets.yaml `interest:` — a RANKING signal
    only. rank.py scores it; optimize.truth_vocabulary() deliberately ignores it,
    so a ticked keyword can never be written into a resume."""
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return []
    s = open(TARGETS, encoding="utf-8").read()
    if "\n  interest:" not in s:
        s = s.replace("\nseniority:", "\n  # Ticked by Sunil on the Sunday keyword page: skills the market asks for that\n"
                      "  # are NOT on the resume. Used by rank.py to surface roles (keywords_learn.\n"
                      "  # interest_weight) and NEVER as evidence — optimize.py excludes this bucket\n"
                      "  # from the truth vocabulary. Matching a job is not claiming the skill.\n"
                      "  interest: []\n\nseniority:", 1)
    st = load_state(); today = dt.date.today().isoformat()
    new = [t for t in terms if t not in st["interest"]]
    lines = "".join(f'    - "{t}"   # ticked {today}, {st["pending"].get(t, {}).get("docs", "?")} JDs — ranking only, not claimable\n' for t in new)
    if "  interest: []\n" in s:
        s = s.replace("  interest: []\n", "  interest:\n" + lines, 1)
    else:
        s = re.sub(r"(  interest:\n(?:    - .*\n)*)", lambda m: m.group(1) + lines, s, count=1)
    open(TARGETS, "w", encoding="utf-8").write(s)
    for t in new:
        st["interest"][t] = {"date": today, "docs": st["pending"].get(t, {}).get("docs")}
        st["pending"].pop(t, None)
    save_state(st)
    return new


TODO = os.path.join(DATA, "resume_todo.md")          # machine-written, for Sunil to read


def add_confirmed(terms):
    """Sunil ticked DONE: real work of his that the resume never wrote up. This is
    his own confirmation, so it counts as truth — targets.yaml `confirmed:` is
    included in optimize.py's truth vocabulary and the tailor may use the JD's
    word for it. It is also queued in data/resume_todo.md: the base resume should
    catch up, because a claim is strongest when the resume itself evidences it."""
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return []
    s = open(TARGETS, encoding="utf-8").read()
    if "\n  confirmed:" not in s:
        s = s.replace("\nseniority:", "\n  # Ticked DONE by Sunil on the keyword page: work he has actually done that the\n"
                      "  # resume does not mention yet. Counts as TRUE (optimize.py includes it in the\n"
                      "  # truth vocabulary; rank.py scores it with the learned bucket). Each one is\n"
                      "  # also listed in data/resume_todo.md until it is written into the base resume.\n"
                      "  confirmed: []\n\nseniority:", 1)
    st = load_state(); today = dt.date.today().isoformat()
    new = [t for t in terms if t not in st["confirmed"]]
    lines = "".join(f'    - "{t}"   # confirmed DONE {today}, {st["pending"].get(t, {}).get("docs", "?")} JDs — write it into the resume\n' for t in new)
    if "  confirmed: []\n" in s:
        s = s.replace("  confirmed: []\n", "  confirmed:\n" + lines, 1)
    else:
        s = re.sub(r"(  confirmed:\n(?:    - .*\n)*)", lambda m: m.group(1) + lines, s, count=1)
    open(TARGETS, "w", encoding="utf-8").write(s)
    with open(TODO, "a", encoding="utf-8") as f:
        for t in new:
            slugs = ", ".join(st["pending"].get(t, {}).get("slugs", [])[:4])
            f.write(f"- [ ] **{t}** — confirmed done {today}; asked for by {st['pending'].get(t, {}).get('docs', '?')} JDs "
                    f"(e.g. {slugs}). Add it to resume/sections/ and project-memory-backup/ with the concrete evidence.\n")
    for t in new:
        st["confirmed"][t] = {"date": today, "docs": st["pending"].get(t, {}).get("docs")}
        st["pending"].pop(t, None)
    save_state(st)
    return new


def dismiss(terms):
    st = load_state(); today = dt.date.today().isoformat()
    for t in (x.strip().lower() for x in terms if x):
        st["dismissed"][t] = today
        st["pending"].pop(t, None)
    save_state(st)


def add_to_targets(terms, docs):
    """Append to the `learned:` bucket in targets.yaml as text, so the file keeps
    its comments. Each entry carries its week and evidence, so it is reversible."""
    s = open(TARGETS, encoding="utf-8").read()
    if "\n  learned:" not in s:
        s = s.replace("\nseniority:", "\n  # Promoted weekly by keyword_learn.py from applied JDs — hard skills the\n"
                      "  # base resume already evidences, in the market's wording. Scored by rank.py\n"
                      "  # at keywords_learn.learned_weight. Remove a line to un-learn it.\n"
                      "  learned: []\n\nseniority:", 1)
    lines = "".join(f'    - "{t}"   # learned {week()}, {docs[t]} JDs\n' for t in terms)
    s = s.replace("  learned: []\n", "  learned:\n" + lines, 1) if "  learned: []\n" in s else \
        re.sub(r"(  learned:\n(?:    - .*\n)*)", lambda m: m.group(1) + lines, s, count=1)
    open(TARGETS, "w", encoding="utf-8").write(s)
    print(f"  targets.yaml: +{len(terms)} learned keyword(s)")


def report(min_docs=2):
    df, n = mine()
    existing = targets_keywords()
    print(f"  {n} applied JDs; {len(df)} distinct hard/soft skill keywords\n")
    print(f"  {'JDs':>4}  {'keyword':<32} {'cat':<5} {'status'}")
    for term, d in sorted(df.items(), key=lambda kv: (-kv[1]["docs"], kv[0]))[:40]:
        if d["docs"] < min_docs:
            break
        status = "covered by targets.yaml" if covered(term, existing) else ("candidate" if d["category"] == "hard" else "soft — not promoted")
        print(f"  {d['docs']:>4}  {term:<32} {d['category']:<5} {status}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.promote or a.dry_run:
        promote(dry_run=a.dry_run)
    else:
        report()


if __name__ == "__main__":
    main()
