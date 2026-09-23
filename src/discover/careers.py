#!/usr/bin/env python3
"""
careers.py — careers-site-first discovery.

Target the company's OWN careers site, not a board aggregator. The careers site
is canonical: it carries every requisition, including the ones no job board ever
sees. Underneath, almost every site runs a known ATS with a public JSON API — so
fingerprint the site, then talk to whatever it is running.

    python jobs/careers.py --detect https://careers.tii.ae
    python jobs/careers.py --add https://careers.g42.ai/inception   # save to boards.yaml
    python jobs/careers.py --list

Supported so far: Phenom People, Greenhouse, Lever, Ashby, SmartRecruiters,
Workable, Recruitee, Workday. Unknown sites are reported, never guessed at.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

import requests
import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
BOARDS = os.path.join(CONFIG, "boards.yaml")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

PHENOM_BODY = {
    "lang": "en_us", "deviceType": "desktop", "country": "us",
    "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "",
    "subsearch": "", "from": 0, "jobs": True, "counts": True,
    "all_fields": ["country", "state", "city", "category", "type"],
    "size": 100, "clearAll": False, "jdsource": "facets",
    "isSliderEnable": False, "pageId": "page1", "siteType": "external",
    "keywords": "", "global": True,
}


def _find_jobs(o, depth=0):
    if depth > 5:
        return None
    if isinstance(o, dict):
        for k, v in o.items():
            if k.lower() == "jobs" and isinstance(v, list) and v:
                return v
            got = _find_jobs(v, depth + 1)
            if got:
                return got
    return None


def phenom_fetch(base, start=0, size=100):
    """base = 'https://careers.tii.ae' or 'https://careers.g42.ai/inception'."""
    body = dict(PHENOM_BODY, **{"from": start, "size": size})
    r = requests.post(base.rstrip("/") + "/widgets",
                      headers={**UA, "Content-Type": "application/json",
                               "Accept": "application/json"},
                      json=body, timeout=30)
    r.raise_for_status()
    return _find_jobs(r.json()) or []


def detect(url):
    """Fingerprint the ATS behind a careers site. Returns (platform, config)."""
    p = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    host, base = p.netloc, f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"

    # Host-level giveaways first — no fetch needed.
    for pat, plat, slug in (
        (r"boards\.greenhouse\.io/([^/?#]+)", "greenhouse", 1),
        (r"job-boards\.greenhouse\.io/([^/?#]+)", "greenhouse", 1),
        (r"jobs\.lever\.co/([^/?#]+)", "lever", 1),
        (r"jobs\.ashbyhq\.com/([^/?#]+)", "ashby", 1),
        (r"jobs\.smartrecruiters\.com/([^/?#]+)", "smartrecruiters", 1),
        (r"apply\.workable\.com/([^/?#]+)", "workable", 1),
        (r"([^./]+)\.recruitee\.com", "recruitee", 1),
        (r"([^./]+)\.wd\d+\.myworkdayjobs\.com", "workday", 1),
    ):
        m = re.search(pat, url)
        if m:
            return plat, {"slug": m.group(slug)}

    try:
        r = requests.get(url, headers=UA, timeout=30)
        html = r.text
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}

    # Phenom: confirm by actually calling its search endpoint.
    if re.search(r"phenompeople|phenom\.|ddoKey|refineSearch", html, re.I):
        for cand in (base, f"{p.scheme}://{host}"):
            try:
                jobs = phenom_fetch(cand, size=5)
                if jobs:
                    return "phenom", {"base": cand, "sample": len(jobs)}
            except Exception:
                continue

    for pat, plat in ((r"greenhouse\.io", "greenhouse"), (r"lever\.co", "lever"),
                      (r"ashbyhq", "ashby"), (r"smartrecruiters", "smartrecruiters"),
                      (r"workable", "workable"), (r"recruitee", "recruitee"),
                      (r"myworkdayjobs", "workday"), (r"icims", "icims"),
                      (r"taleo", "taleo"), (r"successfactors", "successfactors"),
                      (r"hcmRestApi|oracle.*recruit", "oracle")):
        m = re.search(pat, html, re.I)
        if m:
            found = slug_from(html, plat)
            return plat, {"embedded": True, **({"slug": found} if found else {})}
    return None, {"note": "no known ATS fingerprint"}


# Board tokens that are really path noise. Without this the generic
# "<host>/<segment>" pattern yields "boards-api" from boards-api.greenhouse.io.
NOT_SLUG = {"www", "api", "static", "cdn", "embed", "boards", "job", "jobs",
            "boards-api", "job-boards", "apply", "v1", "v0", "assets",
            "posting-api", "widget", "widgets", "search", "en", "careers",
            "company", "companies", "job-board", "postings", "job_board"}

# How each ATS spells its own board in the page it embeds. detect() used to carry
# one of these — greenhouse — so every Ashby, Lever, Workable, SmartRecruiters and
# Recruitee site was fingerprinted correctly and then discarded as "no public API
# path". That is what capped a 111-domain sweep at 11 registered boards.
SLUG_PATS = {
    "greenhouse": [
        r"boards-api\.greenhouse\.io/v\d+/boards/([A-Za-z0-9_-]{2,40})",
        r"(?:boards|job-boards)\.greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]{2,40})",
        r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]{2,40})",
    ],
    "lever": [
        r"api\.lever\.co/v\d+/postings/([A-Za-z0-9_-]{2,40})",
        r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_-]{2,40})",
    ],
    "ashby": [
        r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9._-]{2,40})",
        r"jobs\.ashbyhq\.com/([A-Za-z0-9._-]{2,40})",
        r"jobBoardName['\"]?\s*[:=]\s*['\"]([A-Za-z0-9._-]{2,40})",
    ],
    "smartrecruiters": [
        r"api\.smartrecruiters\.com/v\d+/companies/([A-Za-z0-9]{2,40})",
        # jobs.smartrecruiters.com/Eurofins/7440001... and the /ni/ share form.
        # Matching only the careers./api. hosts missed this, which is the shape
        # an employer's own site actually links to.
        r"smartrecruiters\.com/(?:ni/)?([A-Za-z0-9]{2,40})/\d",
        r"smartrecruiters\.com/(?:ni/)?([A-Za-z0-9]{2,40})/[0-9a-f]{8}-",
        r"careers\.smartrecruiters\.com/([A-Za-z0-9]{2,40})",
    ],
    "workable": [
        r"apply\.workable\.com/api/v\d+/(?:widget/)?accounts/([A-Za-z0-9_-]{2,40})",
        r"apply\.workable\.com/([A-Za-z0-9_-]{2,40})",
        r"([A-Za-z0-9_-]{2,40})\.workable\.com",
    ],
    "recruitee": [
        r"([A-Za-z0-9_-]{2,40})\.recruitee\.com",
        r"recruitee\.com/o/([A-Za-z0-9_-]{2,40})",
    ],
}


def slug_from(blob, plat):
    """Pull the board token for `plat` out of page HTML or captured request URLs."""
    for pat in SLUG_PATS.get(plat, []):
        for m in re.finditer(pat, blob or "", re.I):
            cand = m.group(1)
            if cand.lower() not in NOT_SLUG:
                # SmartRecruiters board names are case-sensitive; the rest are not.
                return cand if plat == "smartrecruiters" else cand.lower()
    return None


def verify(plat, slug, base=None):
    """Does this board actually serve postings? Returns a count, or None.

    A slug scraped from markup is a guess until the API answers. Registering
    unverified guesses is how junk boards enter boards.yaml and then collect a
    company-scale bonus for postings that do not exist.
    """
    from jobpilot.discover import intake                       # lazy: intake imports careers back
    fn = {"greenhouse": intake.from_greenhouse, "lever": intake.from_lever,
          "ashby": intake.from_ashby, "smartrecruiters": intake.from_smartrecruiters,
          "workable": intake.from_workable, "recruitee": intake.from_recruitee}.get(plat)
    if not fn:
        return None
    try:
        return len(list(fn(slug)))
    except Exception:
        return None


ATS_HOSTS = {
    "greenhouse.io": "greenhouse", "lever.co": "lever", "ashbyhq.com": "ashby",
    "smartrecruiters.com": "smartrecruiters", "workable.com": "workable",
    "recruitee.com": "recruitee", "myworkdayjobs.com": "workday",
    "icims.com": "icims", "taleo.net": "taleo", "successfactors": "successfactors",
    "phenompeople": "phenom", "eightfold.ai": "eightfold", "teamtailor": "teamtailor",
    "personio": "personio", "join.com": "join", "jobvite": "jobvite",
    "oraclecloud.com": "oracle", "hcmRestApi": "oracle", "avature": "avature",
}


def detect_deep(url, timeout=25000):
    """Render the page and watch what it actually calls.

    Most careers sites are SPAs: the ATS is loaded by JavaScript after the first
    response, so a fingerprint on the initial HTML sees nothing. 17 of 23 Dutch
    sites came back UNKNOWN that way. Watching the network reveals the real ATS.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, {"error": "playwright not available"}
    seen = set()
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="chrome", headless=True)
            page = b.new_page()
            page.on("request", lambda r: seen.add(r.url))
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                page.wait_for_timeout(6000)
            except Exception:
                pass
            frames = [f.url for f in page.frames]
            html = ""
            try:
                html = page.content()[:400000]
            except Exception:
                pass
            b.close()
    except Exception as e:
        return None, {"error": f"{type(e).__name__}"}

    blob = " ".join(list(seen) + frames)
    for host, plat in ATS_HOSTS.items():
        if host in blob or host in html:
            # The board token is the PATH segment after the ATS host. Matching a
            # subdomain instead yields "boards-api" from boards-api.greenhouse.io.
            slug = slug_from(blob + " " + html, plat)
            for pat in ([] if slug else (rf"{re.escape(host)}/v\d+/boards/([a-z0-9][a-z0-9-]{{2,40}})",
                        rf"{re.escape(host)}/embed/job_board\?for=([a-z0-9][a-z0-9-]{{2,40}})",
                        rf"{re.escape(host)}/(?:job-board/)?([a-z0-9][a-z0-9-]{{2,40}})",
                        rf"([a-z0-9][a-z0-9-]{{2,40}})\.{re.escape(host)}")):
                for m in re.finditer(pat, blob, re.I):
                    cand = m.group(1).lower()
                    if cand not in NOT_SLUG:
                        slug = cand
                        break
                if slug:
                    break
            return plat, {"slug": slug, "via": "rendered"} if slug else {"via": "rendered"}
    return None, {"note": "no ATS seen even after rendering"}


