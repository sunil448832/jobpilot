#!/usr/bin/env python3
"""
scaffold.py — turn an external job URL into a scaffolded, buildable application folder.

Step 1 of the pipeline (see docs/original-plan.md). Fetches the JD, detects
the portal, scaffolds applications/<company>/ from the _template, and writes JD.md
with the full JD text so ats_score.py has real prose to score against.

Tailoring judgment (which project leads, how skills reorder) is deliberately NOT
done here — Claude does it in-session so the never-fabricate rule is enforced by
judgment rather than by a prompt string. This script does everything around it.

Usage:
    ./jobpilot apply https://job-boards.greenhouse.io/acme/jobs/123
    ./jobpilot apply <url> --company acme-mle     # override folder name
    ./jobpilot apply <url> --force                # overwrite existing folder
    python -m jobpilot.tailor.scaffold --build acme                 # build PDF + docx, then score
    python -m jobpilot.tailor.scaffold --score acme                 # score only
    python -m jobpilot.tailor.scaffold --list                       # show all applications
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

from bs4 import BeautifulSoup

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)
from jobpilot.apply import platforms  # noqa: E402   one registry: recognise a link, fetch its JD, route it
from jobpilot.apply.platforms._http import get as _get, html_to_text as _html_to_text  # noqa: E402
APPLICATIONS_DIR = APPLICATIONS
TEMPLATE_DIR = os.path.join(APPLICATIONS_DIR, "_template")


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def detect_portal(url):
    """The platform id for a link — a known module's name, or one derived from
    the host (src/platforms). Unknown platforms are explored generically."""
    return platforms.detect(url)[0]


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
    # A company careers site is often only a front: its Apply button goes to
    # the real platform (capitalonecareers.com -> Capital One's Workday).
    apply_links = []
    for a in soup.find_all("a", href=True):
        if re.search(r"\bapply\b", a.get_text(" ", strip=True), re.I):
            apply_links.append(urllib.parse.urljoin(url, a["href"]))
    return {
        "title": title,
        "company": company,
        "location": "",
        "text": _html_to_text(str(soup)),
        "apply_url": url,
        "apply_links": apply_links,
        "scraped": True,          # company and title read off the page: see main()
    }


def landing(url):
    """Where a link ends up after its redirects: an aggregator's Apply ("arbeitnow.ch/.../apply")
    is a redirect to the employer's own posting (careers.nebius.com/?gh_jid=...)."""
    import requests
    try:
        r = requests.get(url, allow_redirects=True, timeout=20, stream=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.close()
        return r.url or url
    except Exception:
        return url


def fetch_jd(url):
    portal = detect_portal(url)
    jd = None
    if getattr(platforms.get(portal), "fetch_jd", None):
        try:
            jd = platforms.fetch_jd(portal, url)
            if jd:
                print(f"  [fetch] {portal} API -> {len(jd['text'])} chars")
        except SystemExit:
            raise                                   # EXPIRED: the posting is gone
        except Exception as e:
            print(f"  [fetch] {portal} API failed ({e}); falling back to HTML")
    if jd is None:
        jd = fetch_generic(url)
        print(f"  [fetch] HTML scrape -> {len(jd['text'])} chars")
        if not platforms.get(portal):
            for link in (jd.get("apply_links") or [])[:6]:
                pid, known = platforms.detect(link)
                if not known:                            # an aggregator's own Apply redirects there
                    link = landing(link)
                    pid, known = platforms.detect(link)
                if known and not platforms.is_manual(pid):
                    print(f"  [fetch] the page's Apply goes to {pid}: {link[:90]}")
                    portal, jd["apply_url"] = pid, link
                    break
    jd.pop("apply_links", None)
    jd["portal"] = portal
    jd["url"] = url
    # A closed posting must not become a resume. TII's page said "the job you are
    # trying to apply for has been filled" and was scaffolded, built and scored
    # (6.6%) before anyone noticed.
    dead = re.search(r"has been filled|no longer accepting applications|no longer available|"
                     r"position (has been|is) closed|this job is closed|job (has )?expired|"
                     r"posting (has been )?removed|page not found", jd["text"][:3000], re.I)
    if dead and len(jd["text"]) < 1500:
        raise SystemExit(f"EXPIRED: posting is closed ({dead.group(0)!r}) — {url}")
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
    # Routing comes from the platform module (ROUTE); a platform nobody has
    # described yet is "review": explored generically, checked on the phone.
    route = platforms.route(portal)
    ats_critical = route in ("mobile", "desktop")

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
    # The application's exploration record (explore/record.py): exploration writes
    # each page's map and actions into it, submit replays them.
    from jobpilot.apply.explore_agentic import record
    record.ensure(company, jd["apply_url"], portal)
    print("  [scaffold] explore.json")
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
    ap.add_argument("--name", help="the employer, as the pipeline recorded it (used when the JD is a page scrape)")
    ap.add_argument("--title", help="the role, as the pipeline recorded it (used when the JD is a page scrape)")
    ap.add_argument("--location", help="the location, as the pipeline recorded it (used when the JD has none)")
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
    # A page scrape knows the site, not the employer: an aggregator's page gave "arbeitnow.ch"
    # as the company and "<role> – Nebius – Zürich | Arbeitnow" as the title. What the pipeline
    # recorded from the board's own data (--name / --title / --location) is right then.
    if jd.pop("scraped", False):
        jd["company"] = args.name or jd["company"]
        jd["title"] = args.title or jd["title"]
    jd["location"] = jd["location"] or args.location or ""
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
     {company}/sections/objective.tex   -> the JD's role title at his level ("Senior" at most, no
                                           Lead/Staff/Principal, no team suffix), its lead capabilities
     {company}/sections/skills.tex      -> JD priorities first, buckets in the JD's framing
     {company}/sections/experience.tex  -> reorder / reword bullets, same facts only
     {company}/sections/projects.tex    -> most relevant project first
     (resume.tex's location line is set from the market by code;
      ats.md is generated from sections/*.tex on build — do not edit it)
  3. python -m jobpilot.tailor.scaffold --build {company}

Aim for 75%+. Only add keywords that are TRUE.""")


if __name__ == "__main__":
    main()
