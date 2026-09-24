"""Lever — jobs.lever.co postings; the form is the posting URL + /apply, one page."""
import re

from jobpilot.fill.platforms._http import get, html_to_text

ID = "lever"
HOSTS = ("lever.co",)
ROUTE = "mobile"
FRAME_PATTERNS = ("jobs.lever.co",)
START = {"text": "Apply for this job"}
SUBMIT = {"text": "Submit application"}


def fetch_jd(url):
    m = re.search(r"lever\.co/([^/?#]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    co, post_id = m.group(1), m.group(2)
    d = get(f"https://api.lever.co/v0/postings/{co}/{post_id}", as_json=True)
    cats = d.get("categories") or {}
    parts = [d.get("descriptionPlain", "")]
    for lst in d.get("lists") or []:
        parts.append(f"\n{lst.get('text','')}\n" + html_to_text(lst.get("content", "")))
    parts.append(d.get("additionalPlain", ""))
    return {
        "title": d.get("text", ""),
        "company": co.replace("-", " ").title(),
        "location": cats.get("location", ""),
        "text": "\n".join(p for p in parts if p).strip(),
        "apply_url": d.get("hostedUrl", url),
    }
