#!/usr/bin/env python3
"""
mine_keywords.py — grow the alias table from the JD corpus, not from guesswork.

The aliases in keywords.py started as a hand-written list, which means it only ever
catches synonyms someone thought of. This mines the ~250 real job descriptions
already in state.db and reports, by document frequency:

  COVERED    terms an existing canonical keyword already matches
  CANDIDATE  frequent technical terms nothing matches — the gaps in the table
  IGNORED    boilerplate, benefits, legal, and hiring-process language

It is a DISCOVERY tool, not a scorer. Everything it surfaces is reviewed before
being added, because an alias is a claim that two phrases mean the same thing —
and only some of them do. (Embedding-based scoring was tried for this and failed;
see semantic.py.)

    python jobs/mine_keywords.py                 # top uncovered terms
    python jobs/mine_keywords.py --title "forward deployed"
    python jobs/mine_keywords.py --for rag       # what co-occurs with one keyword
"""
import argparse
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
from jobpilot.rank import keywords as KW                                       # noqa: E402

DB = os.path.join(DATA, "state.db")

# Language that appears in every JD and describes no skill.
BOILER = re.compile(
    r"equal opportunit|without regard|protected veteran|reasonable accommodation|"
    r"criminal histor|e-verify|salary range|base pay|total compensation|stock option|"
    r"health insurance|dental|401k|paid time off|parental leave|"
    r"we are committed|our mission|our values|about us|join us|apply now|"
    r"hiring process|interview process|application process|privacy polic|"
    r"years of experience|bachelor|master|phd|degree in|equivalent experience",
    re.I)

STOP = set("""a an the and or but if then than that this these those of in on at to for with
by from as is are was were be been being have has had do does did will would shall should
can could may might must you your we our us they their it its he she his her them who whom
which what when where why how all any both each few more most other some such no nor not
only own same so too very just also very much many well new work working works team teams
role roles job jobs company companies help helps helping build building built make makes
making use uses using used need needs needed want wants like across within about into over
under between during through per via ability able across strong great good best high highly
experience experienced skills skill including include includes etc e g i e
core unique while future senior alongside span value values people person impact
opportunity opportunities candidate candidates benefits time understand based
solve solving problem problems complex technical technology technologies business
customer customers client clients partner partners stakeholder stakeholders
product products platform platforms solution solutions service services system
systems engineer engineers engineering design designing developer development
software application applications project projects process processes approach
world class leading global growth growing scale scaling scalable ensure ensuring
drive driving deliver delivering support supporting collaborate collaborating
cross functional communicate communication write writing writer own owner
ownership lead leading leader manage managing management level levels years year
day days week weeks month months san francisco new york london remote hybrid
onsite office location locations position positions candidate role opening
learn learning teach teaching mentor mentoring grow growth career careers
translate translating integrate integrating integration deliverables outcome
outcomes result results success successful quality reliability reliable
information detail details area areas field fields space spaces domain domains""".split())

TECH_HINT = re.compile(
    r"[a-z]+\.(js|py|io|ai)|[a-z]+-[a-z]+|\d|^(k8s|ci|cd|ml|ai|nlp|llm|rag|api|sdk|gpu|"
    r"cpu|sql|aws|gcp|etl|elt)$", re.I)


def jds(title_filter=None, limit=None):
    con = sqlite3.connect(DB)
    q = "SELECT title, jd FROM jobs WHERE LENGTH(jd) > 800"
    args = []
    if title_filter:
        q += " AND lower(title) LIKE ?"
        args.append(f"%{title_filter.lower()}%")
    if limit:
        q += f" LIMIT {int(limit)}"
    return con.execute(q, args).fetchall()


def phrases(text):
    """1-3 word candidates from one JD, deduped to document frequency."""
    t = KW.normalize(text)
    sents = re.split(r"[.;:\n]", t)
    out = set()
    for sent in sents:
        if BOILER.search(sent):
            continue
        toks = [w for w in sent.split() if w and w not in STOP and len(w) > 1]
        for n in (1, 2, 3):
            for i in range(len(toks) - n + 1):
                g = " ".join(toks[i:i + n])
                if len(g) < 3 or len(g) > 40:
                    continue
                if g[0].isdigit():
                    continue
                out.add(g)
    return out


def covered_by():
    """canonical keyword -> all its surface forms, for coverage testing."""
    import yaml
    with open(os.path.join(CONFIG, "targets.yaml")) as f:
        t = yaml.safe_load(f)
    canon = t["keywords"]["strong"] + t["keywords"]["strong_recent"]
    forms = {}
    for k in canon:
        forms[k] = KW.forms(k)
    return canon, forms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", help="only JDs whose title contains this")
    ap.add_argument("--for", dest="anchor", help="terms co-occurring with one keyword")
    ap.add_argument("--top", type=int, default=45)
    ap.add_argument("--min-docs", type=int, default=8)
    ap.add_argument("--max-frac", type=float, default=0.40,
                    help="drop terms in more than this fraction of JDs — a phrase in "
                         "80%% of postings is boilerplate, not a skill")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    rows = jds(a.title)
    print(f"  mining {len(rows)} JDs" + (f" matching '{a.title}'" if a.title else ""))
    canon, forms = covered_by()
    all_forms = set()
    for v in forms.values():
        all_forms |= v

    df = Counter()
    per_doc = []
    for title, jd in rows:
        p = phrases(f"{title}\n{jd}")
        per_doc.append(p)
        df.update(p)

    if a.anchor:
        anchor_forms = KW.forms(a.anchor)
        hits = [p for p in per_doc if p & anchor_forms]
        print(f"  '{a.anchor}' appears in {len(hits)}/{len(rows)} JDs\n")
        co = Counter()
        for p in hits:
            co.update(p)
        print("  most co-occurring terms (candidate aliases):")
        for term, n in co.most_common(200):
            if term in all_forms or df[term] < 3:
                continue
            lift = (n / max(len(hits), 1)) / max(df[term] / len(rows), 1e-9)
            # Lift alone lets generic English through, so require a stronger
            # association AND a term that is not just a common JD word.
            if lift > 2.2 and len(term.split()) <= 3 and n >= 5:
                print(f"    {n:3}/{len(hits):<3} lift {lift:4.1f}  {term}")
        return

    ceiling = a.max_frac * len(rows)
    covered, candidates = [], []
    for term, n in df.most_common(6000):
        if n < a.min_docs:
            break
        if BOILER.search(term) or n > ceiling:
            continue
        if term in all_forms:
            covered.append((term, n))
        else:
            candidates.append((term, n))

    print(f"  {len(covered)} phrase-forms already covered by the table")
    print(f"\n  TOP UNCOVERED TERMS — candidates for new aliases "
          f"(document frequency out of {len(rows)}):\n")
    for term, n in candidates[:a.top]:
        bar = "█" * min(int(n / len(rows) * 40), 30)
        print(f"    {n:3} ({n/len(rows)*100:4.1f}%) {bar:<30} {term}")


if __name__ == "__main__":
    main()
