#!/usr/bin/env python3
"""
intake.py — passive job discovery (Phase 4).

Pulls postings from company job-board APIs and open aggregators, applies the
hard filters in targets.yaml, dedupes against a local SQLite store, and leaves
new matches for rank.py. Runs unattended, so mornings cost nothing.

Sources, all public JSON and TOS-clean — no LinkedIn scraping:
  Greenhouse  boards-api.greenhouse.io/v1/boards/<board>/jobs
  Lever       api.lever.co/v0/postings/<board>
  Ashby       api.ashbyhq.com/posting-api/job-board/<board>
  Remotive    remotive.com/api/remote-jobs
  Arbeitnow   arbeitnow.com/api/job-board-api
  Himalayas   himalayas.app/jobs/api

Usage:
    python jobs/intake.py --discover      # probe which board slugs exist -> boards.yaml
    python jobs/intake.py                 # pull, filter, store new jobs
    python jobs/intake.py --new           # list jobs found but not yet acted on
    python jobs/intake.py --stats
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)
DB_PATH = os.path.join(DATA, "state.db")
BOARDS_PATH = os.path.join(CONFIG, "boards.yaml")
from jobpilot.core.config import cfg  # noqa: E402
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Companies worth probing: AI labs and infra, remote-first employers, and firms
# with real presence in Sunil's target markets (NL / UAE / KSA / AU / US).
CANDIDATES = """
anthropic openai cohere huggingface scaleai databricks weightsandbiases together
modal replicate perplexityai mistral runwayml elevenlabs deepgram assemblyai
pinecone weaviate qdrant langchain llamaindex anysphere sierra harvey abridge
snowflake confluent elastic mongodb datadog cloudflare gitlab hashicorp grafana
stripe twilio shopify spotify klarna revolut wise n26 adyen mollie booking
tomtom picnic messagebird backbase framer miro
canva atlassian safetyculture cultureamp airwallex
careem talabat tabby propertyfinder swvl bayut
automattic zapier doist deel oyster remotecom toptal gitpod supabase vercel
netlify render fly digitalocean linear notion figma airtable retool

# --- aimed at Sunil's actual priority markets, not US AI labs ---
# Gulf (UAE / Saudi) — priority 2, sponsorship is the norm
g42 tii mbzuai core42 presight bayanat aramco stc sdaia neom alinma tabby
talabat careem swvl kitopi property-finder propertyfinder emiratesnbd mashreq
dubizzle noon alef anghami
# Netherlands — priority 1, fastest sponsorship route
booking bookingcom picnic backbase tomtom mollie messagebird channable
bynder catawiki takeaway justeattakeaway optiver imc flowtraders databricks
elevenlabs framer nedap sendcloud bird
# Australia — priority 3
canva atlassian safetycultureaustralia safetyculture eucalyptus immutable
airtree octopusdeploy linktree employmenthero
# remote-first, pays foreign currency from India
gitlab automattic hotjar buffer aha invisible turing andela crossover

# --- Gulf-native employers: the #2 sponsorship market, missed entirely by the
# first sweep because none of them use Greenhouse/Lever/Ashby ---
tii technologyinnovationinstitute g42 core42 presight khazna inceptioniai
mbzuai m42 bayanat edge edgegroup aramco saudiaramco stc solutionsbystc
sdaia neom redsea alat lucidmotors tabby tamara hala floward
talabat deliveryhero careem chalhoubgroup majidalfuttaim emaar dubaiholding
adnoc mubadala firstabudhabibank emiratesnbd mashreqbank aldar agthia
etisalat eand smartdubai dewa moroagency alfuttaim landmarkgroup
kitopi trukker fetchr yallacompare sarwa baraka huspy propertyfinder
"""
CANDIDATES = [w for line in CANDIDATES.splitlines()
              for w in line.split("#")[0].split()
              if re.fullmatch(r"[a-z0-9][a-z0-9-]{2,}", w)]
CANDIDATES = list(dict.fromkeys(CANDIDATES))     # a slug listed twice probed twice


# --------------------------------------------------------------------------

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS jobs(
        key TEXT PRIMARY KEY, source TEXT, board TEXT, company TEXT, title TEXT,
        location TEXT, url TEXT, remote INTEGER, posted TEXT, seen TEXT,
        score REAL, market TEXT, route TEXT, status TEXT, reason TEXT, jd TEXT)""")
    # added later; ALTER is a no-op once it exists
    for col in ("yc TEXT", "board_size INTEGER", "team_size INTEGER"):
        try:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    con.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)")
    return con


