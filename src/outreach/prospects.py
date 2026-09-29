#!/usr/bin/env python3
"""
prospects.py — find NEW people to connect with at a company (Phase 5, §5b).

referrals.py only covers people already in your network. When you know nobody at
a target company, this finds who to reach out to — WITHOUT scraping LinkedIn
people-search, which is the single most heavily detected behaviour there and
would risk the one channel that has actually converted for you.

Two routes:
  1. Build the URL, let LinkedIn do the search. Its own alumni and company-people
     tools apply your network graph, so they beat any scraper. Tap and go.
  2. Named candidates from public, non-LinkedIn sources: the company's GitHub org
     (real engineers, with visible evidence of their work) and the recruiter or
     hiring manager named in the job description.

Sending is NEVER automated. This prints who to contact and what to say.

Usage:
    python jobs/prospects.py --company Databricks
    python jobs/prospects.py --company OpenAI --github openai
    python jobs/prospects.py --queue          # every company in the queue
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

import requests
import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
SCHOOL = "indian-institute-of-technology-jodhpur"
PAST_EMPLOYER = "Amazon"

# Ranked by accept-likelihood x referral value. Shared context beats seniority:
# an alum who shares your school accepts far more often than a senior stranger.
# `where` is the JOB's location. A referral only carries weight inside the office
# that owns the requisition — someone at Databricks India cannot help with an
# Amsterdam req, however senior they are. So every people-search is scoped to the
# hiring location, which is also what stops results filling up with the company's
# largest office instead of the relevant one.
ANGLES = [
    ("IIT Jodhpur alumni AT THAT OFFICE",
     "Highest accept rate you have — shared school, and they are already local.",
     lambda co, where: f"https://www.linkedin.com/school/{SCHOOL}/people/"
                       f"?keywords={q(co + ' ' + where)}"),
    ("Ex-Amazon people at that office",
     "Shared employer, instant credibility, and they know your bar.",
     lambda co, where: "https://www.linkedin.com/search/results/people/?keywords="
                       f"{q(PAST_EMPLOYER + ' ' + co + ' ' + where)}"),
    ("ML / AI engineers IN THAT OFFICE",
     "An engineer on the team beats a recruiter: referral bonus + internal weight. "
     "Scoped to the hiring location so it is people who can actually refer you.",
     lambda co, where: "https://www.linkedin.com/search/results/people/?keywords="
                       f"{q(co + ' machine learning engineer ' + where)}"),
    ("Hiring managers for AI/ML there",
     "Owns the requisition. Worth one well-written note.",
     lambda co, where: "https://www.linkedin.com/search/results/people/?keywords="
                       f"{q(co + ' engineering manager machine learning ' + where)}"),
    ("Technical recruiters for that region",
     "Lowest conversion of the five — use only if the others turn up nothing.",
     lambda co, where: "https://www.linkedin.com/search/results/people/?keywords="
                       f"{q(co + ' technical recruiter AI ' + where)}"),
]


def hiring_place(location):
    """City, else country, from the posting's location string."""
    loc = (location or "").strip()
    if not loc:
        return ""
    parts = [p.strip() for p in re.split(r"[;|]", loc)[0].split(",") if p.strip()]
    if not parts:
        return ""
    city = parts[0]
    country = parts[-1] if len(parts) > 1 else ""
    # "Remote - India" / "Remote-NORAM": strip the Remote prefix, keep the place.
    m = re.match(r"remote\s*[-–—:]\s*(.+)", city, re.I)
    if m:
        return m.group(1).strip()
    if city.lower() in ("remote", "anywhere", "worldwide", "global"):
        return country or ""
    return f"{city} {country}".strip() if country and country != city else city


def q(s):
    return urllib.parse.quote_plus(s)


def github_people(org, limit=12):
    """Public org members — real engineers, with evidence of what they work on."""
    out = []
    try:
        r = requests.get(f"https://api.github.com/orgs/{org}/members?per_page={limit}",
                         headers={"User-Agent": UA, "Accept": "application/vnd.github+json"},
                         timeout=20)
        if r.status_code != 200:
            return out, f"github: {r.status_code} ({'rate limited' if r.status_code == 403 else 'no public members'})"
        for m in r.json():
            try:
                u = requests.get(m["url"], headers={"User-Agent": UA}, timeout=15).json()
            except Exception:
                u = {}
            out.append({"login": m["login"], "name": u.get("name") or m["login"],
                        "bio": (u.get("bio") or "")[:90],
                        "url": m["html_url"], "blog": u.get("blog") or ""})
        return out, f"github: {len(out)} public members of {org}"
    except Exception as e:
        return out, f"github: {type(e).__name__}"


