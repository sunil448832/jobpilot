#!/usr/bin/env python3
"""
startups.py — pull AI/ML startups into the board list (YC and beyond).

Startups are worth adding, but the "startups sponsor more" premise needs
splitting apart, because it is only true in some markets:

  NOTE (2026-09-07): every YC match so far has been US-based. The pipeline finds
  them fine — Fieldguide 67.9, Checkr 64.5 — but they sit behind the market
  weighting because a small US company is the least able to sponsor an H-1B.
  The value here is the REMOTE subset, not the SF one.

  US        OFTEN FALSE for early stage. H-1B costs real money and legal work,
            and the lottery makes an abroad hire a gamble. Plenty of YC companies
            state outright that they cannot sponsor. Series B+ with 50+ staff is
            a different story.
  NL / EU   TRUE. Dutch recognised-sponsor status is cheap and common, and many
            scale-ups hold it. This is the best startup-visa market for Sunil.
  Gulf      TRUE, trivially — sponsorship is standard for every expat hire.
  Remote    TRUE in effect. Startups hire internationally as contractors far more
            readily than enterprises, which is Sunil's no-visa-needed channel.

So team_size is a filter, not a detail: a 5-person seed company cannot sponsor
anyone. Default floor is 15.

Usage:
    python jobs/startups.py --scan            # find YC startups with live boards
    python jobs/startups.py --scan --min-size 30 --limit 400
    python jobs/startups.py --stats
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
from jobpilot.core.config import cfg  # noqa: E402
CACHE = os.path.join(DATA, ".yc_companies.json")
BOARDS = os.path.join(CONFIG, "boards.yaml")
YC_URL = "https://yc-oss.github.io/api/companies/all.json"

AI_TAGS = re.compile(r"artificial.intelligence|machine.learning|\bai\b|generative|"
                     r"\bllm|nlp|computer.vision|data.science|deep.learning|"
                     r"ml.?ops|agents?\b|robotics", re.I)


def companies(refresh=False):
    if os.path.isfile(CACHE) and not refresh:
        return json.load(open(CACHE))
    print("  fetching YC company list (~10MB) ...")
    d = requests.get(YC_URL, headers={"User-Agent": "jobbot/1.0"}, timeout=120).json()
    json.dump(d, open(CACHE, "w"))
    return d


def relevant(c, min_size):
    if (c.get("status") or "").lower() != "active":
        return False
    if (c.get("team_size") or 0) < min_size:
        return False
    blob = " ".join(str(c.get(k) or "") for k in
                    ("industry", "subindustry", "one_liner", "long_description"))
    blob += " " + " ".join(c.get("tags") or [])
    return bool(AI_TAGS.search(blob))


def slug_candidates(c):
    """Board slugs a company plausibly uses."""
    out = []
    for src in (c.get("slug"), c.get("name")):
        if not src:
            continue
        s = re.sub(r"[^a-z0-9]+", "", str(src).lower())
        if 2 < len(s) < 30:
            out.append(s)
    site = c.get("website") or ""
    m = re.search(r"https?://(?:www\.)?([^./]+)", site)
    if m:
        s = re.sub(r"[^a-z0-9]+", "", m.group(1).lower())
        if 2 < len(s) < 30:
            out.append(s)
    return list(dict.fromkeys(out))


def scan(min_size=10, limit=600, workers=14):
    from jobpilot.discover import intake
    cs = [c for c in companies() if relevant(c, min_size)]
    cs.sort(key=lambda c: -(c.get("team_size") or 0))
    cs = cs[:limit]
    print(f"  {len(cs)} active AI/ML YC companies with >= {min_size} staff\n")

    seen, tasks = set(), []
    for c in cs:
        for s in slug_candidates(c):
            if s not in seen:
                seen.add(s)
                tasks.append((s, c))
    print(f"  probing {len(tasks)} slugs across 6 board APIs ({workers} parallel) ...")

    found, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(intake.probe, s): (s, c) for s, c in tasks}
        for fut in as_completed(futs):
            s, c = futs[fut]
            done += 1
            try:
                kind, n = fut.result()
            except Exception:
                continue
            if kind and n:
                found.append((kind, s, n, c))
                print(f"    [{done:4}/{len(tasks)}] {s:<22} {kind:<16} {n:>4} jobs   "
                      f"{c['name'][:22]} ({c.get('team_size')} staff, {c.get('batch')})")

    d = yaml.safe_load(open(BOARDS)) if os.path.isfile(BOARDS) else {}
    added = 0
    for kind, s, n, c in found:
        d.setdefault(kind, [])
        if not any(b["slug"] == s for b in d[kind]):
            d[kind].append({"slug": s, "jobs": n, "yc": c.get("batch")})
            added += 1
    for k in d:
        d[k] = sorted(d[k], key=lambda b: b["slug"])
    yaml.safe_dump(d, open(BOARDS, "w"), sort_keys=False)
    print(f"\n  {len(found)} live startup boards, {added} new -> boards.yaml")
    print(f"  total boards now: {sum(len(v) for v in d.values())}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--min-size", type=int, default=cfg("discovery.yc_min_size", 10))
    ap.add_argument("--limit", type=int, default=cfg("discovery.yc_limit", 600))
    ap.add_argument("--workers", type=int, default=cfg("discovery.workers", 14))
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    if a.stats:
        cs = companies()
        act = [c for c in cs if (c.get("status") or "").lower() == "active"]
        ai = [c for c in act if relevant(c, 1)]
        print(f"  {len(cs)} YC companies, {len(act)} active, {len(ai)} AI/ML-tagged")
        for lo in (5, 15, 30, 50, 100):
            print(f"    >= {lo:>3} staff: {len([c for c in ai if (c.get('team_size') or 0) >= lo])}")
        return
    if a.scan:
        return scan(a.min_size, a.limit, a.workers)
    ap.print_help()


if __name__ == "__main__":
    main()