def get(url, timeout=25):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"},
                     timeout=timeout)
    r.raise_for_status()
    return r.json()


def key_for(company, title, location):
    raw = f"{company}|{title}|{location}".lower()
    return re.sub(r"[^a-z0-9|]+", "-", raw)[:200]


# ------------------------------------------------------------------ discover

def probe(slug):
    """Return (kind, count) for whichever board API answers for this slug.

    Greenhouse/Lever/Ashby are US-tech defaults. Gulf, Dutch and EU employers
    lean on SmartRecruiters, Workable and Recruitee — all with free public JSON —
    which is why the first sweep found almost nothing in the UAE or Saudi.
    """
    for kind, url in (
        ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"),
        ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json"),
        ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}"),
        ("smartrecruiters",
         f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"),
        ("workable",
         f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"),
        ("recruitee", f"https://{slug}.recruitee.com/api/offers/"),
    ):
        try:
            d = get(url, timeout=12)
        except Exception:
            continue
        # Lever returns a bare list; Greenhouse/Ashby wrap it in {"jobs": [...]}.
        if isinstance(d, list):
            n = len(d)
        elif isinstance(d, dict):
            n = len(d.get("jobs") or d.get("content") or d.get("offers") or [])
        else:
            n = 0
        if n:
            return kind, n
    return None, 0


def discover(workers=12):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    found = {"greenhouse": [], "lever": [], "ashby": [],
             "smartrecruiters": [], "workable": [], "recruitee": []}
    print(f"  probing {len(CANDIDATES)} slugs x 6 board APIs "
          f"({workers} in parallel) ...")
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe, slug): slug for slug in CANDIDATES}
        for fut in as_completed(futs):
            slug = futs[fut]
            done += 1
            try:
                kind, n = fut.result()
            except Exception:
                continue
            if kind:
                found[kind].append({"slug": slug, "jobs": n})
                print(f"    [{done:3}/{len(CANDIDATES)}] {slug:<22} {kind:<16} {n} jobs")
    # merge with what is already known so a re-run never loses a board
    prev = load_boards()
    for kind in found:
        have = {b["slug"] for b in found[kind]}
        for b in prev.get(kind, []):
            if b["slug"] not in have:
                found[kind].append(b)
        seen = set()
        uniq = []
        for b in found[kind]:
            if b["slug"] not in seen:
                seen.add(b["slug"])
                uniq.append(b)
        found[kind] = sorted(uniq, key=lambda b: b["slug"])
    with open(BOARDS_PATH, "w") as f:
        yaml.safe_dump(found, f, sort_keys=False)
    total = sum(len(v) for v in found.values())
    print(f"\n  {total} live boards -> {BOARDS_PATH}")


def load_boards():
    """Registered boards, minus the ones that are not employers.

    A board slug that resolves is not proof of a company. `agency` returns 829
    "reqs" that are all "<Subject> Specialist - Freelance AI Trainer Project" —
    a gig marketplace, not a hiring team — and its req count cleared the
    enterprise tier in scale_tiers, so every one of those gigs collected the
    largest company-scale bonus in rank.py. Excluded at the source rather than
    penalised later, so they never enter state.db at all.
    """
    if not os.path.isfile(BOARDS_PATH):
        return {}
    with open(BOARDS_PATH) as f:
        d = yaml.safe_load(f) or {}
    deny = {x.lower() for x in (cfg("discovery.deny_boards", []) or [])}
    if deny:
        for plat in list(d):
            d[plat] = [b for b in d[plat]
                       if (b.get("slug") or "").lower() not in deny]
            if not d[plat]:
                del d[plat]
    return d