OPENALEX_UA = {"User-Agent": "jobbot/1.0 (mailto:sunil4832sharma@gmail.com)"}
AI_QUERY = ("large language model OR generative AI OR machine learning OR "
            "neural network OR computer vision OR retrieval augmented")


def openalex_people(company, limit=12, since="2025-01-01"):
    """Named researchers at a company, from published work.

    The strongest cold-outreach angle there is: you can open with a specific
    remark about their actual paper rather than a generic connection note. Works
    especially well for research-heavy targets — TII (Falcon), MBZUAI, G42.
    """
    try:
        r = requests.get("https://api.openalex.org/institutions",
                         params={"search": company, "per-page": 3},
                         headers=OPENALEX_UA, timeout=25).json()
        results = r.get("results") or []
        if not results:
            return [], f"openalex: no institution matching '{company}'"
        inst = results[0]
        iid = inst["id"].split("/")[-1]
        w = requests.get("https://api.openalex.org/works", headers=OPENALEX_UA, timeout=30,
                         params={"filter": f"institutions.id:{iid},"
                                           f"from_publication_date:{since},"
                                           f"title_and_abstract.search:{AI_QUERY}",
                                 "per-page": 50, "sort": "publication_date:desc",
                                 "select": "title,publication_year,authorships,doi"}).json()
        works = w.get("results") or []
        people = {}
        for work in works:
            for a in work.get("authorships", []):
                if not any(i.get("id", "").endswith(iid) for i in a.get("institutions", [])):
                    continue
                nm = a["author"]["display_name"]
                rec = people.setdefault(nm, {"name": nm, "papers": [], "n": 0,
                                             "oa": a["author"].get("id", "")})
                rec["n"] += 1
                if len(rec["papers"]) < 2:
                    rec["papers"].append(work["title"])
        ranked = sorted(people.values(), key=lambda x: -x["n"])[:limit]
        return ranked, (f"openalex: {len(works)} AI papers from "
                        f"{inst['display_name']} since {since}")
    except Exception as e:
        return [], f"openalex: {type(e).__name__}"


def github_commit_authors(org, limit=10):
    """Fallback when an org hides its members: who commits to its public repos."""
    out = {}
    try:
        repos = requests.get(f"https://api.github.com/orgs/{org}/repos",
                             params={"sort": "pushed", "per_page": 5},
                             headers={"User-Agent": UA}, timeout=20).json()
        if not isinstance(repos, list):
            return [], "github: no public repos"
        for repo in repos[:4]:
            cs = requests.get(f"https://api.github.com/repos/{org}/{repo['name']}/commits",
                              params={"per_page": 25},
                              headers={"User-Agent": UA}, timeout=20).json()
            if not isinstance(cs, list):
                continue
            for c in cs:
                a = c.get("author") or {}
                nm = (c.get("commit", {}).get("author", {}) or {}).get("name")
                if a.get("login") and nm:
                    out.setdefault(a["login"], {"name": nm, "login": a["login"],
                                                "url": a["html_url"], "commits": 0})
                    out[a["login"]]["commits"] += 1
        top = sorted(out.values(), key=lambda x: -x["commits"])[:limit]
        return top, f"github: {len(top)} active committers on {org} repos"
    except Exception as e:
        return [], f"github: {type(e).__name__}"


def recruiter_from_jd(jd):
    """Some postings name the recruiter or hiring manager outright."""
    hits = []
    for rx in (r"(?:recruiter|hiring manager|contact)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)",
               r"reach out to ([A-Z][a-z]+ [A-Z][a-z]+)",
               r"([A-Z][a-z]+ [A-Z][a-z]+)[,\s]+(?:Technical )?Recruiter"):
        hits += re.findall(rx, jd or "")
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:3]


