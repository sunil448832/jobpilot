"""google.py — Google's careers site (google.com/about/careers): the results page carries its
jobs as embedded data (AF_initDataCallback ds:1), 20 a page. Applying needs a Google account
signed in once in the tool's Chrome (./jobpilot signin google)."""
import json
import re

import requests

from jobpilot.apply.platforms._custom import UA, queries, text

ROUTE = "review"
BASE = "https://www.google.com/about/careers/applications/jobs/results"
COUNTRY = {"netherlands": "Netherlands", "ireland": "Ireland", "germany": "Germany", "uae": "United Arab Emirates",
           "australia": "Australia", "switzerland": "Switzerland", "luxembourg": "Luxembourg", "usa": "United States"}


def match_url(url):
    return "google.com/about/careers" in (url or "")


def _jobs(page_html):
    m = re.search(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel", page_html, re.S)
    if not m:
        return []
    try:
        return (json.loads(m.group(1)) or [[]])[0] or []
    except ValueError:
        return []


def _row(e):
    def part(i):
        x = e[i] if len(e) > i else None
        return text(x[1]) if isinstance(x, list) and len(x) > 1 else ""
    locs = "; ".join(l[0] for l in (e[9] or []) if l) if len(e) > 9 else ""
    slug = re.sub(r"[^a-z0-9]+", "-", (e[1] or "").lower()).strip("-")
    jd = "\n\n".join(x for x in (part(10), "RESPONSIBILITIES\n" + part(3), part(4), part(19)) if x.strip())
    return {"company": e[7] or "Google", "title": e[1] or "", "location": locs,
            "url": f"{BASE}/{e[0]}-{slug}", "jd": jd[:12000]}


def search(markets, known=(), want=lambda title: True):
    seen = set()
    for m in markets:
        loc = COUNTRY.get(m)
        if not loc:
            continue
        for q in queries():
            for page in (1, 2, 3):
                try:
                    h = requests.get(BASE, params={"q": q, "location": loc, "page": page}, headers=UA, timeout=30).text
                except Exception:
                    break
                rows = _jobs(h)
                for e in rows:
                    if e and e[0] not in seen:
                        seen.add(e[0])
                        yield _row(e)
                if len(rows) < 20:
                    break


def fetch_jd(url):
    jid = re.search(r"/results/(\d+)", url or "")
    if not jid:
        return None
    h = requests.get(f"{BASE}/{jid.group(1)}", headers=UA, timeout=30).text
    # a job's own page carries it as ds:0; ds:1 is the list of other jobs
    m = re.search(r"AF_initDataCallback\(\{key: 'ds:0'.*?data:(.*?), sideChannel", h, re.S)
    try:
        own = json.loads(m.group(1)) if m else []
    except ValueError:
        own = []
    for e in ([own[0]] if own and isinstance(own[0], list) else []) + _jobs(h):
        if e and str(e[0]) == jid.group(1):
            r = _row(e)
            return {"title": r["title"], "company": r["company"], "location": r["location"], "text": r["jd"],
                    "apply_url": url}
    raise SystemExit(f"EXPIRED: posting not found — {url}")