# -------------------------------------------------------------------- pulling

def from_greenhouse(slug):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    for j in d.get("jobs", []):
        yield {"source": "greenhouse", "board": slug,
               "company": slug.replace("-", " ").title(), "title": j.get("title", ""),
               "location": (j.get("location") or {}).get("name", ""),
               "url": j.get("absolute_url", ""), "jd": j.get("content", "")[:12000]}


def from_lever(slug):
    for j in get(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
        yield {"source": "lever", "board": slug,
               "company": slug.replace("-", " ").title(), "title": j.get("text", ""),
               "location": (j.get("categories") or {}).get("location", ""),
               "url": j.get("hostedUrl", ""), "jd": (j.get("descriptionPlain") or "")[:12000]}


def from_ashby(slug):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    for j in d.get("jobs", []):
        yield {"source": "ashby", "board": slug,
               "company": slug.replace("-", " ").title(), "title": j.get("title", ""),
               "location": j.get("location", ""), "url": j.get("jobUrl", ""),
               "jd": (j.get("descriptionPlain") or "")[:12000]}


def from_smartrecruiters(slug):
    d = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    posts = []
    for j in d.get("content", []):
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        if loc.get("remote"):
            where += " Remote"
        url = ((j.get("ref") or "").replace("api.smartrecruiters.com/v1", "jobs.smartrecruiters.com")
               or f"https://jobs.smartrecruiters.com/{slug}/{j['id']}")
        posts.append((j, where, url))

    def detail(item):
        j, where, url = item
        if url in KNOWN_JD:                       # fetched on an earlier scan
            return KNOWN_JD[url]
        if not _want(j.get("name", "")):          # never going to be kept: no request
            return ""
        try:
            full = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j['id']}")
            secs = (full.get("jobAd") or {}).get("sections") or {}
            return _html_to_text(" ".join((secs.get(k) or {}).get("text", "")
                                          for k in ("companyDescription", "jobDescription", "qualifications")))[:12000]
        except Exception:
            return ""
    for (j, where, url), jd in zip(posts, _detail_pool(posts, detail)):
        yield {"source": "smartrecruiters", "board": slug,
               "company": (j.get("company") or {}).get("name") or slug.title(),
               "title": j.get("name", ""), "location": where, "url": url, "jd": jd}


def from_workday(entry):
    """Workday tenant: POST /wday/cxs/<tenant>/<site>/jobs pages 20 at a time;
    the description comes from one GET per posting, only for wanted titles."""
    tenant, host, site = entry["slug"], entry["host"], entry["site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    hdr = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    posts, offset, total = [], 0, None
    while offset < cfg("discovery.workday_max", 600):
        r = requests.post(f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                                 "searchText": ""}, headers=hdr, timeout=25)
        r.raise_for_status()
        d = r.json()
        items = d.get("jobPostings") or []
        posts.extend(items)
        if total is None:
            total = d.get("total") or 0           # only the FIRST page carries the count
        offset += 20
        if not items or offset >= total:
            break
    company = entry.get("company") or tenant.replace("-", " ").title()

    def url_of(j):
        return f"https://{host}/{site}{j.get('externalPath') or ''}"

    def detail(j):
        url = url_of(j)
        if url in KNOWN_JD:
            return KNOWN_JD[url]
        if not _want(j.get("title", "")):
            return ""
        try:
            info = get(f"{base}{j.get('externalPath') or ''}").get("jobPostingInfo") or {}
            return _html_to_text(info.get("jobDescription") or "")[:12000]
        except Exception:
            return ""
    for j, jd in zip(posts, _detail_pool(posts, detail)):
        yield {"source": "workday", "board": tenant, "company": company,
               "title": j.get("title", ""), "location": j.get("locationsText") or "",
               "url": url_of(j), "jd": jd}


