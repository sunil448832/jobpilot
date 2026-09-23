#!/usr/bin/env python3
"""
apply.py — turn an external job URL into a scaffolded, buildable application folder.

Step 1 of the pipeline (see README.md, appendix). Fetches the JD, detects
the portal, scaffolds applications/<company>/ from the _template, and writes JD.md
with the full JD text so ats_score.py has real prose to score against.

Tailoring judgment (which project leads, how skills reorder) is deliberately NOT
done here — Claude does it in-session so the never-fabricate rule is enforced by
judgment rather than by a prompt string. This script does everything around it.

Usage:
    python jobs/apply.py https://job-boards.greenhouse.io/acme/jobs/123
    python jobs/apply.py <url> --company acme-mle     # override folder name
    python jobs/apply.py <url> --force                # overwrite existing folder
    python jobs/apply.py --build acme                 # build PDF + docx, then score
    python jobs/apply.py --score acme                 # score only
    python jobs/apply.py --list                       # show all applications
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

import requests
from bs4 import BeautifulSoup

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, TRACKERS, MEMORY)
APPLICATIONS_DIR = APPLICATIONS
TEMPLATE_DIR = os.path.join(APPLICATIONS_DIR, "_template")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = 30

# Portals whose forms are usable on a phone vs. ones batched for desktop.
# Mirrors answers.yaml:portal_routing and automation-plan.md §1.
MOBILE_OK = {"greenhouse", "lever", "ashby"}
DESKTOP_ONLY = {"workday", "taleo", "icims", "successfactors"}


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def detect_portal(url):
    h = urllib.parse.urlparse(url).netloc.lower()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    # Many companies host a Greenhouse board on their own domain and pass the job
    # id as ?gh_jid= (e.g. databricks.com/...?gh_jid=8747605002).
    if "gh_jid" in q:               return "greenhouse"
    if "greenhouse.io" in h:        return "greenhouse"
    if "lever.co" in h:             return "lever"
    if "ashbyhq.com" in h:          return "ashby"
    if "myworkdayjobs.com" in h:    return "workday"
    if "taleo.net" in h:            return "taleo"
    if "icims.com" in h:            return "icims"
    if "successfactors" in h:       return "successfactors"
    if "linkedin.com" in h:         return "linkedin"
    if "wellfound.com" in h:        return "wellfound"
    return "unknown"


def _get(url, as_json=False):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() if as_json else r.text


def _html_to_text(markup):
    """HTML (possibly entity-encoded) -> readable plain text."""
    soup = BeautifulSoup(html.unescape(markup or ""), "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_greenhouse(url):
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)

    job_id = None
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
            d = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}",
                     as_json=True)
            break
        except Exception:
            continue
    if d is None:
        return None
    return {
        "title": d.get("title", ""),
        "company": (d.get("company_name")
                    or board.replace("-", " ").title()),
        "location": (d.get("location") or {}).get("name", ""),
        "text": _html_to_text(d.get("content", "")),
        "apply_url": d.get("absolute_url", url),
    }


def fetch_lever(url):
    m = re.search(r"lever\.co/([^/?#]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    co, post_id = m.group(1), m.group(2)
    d = _get(f"https://api.lever.co/v0/postings/{co}/{post_id}", as_json=True)
    cats = d.get("categories") or {}
    parts = [d.get("descriptionPlain", "")]
    for lst in d.get("lists") or []:
        parts.append(f"\n{lst.get('text','')}\n" + _html_to_text(lst.get("content", "")))
    parts.append(d.get("additionalPlain", ""))
    return {
        "title": d.get("text", ""),
        "company": co.replace("-", " ").title(),
        "location": cats.get("location", ""),
        "text": "\n".join(p for p in parts if p).strip(),
        "apply_url": d.get("hostedUrl", url),
    }


def fetch_ashby(url):
    m = re.search(r"ashbyhq\.com/([^/?#]+)(?:/([0-9a-f-]{36}))?", url)
    if not m:
        return None
    board, post_id = m.group(1), m.group(2)
    d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", as_json=True)
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
        "text": job.get("descriptionPlain") or _html_to_text(job.get("descriptionHtml", "")),
        "apply_url": job.get("jobUrl", url),
    }


def fetch_workday(url):
    """Workday exposes the posting as JSON under /wday/cxs/<tenant>/<site>/job/<path>."""
    p = urllib.parse.urlparse(url)
    m = re.match(r"^([^.]+)\.wd\d+\.myworkdayjobs\.com$", p.netloc.lower())
    if not m:
        return None
    tenant = m.group(1)
    segs = [s for s in p.path.split("/") if s]
    if "job" not in segs:
        return None
    i = segs.index("job")
    site = segs[i - 1] if i >= 1 else None
    if not site:
        return None
    api = f"https://{p.netloc}/wday/cxs/{tenant}/{site}/job/" + "/".join(segs[i + 1:])
    d = _get(api, as_json=True)
    info = d.get("jobPostingInfo") or {}
    return {
        "title": info.get("title", ""),
        "company": tenant.replace("-", " ").title(),
        "location": info.get("location", ""),
        "text": _html_to_text(info.get("jobDescription", "")),
        "apply_url": info.get("externalUrl", url),
    }


def fetch_generic(url):
    soup = BeautifulSoup(_get(url), "html.parser")
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)
    ogt = soup.find("meta", property="og:title")
    if ogt and ogt.get("content"):
        title = ogt["content"]
    ogs = soup.find("meta", property="og:site_name")
    company = ogs["content"] if ogs and ogs.get("content") else urllib.parse.urlparse(url).netloc
    return {
        "title": title,
        "company": company,
        "location": "",
        "text": _html_to_text(str(soup)),
        "apply_url": url,
    }


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
}


def fetch_jd(url):
    portal = detect_portal(url)
    jd = None
    fn = FETCHERS.get(portal)
    if fn:
        try:
            jd = fn(url)
            if jd:
                print(f"  [fetch] {portal} API -> {len(jd['text'])} chars")
        except Exception as e:
            print(f"  [fetch] {portal} API failed ({e}); falling back to HTML")
    if jd is None:
        jd = fetch_generic(url)
        print(f"  [fetch] HTML scrape -> {len(jd['text'])} chars")
    jd["portal"] = portal
    jd["url"] = url
    if len(jd["text"]) < 400:
        print("  [warn] JD text looks short — the page may be JS-rendered.")
        print("         Paste the full JD into JD.md by hand for an accurate score.")
    return jd


# --------------------------------------------------------------------------
# scaffolding
# --------------------------------------------------------------------------

def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def copy_sections(dest):
    """Fresh copy of the base resume's sections/ — the only thing the tailor edits."""
    src = os.path.join(RESUME, "sections")
    dst = os.path.join(dest, "sections")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.aux", "*.log", "*.out", "*.pdf"))


