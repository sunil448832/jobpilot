"""oracle.py — careers sites on Oracle Recruiting Cloud (Fusion HCM "CandidateExperience":
Presight, Mubadala and most large Gulf / enterprise employers): the public REST endpoints the
site itself calls. One entry per site under `custom:` in boards.yaml:
    {slug: presight, site: oracle, host: iaambv.fa.ocs.oraclecloud.com, site_number: CX_1, company: Presight}
Intake reads the requisition list (every posting, with its primary location), then the details
of each wanted title (ExternalDescriptionStr + ExternalQualificationsStr)."""
import re
import urllib.parse

import requests

from jobpilot.apply.platforms._custom import UA, text

HOSTS = ("oraclecloud.com",)
ROUTE = "review"
PAGE = 25


def _api(host):
    return f"https://{host}/hcmRestApi/resources/latest"


def _list(host, site_number, offset):
    finder = f"findReqs;siteNumber={site_number},limit={PAGE},offset={offset},sortBy=POSTING_DATES_DESC"
    r = requests.get(f"{_api(host)}/recruitingCEJobRequisitions",
                     params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder},
                     headers=UA, timeout=30)
    r.raise_for_status()
    items = (r.json() or {}).get("items") or [{}]
    return items[0].get("requisitionList") or [], int(items[0].get("TotalJobsCount") or 0)


def _details(host, site_number, req_id):
    finder = f'ById;Id="{req_id}",siteNumber={site_number}'
    r = requests.get(f"{_api(host)}/recruitingCEJobRequisitionDetails",
                     params={"expand": "all", "onlyData": "true", "finder": finder}, headers=UA, timeout=30)
    r.raise_for_status()
    items = (r.json() or {}).get("items") or []
    return items[0] if items else {}


def job_url(host, site_number, req_id):
    return f"https://{host}/hcmUI/CandidateExperience/en/sites/{site_number}/job/{req_id}"


def _jd(det):
    parts = [det.get("ExternalDescriptionStr"), det.get("ExternalQualificationsStr")]
    return text("\n\n".join(p for p in parts if p))


def search(markets, host, site_number="CX_1", company="", known=(), want=lambda title: True, limit=400):
    """Every requisition on the site (the list is small enough to walk whole); JD fetched for
    wanted titles not already in state.db."""
    offset, seen = 0, set()
    while offset < limit:
        try:
            reqs, total = _list(host, site_number, offset)
        except Exception:
            break
        for q in reqs:
            rid = str(q.get("Id") or "")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            url = job_url(host, site_number, rid)
            title = q.get("Title") or ""
            locs = [q.get("PrimaryLocation") or ""] + [s.get("Name") or "" for s in (q.get("secondaryLocations") or [])]
            jd = ""
            if url not in known and want(title):
                try:
                    jd = _jd(_details(host, site_number, rid))
                except Exception:
                    jd = ""
            yield {"company": company or host.split(".")[0].title(), "title": title,
                   "location": "; ".join(l for l in locs if l), "url": url, "jd": jd[:12000]}
        if len(reqs) < PAGE or offset + PAGE >= total:
            break
        offset += PAGE


def match_url(url):
    return "oraclecloud.com" in url and "/CandidateExperience/" in url


def parse_url(url):
    """(host, site_number, req_id) from .../CandidateExperience/en/sites/CX_1/job/3288[/...]"""
    p = urllib.parse.urlparse(url)
    m = re.search(r"/sites/([^/]+)/(?:job|requisitions)/(\d+)", p.path)
    return (p.netloc, m.group(1), m.group(2)) if m else (p.netloc, None, None)


def fetch_jd(url):
    host, site_number, rid = parse_url(url)
    if not rid:
        return None
    det = _details(host, site_number, rid)
    if not det:
        raise SystemExit(f"EXPIRED: no such requisition — {url}")
    return {"title": det.get("Title") or "", "company": "", "location": det.get("PrimaryLocation") or "",
            "text": _jd(det), "apply_url": url}
