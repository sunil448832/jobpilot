"""generic.py — any employer's own careers site, read by code, no adapter of its own.

    search(markets, url, company, known)   the listing page's postings: links whose text reads
                                           like a role (rendered in headless Chrome when the
                                           page draws its links by script), each read below
    posting(url)                           one posting: its schema.org JobPosting (the JSON-LD
                                           search engines read: title, description, location),
                                           else None

An entry under `custom:` in boards.yaml: {slug: <name>, site: generic, url: <the careers
page that lists the jobs>, company: <name>, location: <where that page is filtered to>}.
A posting page without JobPosting data is read as text under its link text and `location`. Applying is the form agent's job, as anywhere.
"""
import json
import re
import urllib.parse

import requests

from jobpilot.apply.platforms._custom import UA, text

ROUTE = "review"
JOB_PATH = re.compile(r"/(jobs?|careers?|positions?|openings?|vacanc\w*|requisitions?|req|details|role|opportunit\w*)"
                      r"(/|-|\?|$)|[?&](job|gh_jid|jobid|id)=", re.I)
ROLE_WORDS = re.compile(r"engineer|scientist|machine learning|\bml\b|\bai\b|data|research|llm|developer|applied|"
                        r"architect|analyst", re.I)


def _anchors(html, base):
    out = []
    for href, label in re.findall(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        label = re.sub(r"<(style|script)\b.*?</\1>", " ", label, flags=re.S | re.I)   # CSS-in-JS inside links
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", label)).strip()
        if label:
            out.append((urllib.parse.urljoin(base, href), label))
    return out


def _rendered_anchors(url):
    """The links a script draws, read off headless Chrome (portals reject headless for
    applying, not for reading a list)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        try:
            pg = b.new_page(user_agent=UA["User-Agent"])
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(3500)                    # its script draws the content; analytics never go idle
            return [(urllib.parse.urljoin(url, h), re.sub(r"\s+", " ", t).strip())
                    for h, t in pg.eval_on_selector_all("a[href]", "els => els.map(a => [a.href, a.innerText])") if t]
        finally:
            b.close()


def job_links(url, limit=60):
    """[(posting url, its link text)] from a careers listing page."""
    try:
        anchors = _anchors(requests.get(url, headers=UA, timeout=30).text, url)
    except Exception:
        anchors = []
    pick = lambda a: [(h, t) for h, t in a if JOB_PATH.search(urllib.parse.urlparse(h).path + "?" +
                      urllib.parse.urlparse(h).query) and ROLE_WORDS.search(t) and len(t) < 160]
    found = pick(anchors)
    if len(found) < 3:                                   # drawn by script: read the rendered page
        try:
            found = pick(_rendered_anchors(url))
        except Exception:
            pass
    return list(dict.fromkeys(found))[:limit]


def _jsonld(html):
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            d = json.loads(blob.strip())
        except ValueError:
            continue
        stack = d if isinstance(d, list) else [d]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                t = x.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return x
                stack.extend(v for v in x.values() if isinstance(v, (list, dict)))
            elif isinstance(x, list):
                stack.extend(x)
    return None


def _place(loc):
    out = []
    for l in (loc if isinstance(loc, list) else [loc]):
        a = (l or {}).get("address") if isinstance(l, dict) else None
        if isinstance(a, dict):
            c = a.get("addressCountry")
            c = c.get("name") if isinstance(c, dict) else c
            out.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressRegion"), c) if x))
        elif isinstance(a, str):
            out.append(a)
    return "; ".join(x for x in out if x)


def page_text(html):
    """A posting page's readable text: scripts, styles, navigation and footers dropped."""
    body = re.sub(r"<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return text(body)


def rendered_text(url):
    """A page's text as headless Chrome draws it (its description filled in by script)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        try:
            pg = b.new_page(user_agent=UA["User-Agent"])
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(3500)                    # its script draws the content; analytics never go idle
            main = pg.query_selector("main") or pg.query_selector("body")
            return re.sub(r"\n\s*\n+", "\n\n", main.inner_text() if main else "").strip()
        finally:
            b.close()


def posting(url, fallback=None):
    """{company, title, location, url, jd} from a posting's JSON-LD JobPosting; with
    fallback={title, location}, a page without it is read as text under those."""
    try:
        h = requests.get(url, headers=UA, timeout=30).text
    except Exception:
        return None
    jp = _jsonld(h)
    if not jp:
        if fallback is None:
            return None
        body = page_text(h)
        if len(body) < 400:                              # drawn by script: read the rendered page
            try:
                body = rendered_text(url)
            except Exception:
                body = ""
        return {"company": "", "title": fallback.get("title", ""), "location": fallback.get("location", ""),
                "url": url, "jd": body[:12000]} if len(body) > 400 else None
    org = jp.get("hiringOrganization")
    remote = "Remote" if str(jp.get("jobLocationType", "")).upper() == "TELECOMMUTE" else ""
    return {"company": (org.get("name") if isinstance(org, dict) else org) or "",
            "title": jp.get("title") or "", "location": _place(jp.get("jobLocation")) or remote,
            "url": url, "jd": text(jp.get("description"))[:12000]}


def search(markets, url, company="", known=(), limit=60, location="", want=lambda title: True):
    """`location`: where the listing page was filtered to (e.g. "Dublin, Ireland"), used for a
    posting whose page does not say."""
    for link, label in job_links(url, limit):
        if not want(label):                              # a title intake would drop: not read
            continue
        if link in known:                                # its JD is in state.db already
            yield {"company": company, "title": label, "location": location, "url": link, "jd": ""}
            continue
        p = posting(link, fallback={"title": label, "location": location})
        if p:
            yield {**p, "company": p["company"] or company}
