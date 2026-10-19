"""pinpoint.py — careers sites on Pinpoint (<company>.pinpointhq.com: tabby and many UK / Gulf
mid-size employers): its public postings.json carries every posting with the full description.
One entry per site under `custom:` in boards.yaml:
    {slug: tabby, site: pinpoint, host: tabby.pinpointhq.com, company: tabby}"""
import re

import requests

from jobpilot.apply.platforms._custom import UA, text

HOSTS = ("pinpointhq.com",)
ROUTE = "review"


def _postings(host):
    r = requests.get(f"https://{host}/postings.json", headers=UA, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("data") if isinstance(d, dict) else d


def _loc(p):
    loc = p.get("location") or {}
    parts = [loc.get("city"), loc.get("name") or loc.get("country")]
    s = ", ".join(x for x in parts if x)
    if (p.get("workplace_type") or "") == "remote":
        s = (s + " " if s else "") + "Remote"
    return s


def _jd(p):
    return text("\n\n".join(x for x in (p.get("description"), p.get("key_responsibilities"),
                                         p.get("skills_knowledge_expertise"), p.get("benefits")) if x))


def search(markets, host, company="", known=(), want=lambda title: True):
    for p in _postings(host) or []:
        title = p.get("title") or ""
        url = p.get("url") or f"https://{host}{p.get('path') or ''}"
        yield {"company": company or host.split(".")[0].title(), "title": title, "location": _loc(p),
               "url": url, "jd": ("" if url in known or not want(title) else _jd(p))[:12000]}


def match_url(url):
    return "pinpointhq.com" in url


def fetch_jd(url):
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
    for p in _postings(host) or []:
        if (p.get("url") or "") == url or (p.get("path") and p["path"] in url):
            return {"title": p.get("title") or "", "company": host.split(".")[0].title(),
                    "location": _loc(p), "text": _jd(p), "apply_url": url}
    raise SystemExit(f"EXPIRED: posting not on {host} any more — {url}")
