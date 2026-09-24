"""Greenhouse — hosted boards and self-hosted embeds (?gh_jid= on a company domain)."""
import re
import urllib.parse

from jobpilot.fill.platforms._http import get, html_to_text

ID = "greenhouse"
HOSTS = ("greenhouse.io",)
ROUTE = "mobile"
# The application form is a single page. On a company domain it sits in an
# embedded iframe; the walker looks for these before the page's own controls.
FRAME_PATTERNS = ("greenhouse.io/embed/job_app", "job-boards.greenhouse.io", "boards.greenhouse.io")
SUBMIT = {"text": "Submit application"}


def match_url(url):
    return "gh_jid" in urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def fetch_jd(url):
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    if q.get("gh_jid"):
        job_id = q["gh_jid"][0]
    else:
        j = re.search(r"/jobs/(\d+)", url)
        job_id = j.group(1) if j else None
    if not job_id:
        return None
    # Board token: explicit in a greenhouse.io URL, otherwise guessed from the
    # company's own domain (databricks.com -> "databricks"), which is the
    # convention for self-hosted boards.
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?#]+)", url)
    if m and m.group(1) not in ("embed",):
        candidates = [m.group(1)]
    else:
        host = parsed.netloc.lower().replace("www.", "").replace("boards.", "")
        base = host.split(".")[0]
        candidates = [base, base.replace("-", ""), q.get("for", [""])[0]]
    d = None
    for board in [c for c in candidates if c]:
        try:
            d = get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", as_json=True)
            break
        except Exception:
            continue
    if d is None:
        return None
    return {
        "title": d.get("title", ""),
        "company": d.get("company_name") or board.replace("-", " ").title(),
        "location": (d.get("location") or {}).get("name", ""),
        "text": html_to_text(d.get("content", "")),
        "apply_url": d.get("absolute_url", url),
    }
