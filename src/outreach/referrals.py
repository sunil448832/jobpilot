#!/usr/bin/env python3
"""
referrals.py — who do you already know at a target company? (Phase 5)

Reads the LinkedIn connections export (your own data, downloaded legitimately:
Settings → Data Privacy → Get a copy of your data → Connections) and joins it
against the companies in state.db. Ranks each contact by how useful the tie is.

This is better than any scraper: it is complete for your 1st degree, needs no
credentials, and breaks no terms.

    Settings → Data Privacy → Get a copy of your data → Connections → CSV
    save it as jobs/connections.csv

Usage:
    python jobs/referrals.py                 # matches across the whole queue
    python jobs/referrals.py --company openai
    python jobs/referrals.py --stats
"""
import argparse
import csv
import io
import os
import re
import sys

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)
# LinkedIn ships it as "Connections.csv"; accept either spelling.
def _find_csv():
    for n in ("connections.csv", "Connections.csv"):
        p = os.path.join(JOBS_DIR, n)
        if os.path.isfile(p):
            return p
    return os.path.join(DATA, "connections.csv")


CSV_PATH = _find_csv()

# Roles that actually help, in order. An engineer on the team beats a recruiter:
# they get a referral bonus and their referral carries internal weight.
ROLE_WEIGHTS = [
    (r"machine learning|ml engineer|ai engineer|applied scientist|research engineer"
     r"|data scientist|forward deployed", 40, "works in your field"),
    (r"engineering manager|head of (ai|ml|data)|director of (ai|ml|engineering)"
     r"|vp engineering", 32, "hiring manager level"),
    (r"staff|principal|senior.*engineer|lead engineer", 28, "senior engineer"),
    (r"software engineer|developer|sde", 22, "engineer"),
    (r"recruit|talent|people ops|hr\b", 18, "recruiter"),
    (r"founder|ceo|cto", 12, "founder — only if you know them well"),
]


def load_connections(path=CSV_PATH):
    """LinkedIn's export has a few preamble lines before the real header."""
    if not os.path.isfile(path):
        return None
    raw = open(path, encoding="utf-8-sig", errors="replace").read()
    lines = raw.splitlines()
    start = 0
    for i, line in enumerate(lines[:12]):
        if "First Name" in line and "Company" in line:
            start = i
            break
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    out = []
    for r in rows:
        out.append({
            "first": (r.get("First Name") or "").strip(),
            "last": (r.get("Last Name") or "").strip(),
            "url": (r.get("URL") or "").strip(),
            "company": (r.get("Company") or "").strip(),
            "position": (r.get("Position") or "").strip(),
            "connected": (r.get("Connected On") or "").strip(),
        })
    return out


def norm_co(s):
    s = (s or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|bv|b\.v\.|plc|pvt|"
               r"private|technologies|technology|labs|group|holdings)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def score_contact(c):
    pos = (c["position"] or "").lower()
    for rx, pts, why in ROLE_WEIGHTS:
        if re.search(rx, pos):
            return pts, why
    return 10, "connection at the company"


def match(connections, companies):
    """company -> ranked contacts."""
    by_co = {}
    index = {}
    for c in connections:
        key = norm_co(c["company"])
        if not key:                       # a blank company matches EVERY company
            continue                      # under a substring test — drop them
        index.setdefault(key, []).append(c)
    for co in companies:
        key = norm_co(co)
        if not key:
            continue
        hits = list(index.get(key, []))
        if not hits:                      # substring fallback: "Openai" vs "OpenAI Inc"
            for k, v in index.items():
                if not k or len(k) < 4:   # short keys match far too much
                    continue
                if (key in k or k in key) and abs(len(k) - len(key)) < 8:
                    hits.extend(v)
        scored = []
        for c in hits:
            pts, why = score_contact(c)
            scored.append((pts, why, c))
        scored.sort(key=lambda x: -x[0])
        if scored:
            by_co[co] = scored
    return by_co


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    conns = load_connections()
    if conns is None:
        print(f"""  No connections export found at {CSV_PATH}

  Get it (takes ~10 minutes, it is your own data):
    LinkedIn → Settings & Privacy → Data Privacy
    → Get a copy of your data → select "Connections" → Request archive
    → download the CSV and save it as jobs/connections.csv

  Then re-run. Until then, use: python jobs/prospects.py --company <name>
  which needs no export.""")
        sys.exit(1)

    print(f"  loaded {len(conns)} connections")
    if a.stats:
        from collections import Counter
        for co, n in Counter(c["company"] for c in conns if c["company"]).most_common(15):
            print(f"    {n:3}  {co}")
        return

    if a.company:
        companies = [a.company]
    else:
        import sqlite3
        con = sqlite3.connect(os.path.join(DATA, "state.db"))
        companies = [r[0] for r in con.execute(
            "SELECT DISTINCT company FROM jobs WHERE status IN ('new','queued')")]

    found = match(conns, companies)
    if not found:
        print("  no 1st-degree connections at any target company")
        print("  -> run: python jobs/prospects.py --company <name>")
        return
    for co, scored in sorted(found.items(), key=lambda x: -max(s[0] for s in x[1])):
        print(f"\n  {co} — {len(scored)} connection(s)")
        for pts, why, c in scored[:5]:
            print(f"    [{pts:3}] {c['first']} {c['last']:<18} {c['position'][:42]:<44} {why}")
            print(f"          {c['url']}")


if __name__ == "__main__":
    main()
