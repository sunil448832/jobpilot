#!/usr/bin/env python3
"""
find_careers.py — locate a company's careers site, then fingerprint its ATS.

careers.py can identify the ATS behind a careers URL, but only if you already
know the URL. Guessing them by hand went badly (careers.adnoc.ae and
jobs.core42.ai do not exist). So this tries the handful of patterns companies
actually use, in parallel, and hands whatever resolves to careers.py.

    python jobs/find_careers.py --domains tii.ae g42.ai mbzuai.ac.ae
    python jobs/find_careers.py --market gulf        # a curated list
    python jobs/find_careers.py --market gulf --add  # ...and register what it finds
"""
import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

PATTERNS = [
    "https://careers.{d}", "https://jobs.{d}", "https://{d}/careers",
    "https://www.{d}/careers", "https://{d}/en/careers", "https://www.{d}/en/careers",
    "https://{d}/careers/jobs", "https://{d}/about/careers", "https://{d}/join-us",
    "https://{d}/company/careers", "https://careers.{d}/en",
]

# AI/ML-relevant employers in Sunil's sponsorship markets, grouped so a sweep can
# target one country at a time. The US is deliberately absent — it is oversupplied
# and H-1B-gated, so discovery effort spent there displaces reachable markets.
EMPLOYERS = os.path.join(CONFIG, "employers.yaml")


def load_markets():
    """{market: [domains]} from config/employers.yaml — the sweep lists live there, not here."""
    try:
        import yaml
        doc = yaml.safe_load(open(EMPLOYERS, encoding="utf-8")) or {}
        return {k: [str(d).strip() for d in (v or []) if str(d).strip()] for k, v in doc.items()}
    except (OSError, ValueError) as e:
        print(f"  [find_careers] {EMPLOYERS}: {type(e).__name__}: {e}")
        return {}


MARKETS = load_markets()


def probe_url(url, timeout=12):
    """Does this URL exist and look like a careers page?"""
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    if r.status_code >= 400 or len(r.text) < 2000:
        return None
    low = r.text.lower()
    if not re.search(r"career|job|vacanc|opening|position|hiring|apply", low):
        return None
    return r.url


def find_for(domain):
    for pat in PATTERNS:
        u = probe_url(pat.format(d=domain))
        if u:
            return domain, u
    return domain, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", nargs="*")
    ap.add_argument("--market", choices=sorted(MARKETS))
    ap.add_argument("--add", action="store_true", help="register what resolves")
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    domains = list(a.domains or [])
    if a.market:
        domains += MARKETS[a.market]
    domains = list(dict.fromkeys(domains))
    if not domains:
        ap.error("give --domains or --market")

    print(f"  probing {len(domains)} domains x {len(PATTERNS)} url patterns ...")
    found = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(find_for, d): d for d in domains}
        for fut in as_completed(futs):
            d, url = fut.result()
            if url:
                found.append((d, url))
                print(f"    {d:<24} {url[:66]}")
    print(f"\n  {len(found)}/{len(domains)} careers sites located")

    if not a.add:
        print("  re-run with --add to fingerprint and register them")
        return

    from jobpilot.discover import careers
    ok = 0
    print("\n  fingerprinting the ATS behind each ...")
    for d, url in found:
        try:
            if careers.add(url):
                ok += 1
        except Exception as e:
            print(f"    {d}: {type(e).__name__}")
    print(f"\n  {ok} registered in boards.yaml")


if __name__ == "__main__":
    main()