def from_workable(slug):
    d = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    for j in d.get("jobs", []):
        yield {"source": "workable", "board": slug,
               "company": d.get("name") or slug.title(), "title": j.get("title", ""),
               "location": ", ".join(x for x in (j.get("city"), j.get("country")) if x)
                           + (" Remote" if j.get("telecommuting") else ""),
               "url": j.get("url") or j.get("shortlink", ""),
               "jd": _html_to_text((j.get("description") or "")
                                   + (j.get("requirements") or ""))[:12000]}


def from_recruitee(slug):
    d = get(f"https://{slug}.recruitee.com/api/offers/")
    for j in d.get("offers", []):
        yield {"source": "recruitee", "board": slug,
               "company": j.get("company_name") or slug.title(),
               "title": j.get("title", ""),
               "location": j.get("location") or j.get("city") or "",
               "url": j.get("careers_url") or j.get("careers_apply_url", ""),
               "jd": _html_to_text((j.get("description") or "")
                                   + (j.get("requirements") or ""))[:12000]}


def _html_to_text(markup):
    import html as _h
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_h.unescape(markup or ""), "html.parser")
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()


# Populated by pull() before fetching: url -> stored JD. The two fetchers that
# need one extra request PER posting (SmartRecruiters, Phenom) consult this so a
# description is fetched once, ever, not every eight hours.
KNOWN_JD = {}
_TARGETS = None


def _want(title):
    """Title gate applied BEFORE any per-posting detail request. The pipeline
    keeps ~2% of postings by title; fetching descriptions for the other 98% was
    the entire cost of intake (Philips: 200 pages, 311 s, for zero matches)."""
    global _TARGETS
    if _TARGETS is None:
        _TARGETS = load_targets()
    return title_ok(title or "", _TARGETS)


def _detail_pool(items, fn, workers=6):
    """Run the remaining detail fetches for one board concurrently."""
    from concurrent.futures import ThreadPoolExecutor
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


