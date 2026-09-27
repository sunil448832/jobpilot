"""Ashby — jobs.ashbyhq.com; the form lives at <posting>/application, one page.
Ashby parses the uploaded resume and REWRITES the form when done — exploration waits
for the page to go quiet (browser.wait_quiet) before mapping it."""
import re

from jobpilot.apply.platforms._http import get, html_to_text

ID = "ashby"
HOSTS = ("ashbyhq.com",)
ROUTE = "mobile"
FRAME_PATTERNS = ("jobs.ashbyhq.com", "ashbyhq.com/embed")
STANDARD = True


def company_key(url):
    m = re.search(r"ashbyhq\.com/([^/?#]+)", url or "")
    return m.group(1) if m else None


def form_url(url):
    """The posting page shows the JD; the form is the /application route."""
    u = (url or "").rstrip("/")
    return u if u.endswith("/application") else u + "/application"


def fetch_jd(url):
    m = re.search(r"ashbyhq\.com/([^/?#]+)(?:/([0-9a-f-]{36}))?", url)
    if not m:
        return None
    board, post_id = m.group(1), m.group(2)
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", as_json=True)
    jobs = d.get("jobs") or []
    job = next((x for x in jobs if x.get("id") == post_id), None) if post_id else None
    if job is None and jobs:
        job = jobs[0]
    if job is None:
        return None
    return {
        "title": job.get("title", ""),
        "company": board.replace("-", " ").title(),
        "location": job.get("location", ""),
        "text": job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml", "")),
        "apply_url": form_url(job.get("jobUrl", url)),
    }