def set_location(dest, market):
    r"""Write the \location line for this role's market (targets.yaml). Code, not
    the LLM: with the score gate a role may never see an LLM session at all."""
    if not market:
        return
    import yaml
    t = yaml.safe_load(open(os.path.join(CONFIG, "targets.yaml")))
    line = next((m.get("resume_location_line") for m in t.get("markets", [])
                 if m.get("id") == market), None)
    if not line:
        return
    p = os.path.join(dest, "resume.tex")
    tex = open(p, encoding="utf-8").read()
    # "India | Open to relocation ... | Visa sponsorship required" -> TeX separators
    parts = [x.strip() for x in line.split("|")]
    texline = " \\hspace{.3em}$\\vert$\\hspace{.3em} ".join(parts)
    tex = re.sub(r"\\def\\location\{.*\}", lambda m: "\\def\\location{" + texline + "}", tex)
    open(p, "w", encoding="utf-8").write(tex)


def scaffold(company, jd, force=False, market=None):
    dest = os.path.join(APPLICATIONS_DIR, company)
    if os.path.isdir(dest):
        if not force:
            print(f"  [scaffold] {company}/ already exists — use --force to overwrite")
            return dest, False
        # Preserve built artifacts and JD.md; reset the tailoring inputs to the
        # current base resume so a re-tailor starts from today's truth.
        for f in ("resume.tex", "notes.md"):
            src = os.path.join(TEMPLATE_DIR, f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dest, f))
        copy_sections(dest)
        print(f"  [scaffold] reset sections/ + resume.tex in {company}/")
    else:
        shutil.copytree(TEMPLATE_DIR, dest)
        copy_sections(dest)
        print(f"  [scaffold] created applications/{company}/ with a copy of the base sections")

    portal = jd["portal"]
    route = ("mobile" if portal in MOBILE_OK
             else "desktop" if portal in DESKTOP_ONLY
             else "manual" if portal == "linkedin" else "review")
    ats_critical = portal in MOBILE_OK or portal in DESKTOP_ONLY

    with open(os.path.join(dest, "JD.md"), "w", encoding="utf-8") as f:
        f.write(f"""# Job Description

- **Company:** {jd['company']}
- **Role / Title:** {jd['title']}
- **Location:** {jd['location']}
- **Platform / How applying:** {portal}{' — ATS-critical, use the .docx' if ats_critical else ''}
- **Autofill route:** {route}
- **Visa sponsorship:** unclear — CHECK the JD text below
- **Salary range:**
- **Link:** {jd['url']}
- **Apply URL:** {jd['apply_url']}
- **Date applied:**

## Full JD text

{jd['text']}
""")
    print(f"  [scaffold] JD.md written ({len(jd['text'])} chars)")
    set_location(dest, market)
    return dest, True