def phenom_full_jd(url):
    """Phenom job pages embed the complete description in JSON-LD."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(tag.string or "{}")
            except Exception:
                continue
            if isinstance(d, dict) and d.get("description"):
                return _html_to_text(d["description"])
    except Exception:
        pass
    return ""


def from_phenom(entry):
    """Phenom People — what most Gulf and enterprise careers sites actually run.

    Reached via the company's OWN careers domain, which is the canonical source:
    it carries requisitions that never reach a job board at all.
    """
    from jobpilot.discover import careers as _c
    base = entry["base"] if isinstance(entry, dict) else entry
    company = urllib.parse.urlparse(base).netloc.replace("careers.", "").split(".")[0]
    for start in (0, 100):
        try:
            jobs = _c.phenom_fetch(base, start=start, size=100)
        except Exception:
            break
        if not jobs:
            break
        entries = []
        for j in jobs:
            # Phenom sites differ: some key job pages off jobId under the site's
            # own locale path (TII: /us/en/job/5398/Title), others off jobSeqNo
            # under /global/en (G42). Build both and use whichever resolves.
            slug = re.sub(r"[^A-Za-z0-9]+", "-", j.get("title", "")).strip("-")
            b = base.rstrip("/")
            loc = (j.get("locale") or "en_US").lower().replace("_", "/")
            cands = [j.get("applyUrl") or "", j.get("jobUrl") or ""]
            if j.get("jobId"):
                cands.append(f"{b}/{loc.split('/')[-1]}/en/job/{j['jobId']}/{slug}")
                cands.append(f"{b}/us/en/job/{j['jobId']}/{slug}")
            if j.get("jobSeqNo"):
                cands.append(f"{b}/global/en/job/{j['jobSeqNo']}/{slug}")
            url = next((c for c in cands if c), "")
            entries.append((j, cands, url))
        # The search API returns only a ~300-char teaser, which systematically
        # under-scores these roles against boards that return the full text. The
        # job page carries the whole description in JSON-LD — but only fetch it
        # for postings the title gate would keep, and never twice for one URL.
        def detail(item):
            j, cands, url = item
            teaser = (j.get("descriptionTeaser") or "")
            for c in cands:
                if c in KNOWN_JD:
                    return KNOWN_JD[c], c
            if not _want(j.get("title", "")):
                return teaser, url
            for cand in [c for c in cands if c]:
                full = phenom_full_jd(cand)
                if len(full) > len(teaser):
                    return full, cand
            return teaser, url
        for (j, cands, url), (jd, url2) in zip(entries, _detail_pool(entries, detail)):
            yield {"source": "phenom", "board": company,
                   "company": (j.get("brand") or company).title(),
                   "title": j.get("title", ""),
                   "location": j.get("location") or j.get("cityStateCountry")
                               or j.get("cityState") or j.get("country", ""),
                   "url": url2 or url,
                   "jd": (jd or "")[:12000]}
        if len(jobs) < 100:
            break


def from_aggregators():
    try:
        for j in get("https://remotive.com/api/remote-jobs?category=software-dev&limit=200").get("jobs", []):
            yield {"source": "remotive", "board": "remotive",
                   "company": j.get("company_name", ""), "title": j.get("title", ""),
                   "location": j.get("candidate_required_location", "Remote"),
                   "url": j.get("url", ""), "jd": (j.get("description") or "")[:12000]}
    except Exception as e:
        print(f"    [warn] remotive: {e}")
    try:
        for j in get("https://www.arbeitnow.com/api/job-board-api").get("data", []):
            yield {"source": "arbeitnow", "board": "arbeitnow",
                   "company": j.get("company_name", ""), "title": j.get("title", ""),
                   "location": j.get("location", "") + (" Remote" if j.get("remote") else ""),
                   "url": j.get("url", ""), "jd": (j.get("description") or "")[:12000]}
    except Exception as e:
        print(f"    [warn] arbeitnow: {e}")


# -------------------------------------------------------------------- filters

def load_targets():
    with open(os.path.join(CONFIG, "targets.yaml")) as f:
        return yaml.safe_load(f)


# Substring matching let "AI Engineering" satisfy "AI Engineer", pulling in Ruby
# backend roles. Match on word boundaries instead.
def _has(phrase, text):
    return re.search(r"\b" + re.escape(phrase.lower()) + r"\b", text) is not None


NOT_ML = ("backend engineer", "frontend engineer", "fullstack", "full stack",
          "ruby", "android", "ios engineer", "site reliability", "security engineer",
          "support engineer", "solutions architect", "account", "technical writer")


def title_ok(title, t):
    tl = (title or "").lower()
    if any(_has(a, tl) for a in t["titles"]["avoid"]):
        return False
    if any(_has(a, tl) for a in t["seniority"]["avoid"]):
        return False
    if any(n in tl for n in NOT_ML):
        return False
    wanted = t["titles"]["primary"] + t["titles"]["also_consider"]
    return any(_has(w, tl) for w in wanted)


MARKET_HINTS = {
    "netherlands": ["netherlands", "amsterdam", "eindhoven", "utrecht", "rotterdam", "hague"],
    "ireland": ["ireland", "dublin", "cork", "galway", "limerick"],
    "germany": ["germany", "berlin", "munich", "münchen", "hamburg", "karlsruhe",
                "heidelberg", "cologne", "köln", "frankfurt", "stuttgart"],
    "luxembourg": ["luxembourg", "esch-sur-alzette", "belval", "kirchberg"],
    "uae": ["united arab emirates", "uae", "dubai", "abu dhabi"],
    "saudi": ["saudi", "riyadh", "neom", "jeddah", "dhahran"],
    "switzerland": ["switzerland", "zurich", "zürich", "lausanne", "basel",
                    "geneva", "genève", "zug", "bern"],
    "australia": ["australia", "sydney", "melbourne", "brisbane", "perth", "canberra"],
    "usa": ["united states", "usa", "new york", "san francisco", "seattle", "austin",
            "boston", ", ca", ", ny", ", wa", ", tx"],
}

# LOCKED_REMOTE still names uk and canada even though neither is a target market
# any more (removed 2026-09-07 on Sunil's instruction). It is a REJECT list: a
# "Remote - Canada" posting must be thrown out whether or not Canada is a market,
# and leaving them here costs nothing.
LOCKED_REMOTE = re.compile(
    r"\bremote\b[\s,:–—-]*(?:in|from|within|-)?[\s,:–—-]*"
    r"(u\.?s\.?a?\b|united states|uk\b|united kingdom|ireland|switzerland|canada|"
    r"germany|netherlands|luxembourg|australia|spain|france|poland|portugal|"
    r"brazil|mexico|"
    r"japan|singapore|emea|apac|europe|latam|amer\b)", re.I)


def classify(job, t):
    """Return (market, reason) or (None, why-rejected)."""
    blob = f"{job['location']} {job['jd'][:4000]}".lower()
    for bad in t["hard_requirements"]["reject_if_jd_says"]:
        if re.search(bad.lower(), blob):
            return None, f"reject: '{bad}'"
    loc = (job["location"] or "").lower()
    # Check country-locked remote BEFORE the location hints. "Remote - Ireland"
    # contains "ireland" and would otherwise classify as the Ireland market and
    # collect its priority-1 bonus — but it means you must ALREADY hold Irish
    # residency, which is the opposite of a sponsorship opening. Only an on-site
    # posting ("Dublin, Ireland") is a relocation target.
    if "remote" in loc and LOCKED_REMOTE.search(loc):
        if not re.search(r"\b(india|worldwide|anywhere|global)\b", loc):
            return None, f"country-locked remote: {job['location'][:40]}"
    for market, hints in MARKET_HINTS.items():
        if any(h in loc for h in hints):
            return market, "target market"
    if "remote" in loc or "anywhere" in loc or "worldwide" in loc:
        # "Remote - US", "Remote, United Kingdom", "Remote - Spain" are
        # country-LOCKED: you must already reside there. Not reachable from India.
        if re.search(r"\b(india|worldwide|anywhere|global|any location)\b", loc):
            return "remote_india", "remote, India-eligible"
        locked = re.search(
            r"\bremote\b[\s,:–—-]*(?:in|from|within)?[\s,:–—-]*"
            r"(u\.?s\.?a?\b|united states|uk\b|united kingdom|canada|spain|ireland|"
            r"germany|france|poland|portugal|brazil|mexico|japan|singapore|emea|apac|"
            r"europe|latam|amer\b)", loc)
        if locked:
            return None, f"country-locked remote: {job['location'][:40]}"
        if re.search(r"\b(india|worldwide|anywhere|global)\b", blob[:3000]):
            return "remote_india", "remote, India named in JD"
        return None, f"remote but residency unclear: {job['location'][:40]}"
    return None, f"outside target markets: {job['location'][:40]}"


# ----------------------------------------------------------------------- main

SCANS = os.path.join(DATA, "board_scans.json")     # {board: when it was last fetched}


def pull(workers=None, limit=None):
    """Fetch the boards, keep what matches titles + markets, store what is new.

    limit (discovery.source_limit; 0 = none): stop fetching boards once this many postings
    matching titles + markets are in hand (known ones count too), and store at most that many.
    Boards scanned longest ago go first then, so limited runs take turns over every board."""
    workers = workers or cfg("discovery.workers", 12)
    limit = cfg("discovery.source_limit", 0) if limit is None else limit
    t = load_targets()
    boards = load_boards()
    con = db()
    seen_before = {r[0] for r in con.execute("SELECT key FROM jobs")}
    KNOWN_JD.clear()
    KNOWN_JD.update({u: j for u, j in con.execute("SELECT url, jd FROM jobs WHERE jd IS NOT NULL AND url != ''")})
    fetchers = []
    for slug in [b["slug"] for b in boards.get("greenhouse", [])]:
        fetchers.append((from_greenhouse, slug))
    for slug in [b["slug"] for b in boards.get("lever", [])]:
        fetchers.append((from_lever, slug))
    for slug in [b["slug"] for b in boards.get("ashby", [])]:
        fetchers.append((from_ashby, slug))
    for slug in [b["slug"] for b in boards.get("smartrecruiters", [])]:
        fetchers.append((from_smartrecruiters, slug))
    for slug in [b["slug"] for b in boards.get("workable", [])]:
        fetchers.append((from_workable, slug))
    for slug in [b["slug"] for b in boards.get("recruitee", [])]:
        fetchers.append((from_recruitee, slug))
    for entry in boards.get("phenom", []):
        fetchers.append((from_phenom, entry))
    for entry in boards.get("workday", []):
        fetchers.append((from_workday, entry))
    try:
        scans = json.load(open(SCANS))
    except (OSError, ValueError):
        scans = {}
    label_of = lambda item: item[1]["slug"] if isinstance(item[1], dict) else item[1]
    if limit:
        fetchers.sort(key=lambda item: scans.get(label_of(item), ""))    # never scanned first

    # Which boards belong to YC companies, so postings can be tagged and boosted.
    yc_of, size_of = {}, {}
    for kind, lst in boards.items():
        for b in lst:
            if b.get("yc"):
                yc_of[b["slug"]] = b["yc"]
            # Open-req count is the best scale proxy available without paid data:
            # Databricks 870, Anthropic 595, a seed startup 3.
            size_of[b["slug"]] = b.get("jobs") or 0

    # YC publishes headcount, which is a better signal than req count for them.
    team_of = {}
    try:
        import json as _json
        cache = os.path.join(DATA, ".yc_companies.json")
        if os.path.isfile(cache):
            for c in _json.load(open(cache)):
                for key in (c.get("slug"), c.get("name")):
                    if key and c.get("team_size"):
                        team_of[re.sub(r"[^a-z0-9]+", "", str(key).lower())] = c["team_size"]
    except Exception:
        pass

    raw = kept = new = 0
    rows = []
    # Fetched in parallel. This loop used to be serial, which is fine at 46
    # boards and not fine at 296: one scan took over 16 minutes and printed
    # nothing until it finished, because every board waits on the previous
    # board's network round-trip. The work is pure I/O wait, so threads help
    # almost linearly. --workers now reaches here, not just --discover.
    def _fetch(item):
        fn, slug = item
        label = slug["slug"] if isinstance(slug, dict) else slug
        try:
            return label, list(fn(slug)), None
        except Exception as e:
            return label, [], type(e).__name__

    def matching(jobs):
        return sum(1 for j in jobs if title_ok(j["title"], t) and classify(j, t)[0] is not None)

    done = matched = 0
    now = dt.datetime.now().isoformat(timespec="seconds")
    ex = ThreadPoolExecutor(max_workers=workers)
    futs = [ex.submit(_fetch, item) for item in fetchers]
    try:
        for fut in as_completed(futs):
            label, jobs, err = fut.result()
            done += 1
            if err:
                print(f"    [warn] {label}: {err}")
            else:
                scans[label] = now
            rows.extend(jobs)
            raw += len(jobs)
            if limit:
                matched += matching(jobs)
            if done % 50 == 0:
                print(f"    {done}/{len(fetchers)} boards, {raw} postings so far")
            if limit and matched >= limit:
                print(f"    limit {limit} reached: {matched} matching postings from {done}/{len(fetchers)} boards")
                break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if not limit or matched < limit:
        for job in from_aggregators():
            raw += 1
            rows.append(job)
    json.dump(scans, open(SCANS, "w"), indent=1)

    # EXPIRY. A posting that has left its board is filled or withdrawn. Until now
    # it stayed 'new' forever: a TII role from 7 Sep was picked first on 23 Sep
    # and scaffolded from a page saying "the job you are trying to apply for has
    # been filled". Only boards that answered with postings expire anything —
    # a failed or empty fetch must not wipe a board.
    live_by_board = {}
    for job in rows:
        live_by_board.setdefault(job["board"], set()).add(job["url"])
    expired = 0
    for board, urls in live_by_board.items():
        for (key, url) in con.execute("SELECT key, url FROM jobs WHERE board=? AND status IN ('new', 'held')", (board,)).fetchall():
            if url and url not in urls:
                con.execute("UPDATE jobs SET status='expired' WHERE key=?", (key,)); expired += 1
    if expired:
        print(f"    expired {expired} posting(s) no longer on their board")

    for job in rows:
        if not title_ok(job["title"], t):
            continue
        market, reason = classify(job, t)
        if market is None:
            continue
        if limit and kept >= limit:
            break
        kept += 1
        k = key_for(job["company"], job["title"], job["location"])
        if k in seen_before:
            continue
        new += 1
        con.execute(
            "INSERT OR IGNORE INTO jobs(key,source,board,company,title,location,url,"
            "remote,posted,seen,market,status,reason,jd,yc,board_size,team_size) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (k, job["source"], job["board"], job["company"], job["title"],
             job["location"], job["url"], int("remote" in (job["location"] or "").lower()),
             "", dt.datetime.now().isoformat(timespec="seconds"), market, "new",
             reason, job["jd"][:12000], yc_of.get(job["board"]),
             size_of.get(job["board"], 0),
             team_of.get(re.sub(r"[^a-z0-9]+", "", (job["company"] or "").lower()))))
    # INSERT OR IGNORE skips rows that already exist, so a column added later
    # never gets populated for them. Reconcile every run, not just on insert.
    tagged = 0
    for slug, batch in yc_of.items():
        tagged += con.execute("UPDATE jobs SET yc=? WHERE board=? AND yc IS NULL",
                              (batch, slug)).rowcount
    for slug, n in size_of.items():
        con.execute("UPDATE jobs SET board_size=? WHERE board=? AND "
                    "(board_size IS NULL OR board_size=0)", (n, slug))
    con.commit()
    print(f"\n  scanned {raw} postings — {kept} matched titles + markets, {new} new"
          + (f", {tagged} newly tagged YC" if tagged else ""))
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--workers", type=int, default=None,
                    help=f"parallel board fetches (default discovery.workers = {cfg('discovery.workers', 12)})")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop once this many postings match titles + markets, store at most that many "
                         f"(default discovery.source_limit = {cfg('discovery.source_limit', 0)}; 0 = all boards)")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    if a.discover:
        return discover(a.workers)
    if a.stats:
        con = db()
        for row in con.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status"):
            print(f"  {row[0]:<12} {row[1]}")
        for row in con.execute("SELECT market, COUNT(*) FROM jobs GROUP BY market ORDER BY 2 DESC"):
            print(f"  {row[0]:<14} {row[1]}")
        n = con.execute("SELECT COUNT(*) FROM jobs WHERE yc IS NOT NULL").fetchone()[0]
        print(f"  {'from YC cos':<14} {n}")
        return
    if a.new:
        con = db()
        for r in con.execute("SELECT company,title,location,market,url FROM jobs "
                             "WHERE status='new' ORDER BY market, company"):
            print(f"  [{r[3]:<13}] {r[0][:18]:<20} {r[1][:44]:<46} {r[2][:28]}")
        return
    pull(a.workers, a.limit)


if __name__ == "__main__":
    main()
