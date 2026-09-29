"""Greenhouse — hosted boards and self-hosted embeds (?gh_jid= on a company domain)."""
import re
import urllib.parse

from jobpilot.apply.platforms._http import get, html_to_text

ID = "greenhouse"
HOSTS = ("greenhouse.io",)
ROUTE = "mobile"
# The application form is a single page. On a company domain it sits in an
# embedded iframe; see.form_frame looks for these first.
FRAME_PATTERNS = ("greenhouse.io/embed/job_app", "job-boards.greenhouse.io", "boards.greenhouse.io")
STANDARD = True                      # name, email, resume, EEO: shared across companies


def company_key(url):
    """The board token: job-boards.greenhouse.io/<board>/jobs/..., else the company domain."""
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?#]+)", url or "")
    if m and m.group(1) != "embed":
        return m.group(1)
    # the employer's own domain (careers.nebius.com/?gh_jid=...): its name, not "careers"
    from jobpilot.apply.platforms import slug_for_host
    return slug_for_host(urllib.parse.urlparse(url or "").netloc.lower().split(":")[0]) or None


def _board(url, job_id):
    """The Greenhouse board a self-hosted posting (?gh_jid= on the employer's domain) belongs
    to: the first guess the boards API knows this job under. (board, the job) or (None, None)."""
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?#]+)", url)
    if m and m.group(1) not in ("embed",):
        guesses = [m.group(1)]
    else:
        host = parsed.netloc.lower().replace("www.", "").replace("boards.", "")
        base = host.split(".")[0]
        guesses = [company_key(url), base, base.replace("-", ""), q.get("for", [""])[0]]
    for board in dict.fromkeys(g for g in guesses if g):
        try:
            return board, get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", as_json=True)
        except Exception:
            continue
    return None, None


def form_url(url):
    """A posting embedded on the employer's own site (careers.nebius.com/?gh_jid=...) opens
    Greenhouse's own form page instead: the site loads the posting by script and sometimes
    shows its job list instead; the embed page is always the form."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query)
    if "greenhouse.io" in (url or "") or not q.get("gh_jid"):
        return url
    board, _ = _board(url, q["gh_jid"][0])
    return f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={q['gh_jid'][0]}" if board else url


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
    # company's own domain (careers.nebius.com -> "nebius"), the convention for
    # self-hosted boards.
    board, d = _board(url, job_id)
    if d is None:
        return None
    return {
        "title": d.get("title", ""),
        "company": d.get("company_name") or board.replace("-", " ").title(),
        "location": (d.get("location") or {}).get("name", ""),
        "text": html_to_text(d.get("content", "")),
        "apply_url": d.get("absolute_url", url),
    }