# --------------------------------------------------------------------------
# build + score
# --------------------------------------------------------------------------

def run_build(company):
    print(f"  [build] building {company} ...")
    sys.stdout.flush()          # subprocess writes to the fd directly; keep order
    return subprocess.run([sys.executable, "-m", "jobpilot.tailor.build", company],
                          cwd=APPLICATIONS_DIR).returncode == 0


def run_score(company):
    sys.stdout.flush()
    proc = subprocess.run([sys.executable, "-m", "jobpilot.tailor.ats_score", "--company", company],
                          cwd=APPLICATIONS_DIR)
    return proc.returncode == 0


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="job posting URL")
    ap.add_argument("--company", help="folder name under applications/ (default: derived)")
    ap.add_argument("--market", help="market id from targets.yaml; sets the \\location line")
    ap.add_argument("--force", action="store_true", help="overwrite an existing folder")
    ap.add_argument("--build", metavar="COMPANY", help="build PDF + docx, then score")
    ap.add_argument("--score", metavar="COMPANY", help="score only")
    ap.add_argument("--list", action="store_true", help="list application folders")
    args = ap.parse_args()

    if args.list:
        for d in sorted(os.listdir(APPLICATIONS_DIR)):
            p = os.path.join(APPLICATIONS_DIR, d)
            if os.path.isdir(p) and d != "_template":
                built = "built" if any(x.endswith(".pdf") for x in os.listdir(p)) else "-"
                print(f"  {d:<28} {built}")
        return

    if args.score:
        sys.exit(0 if run_score(args.score) else 1)

    if args.build:
        ok = run_build(args.build)
        print()
        run_score(args.build)
        sys.exit(0 if ok else 1)

    if not args.url:
        ap.error("give a job URL, or use --build / --score / --list")

    print(f"Fetching: {args.url}")
    jd = fetch_jd(args.url)
    company = args.company or slugify(jd["company"]) or "unknown-company"
    print(f"  [jd] {jd['title']} @ {jd['company']} ({jd['location'] or 'location n/a'})")
    print(f"  [jd] portal={jd['portal']}  folder={company}")

    dest, fresh = scaffold(company, jd, force=args.force, market=args.market)
    if not fresh:
        sys.exit(1)

    print(f"""
Next — tailoring (Claude does this in-session, never fabricating):
  1. Read applications/{company}/JD.md and pull the exact keywords.
  2. Tailoring (only if the score is below pipeline.ats_target) edits, in place:
     {company}/sections/objective.tex   -> JD's exact job title, its lead capabilities
     {company}/sections/skills.tex      -> JD priorities first, buckets in the JD's framing
     {company}/sections/experience.tex  -> reorder / reword bullets, same facts only
     {company}/sections/projects.tex    -> most relevant project first
     (resume.tex's location line is set from the market by code;
      ats.md is generated from sections/*.tex on build — do not edit it)
  3. python jobs/apply.py --build {company}

Aim for 75%+. Only add keywords that are TRUE.""")


if __name__ == "__main__":
    main()