def add(url, deep=True):
    plat, cfg = detect(url)
    if not plat and deep:
        plat, cfg = detect_deep(url)
    if not plat:
        print(f"  {url}\n    -> UNKNOWN ATS: {cfg}")
        return False
    print(f"  {url}\n    -> {plat}  {cfg}")
    if plat == "phenom" and cfg.get("base"):
        d = yaml.safe_load(open(BOARDS)) if os.path.isfile(BOARDS) else {}
        d.setdefault("phenom", [])
        # One host = one board, whatever locale path it was found under.
        host = urllib.parse.urlparse(cfg["base"]).netloc
        if not any(b.get("slug") == host for b in d["phenom"]):
            jobs = phenom_fetch(cfg["base"])
            d["phenom"].append({"slug": urllib.parse.urlparse(cfg["base"]).netloc,
                                "base": cfg["base"], "jobs": len(jobs)})
            yaml.safe_dump(d, open(BOARDS, "w"), sort_keys=False)
            print(f"    -> saved to boards.yaml ({len(jobs)} jobs)")
        else:
            print("    -> already in boards.yaml")
        return True
    if cfg.get("slug"):
        d = yaml.safe_load(open(BOARDS)) if os.path.isfile(BOARDS) else {}
        d.setdefault(plat, [])
        if any(b["slug"] == cfg["slug"] for b in d[plat]):
            print("    -> already in boards.yaml")
            return True
        # Confirm before writing. A slug lifted from markup is a guess, and an
        # unverified guess in boards.yaml costs a network round-trip on every
        # scan forever while contributing nothing.
        n = verify(plat, cfg["slug"])
        if n is None:
            print(f"    -> slug '{cfg['slug']}' did not resolve on {plat}; not saved")
            return False
        d[plat].append({"slug": cfg["slug"], "jobs": n})
        yaml.safe_dump(d, open(BOARDS, "w"), sort_keys=False)
        print(f"    -> saved to boards.yaml ({n} jobs)")
        return True
    print("    -> recognised but no public API path; needs manual checking")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detect", metavar="URL")
    ap.add_argument("--add", nargs="+", metavar="URL")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-deep", action="store_true", help="skip browser rendering")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    if a.list:
        d = yaml.safe_load(open(BOARDS)) if os.path.isfile(BOARDS) else {}
        for k, v in d.items():
            print(f"  {k:<16} {len(v)}")
        return
    if a.detect:
        plat, cfg = detect(a.detect)
        print(f"  {a.detect}\n    -> {plat or 'UNKNOWN'}  {cfg}")
        return
    if a.add:
        for u in a.add:
            add(u, deep=not a.no_deep)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
