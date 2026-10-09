"""eightfold.py — careers sites run on Eightfold (Microsoft's apply.careers.microsoft.com and
others): its search and position-details JSON. One entry per site under `custom:` in
boards.yaml: {slug: microsoft, site: eightfold, host: apply.careers.microsoft.com,
domain: microsoft.com, company: Microsoft}."""
import re

import requests

from jobpilot.apply.platforms._custom import UA, queries, text

HOSTS = ("eightfold.ai", "apply.careers.microsoft.com")
ROUTE = "review"
COUNTRY = {"netherlands": "Netherlands", "ireland": "Ireland", "germany": "Germany", "uae": "United Arab Emirates",
           "australia": "Australia", "switzerland": "Switzerland", "luxembourg": "Luxembourg", "usa": "United States"}
SITES = {"apply.careers.microsoft.com": ("microsoft.com", "Microsoft")}


def _details(host, domain, pid):
    r = requests.get(f"https://{host}/api/pcsx/position_details", params={"position_id": pid, "domain": domain},
                     headers=UA, timeout=30)
    return (r.json() or {}).get("data") or {}


def search(markets, host="apply.careers.microsoft.com", domain="microsoft.com", company="Microsoft", known=(),
           want=lambda title: True):
    seen = set()
    for m in markets:
        loc = COUNTRY.get(m)
        if not loc:
            continue
        for q in queries():
            for start in (0, 10, 20, 30):
                try:
                    d = requests.get(f"https://{host}/api/pcsx/search", params={"domain": domain, "query": q,
                                     "location": loc, "start": start}, headers=UA, timeout=30).json()
                except Exception:
                    break
                pos = ((d or {}).get("data") or {}).get("positions") or []
                for p in pos:
                    if p["id"] in seen:
                        continue
                    seen.add(p["id"])
                    url = f"https://{host}{p.get('positionUrl') or '/careers/job/' + str(p['id'])}"
                    try:                                  # a known posting's JD is in state.db already
                        det = {} if url in known or not want(p.get("name")) else _details(host, domain, p["id"])
                    except Exception:
                        det = {}
                    yield {"company": company, "title": p.get("name") or "",
                           "location": "; ".join(p.get("locations") or []),
                           "url": url,
                           "jd": text(det.get("jobDescription"))[:12000]}
                if len(pos) < 10:
                    break


def fetch_jd(url):
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
    pid = re.search(r"/job/(\d+)", url)
    domain, company = SITES.get(host, (None, None))
    if not (pid and domain):
        return None
    d = _details(host, domain, pid.group(1))
    if not d:
        raise SystemExit(f"EXPIRED: no such position — {url}")
    return {"title": d.get("name") or "", "company": company, "location": "; ".join(d.get("locations") or []),
            "text": text(d.get("jobDescription")), "apply_url": url}