def note_for(company, role=None):
    """A 300-char connection note. LinkedIn caps it there."""
    r = f" for the {role}" if role else ""
    n = (f"Hi — I'm an ML engineer (ex-Amazon Applied Scientist, M.Tech AI from "
         f"IIT Jodhpur) working on production LLM agents and evaluation systems. "
         f"I'm exploring {company}{r} and would value connecting with someone "
         f"doing this work there.")
    return n[:300]


def existing_connections(company):
    """People already in his network there. A warm intro beats every cold angle,
    so this leads the report whenever the LinkedIn export is installed."""
    try:
        from jobpilot.outreach import referrals
        conns = referrals.load_connections()
        if not conns:
            return []
        found = referrals.match(conns, [company])
        return found.get(company, [])[:6]
    except Exception:
        return []


def report(company, jd="", github_org=None, location=""):
    where = hiring_place(location)
    head = f"{company}" + (f"  —  hiring in {where}" if where else "")
    print(f"\n{'=' * 74}\n  {head}\n{'=' * 74}")

    warm = existing_connections(company)
    if warm:
        print(f"\n  ★ YOU ALREADY KNOW {len(warm)} PERSON(S) HERE — start with these\n")
        for pts, why, c in warm:
            print(f"    [{pts:3}] {c['first']} {c['last']:<20} {c['position'][:40]:<42} {why}")
            print(f"          {c['url']}")
        print("\n    A referral from one of these beats every cold angle below.")
    if not where:
        print("  (no location known — searches are unscoped; pass --location)")
    print("\n  ROUTE 1 — tap these; LinkedIn searches with your network graph applied\n")
    for i, (title, why, url) in enumerate(ANGLES, 1):
        print(f"  {i}. {title}\n     {why}\n     {url(company, where)}\n")

    print("  ROUTE 2 — named people from public, non-LinkedIn sources\n")
    names = recruiter_from_jd(jd)
    if names:
        for n in names:
            print(f"     JD names: {n}")
    if github_org:
        people, note = github_people(github_org)
        if not people:
            people, note = github_commit_authors(github_org)
        print(f"     {note}")
        for p in people[:8]:
            extra = f" — {p.get('bio') or ''}" if p.get("bio") else ""
            print(f"       {p['name'][:26]:<28} {p.get('url','')}{extra[:50]}")

    papers, pnote = openalex_people(company)
    print(f"\n     {pnote}")
    for p in papers[:8]:
        print(f"       {p['name'][:26]:<28} {p['n']:>2} AI paper(s)")
        if p["papers"]:
            print(f"         \u21b3 {p['papers'][0][:78]}")
    if papers:
        print("\n     -> reach these out on a PAPER, not a generic note:")
        print("        python jobs/outreach.py --company "
              f"'{company}' --kind paper --name '<name>' --paper '<title>'")

    print(f"\n  CONNECTION NOTE (300 char cap, copy as-is):\n")
    print(f"     {note_for(company)}\n")
    print("  Send by hand. Automated invites and DMs are what get accounts restricted —")
    print("  and LinkedIn is the only channel that has converted for you.\n")
    print("  Volume: ~100 invites/week cap, and ignored invites hurt. Aim 5-10 good ones.")
    print("  A bare request often out-accepts one with a note — then message after they accept.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company")
    ap.add_argument("--github", help="the company's GitHub org, e.g. openai")
    ap.add_argument("--location", default="", help="override the hiring location")
    ap.add_argument("--queue", action="store_true")
    a = ap.parse_args()

    if a.queue:
        import sqlite3
        con = sqlite3.connect(os.path.join(DATA, "state.db"))
        rows = con.execute("SELECT DISTINCT company, jd, location FROM jobs "
                           "WHERE market != 'usa' AND score >= 50 LIMIT 8").fetchall()
        for co, jd, loc in rows:
            report(co, jd or "", location=loc or "")
        return
    if not a.company:
        ap.error("give --company or --queue")

    jd = ""
    try:
        import sqlite3
        con = sqlite3.connect(os.path.join(DATA, "state.db"))
        row = con.execute("SELECT jd, location FROM jobs WHERE company LIKE ? "
                          "ORDER BY score DESC LIMIT 1", (f"%{a.company}%",)).fetchone()
        jd, loc = (row or ["", ""])[0] or "", (row or ["", ""])[1] or ""
    except Exception:
        loc = ""
    report(a.company, jd, a.github, a.location or loc)


if __name__ == "__main__":
    main()
