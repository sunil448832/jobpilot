"""
listed.py — one-off (or occasional) sweep of LISTED companies for ATS boards.

Constituents of the big indices are pulled from Wikipedia (S&P 500, Nasdaq-100,
Euro Stoxx 50, DAX, CAC 40, AEX, SMI, FTSE 100, IBEX 35, OMXS30, BEL 20, ISEQ),
plus a curated Gulf list (ADX / DFM / Tadawul), filtered to well-paying sectors
and away from consultancies, outsourcers, staffing, retail and restaurants —
Sunil's instruction, 2026-09-23. Each surviving company is resolved to its
careers page (guessed domains, existence-checked) and the page is fingerprinted
and registered by careers.add(); a careers page that links out to a Workday
tenant registers that tenant. Workday roots cannot be probed directly (every
host on myworkdayjobs.com answers alike), which is why the careers page is the
route.

    python -m jobpilot.discover.listed                # everything, ~30-40 min
    python -m jobpilot.discover.listed --indices sp500,ftse100 --budget 900
    python -m jobpilot.discover.listed --dry-run       # list the companies only
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _TO

import requests

from jobpilot.core.paths import DATA
from jobpilot.core.config import cfg

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
OUT = os.path.join(DATA, "listed_companies.json")

WIKI = {
    "sp500":     ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Security", "GICS Sector"),
    "eurostoxx": ("https://en.wikipedia.org/wiki/EURO_STOXX_50", "Name", "Main industry"),
    "dax":       ("https://en.wikipedia.org/wiki/DAX", "Company", "Prime Standard Sector"),
    "cac40":     ("https://en.wikipedia.org/wiki/CAC_40", "Company", "Sector"),
    "aex":       ("https://en.wikipedia.org/wiki/AEX_index", "Company", "ICB Sector"),
    "smi":       ("https://en.wikipedia.org/wiki/Swiss_Market_Index", "Name", "Sector"),
    "ftse100":   ("https://en.wikipedia.org/wiki/FTSE_100_Index", "Company", "FTSE Industry Classification Benchmark sector"),
    "ibex35":    ("https://en.wikipedia.org/wiki/IBEX_35", "Company", "Sector"),
    "omxs30":    ("https://en.wikipedia.org/wiki/OMX_Stockholm_30", "Company", "Sector"),
    "bel20":     ("https://en.wikipedia.org/wiki/BEL_20", "Company", "Sector"),
    "iseq":      ("https://en.wikipedia.org/wiki/ISEQ_20", "Company", "Sector"),
}

# Gulf exchanges have no clean Wikipedia table; the employers that matter for
# AI/ML hiring and pay well are few enough to name.
GULF = [
    ("e& (Etisalat)", "e&"), ("ADNOC", "adnoc"), ("Emirates NBD", "emiratesnbd"), ("First Abu Dhabi Bank", "bankfab"),
    ("Abu Dhabi Commercial Bank", "adcb"), ("Dubai Islamic Bank", "dib"), ("Mashreq", "mashreq"), ("Emaar", "emaar"),
    ("Aldar", "aldar"), ("TAQA", "taqa"), ("DEWA", "dewa"), ("Presight AI", "presight"), ("Aramex", "aramex"),
    ("du (EITC)", "du"), ("Emirates", "emirates"), ("Etihad Airways", "etihad"), ("ADQ", "adq"), ("Mubadala", "mubadala"),
    ("Saudi Aramco", "aramco"), ("stc", "stc"), ("SABIC", "sabic"), ("Al Rajhi Bank", "alrajhibank"),
    ("Saudi National Bank", "alahli"), ("Elm", "elm"), ("Mobily", "mobily"), ("NEOM", "neom"), ("Riyad Bank", "riyadbank"),
    ("SDAIA", "sdaia"), ("Ma'aden", "maaden"), ("ACWA Power", "acwapower"),
]

# Sectors worth the effort (GICS / ICB wording varies by index).
SECTOR_OK = re.compile(r"information technology|technology|software|semiconductor|communication|telecom|media|"
                       r"financial|bank|insurance|health|pharma|biotech|medical|consumer discretionary|"
                       r"automobile|automotive|aerospace|defen[cs]e|industrial goods|electronic|internet|"
                       r"e-?commerce|payment|fintech|chemicals?$|luxury", re.I)
# Plainly low-paying or out of scope, whatever the sector says.
NAME_DENY = re.compile(r"accenture|cognizant|infosys|wipro|tata consultancy|hcl|tech mahindra|capgemini|epam|dxc|"
                       r"genpact|wns|kyndryl|concentrix|teleperformance|randstad|adecco|manpower|robert half|"
                       r"sodexo|compass group|securitas|g4s|restaurant|stores?\b|retail|hotels?|resorts?|cruise|"
                       r"apparel|foods?\b|beverage|tobacco|brewing|casino|gaming|auto parts|tire|rubber|"
                       r"dollar|mcdonald|starbucks|yum|darden|chipotle|kroger|albertsons|tjx|ross|gap\b|macy|"
                       r"walgreens|cvs|rite aid|reit\b|realty|properties|utilities|utility|energy|oil|gas|"
                       r"mining|steel|cement|paper|packaging|waste|water\b|airlines?|airways|railway|shipping|"
                       r"trucking|logistics|leasing|rental", re.I)
# Industrials that do employ ML engineers at good pay — kept despite the sector.
NAME_KEEP = re.compile(r"siemens|abb\b|schneider|honeywell|general electric|ge aerospace|ge vernova|rtx|raytheon|"
                       r"lockheed|northrop|boeing|airbus|deere|caterpillar|rockwell|emerson|uber|airbnb|booking|"
                       r"tesla|amazon|rolls-royce|bae systems|thales|safran|dassault|leonardo|saab|"
                       r"bmw|mercedes|volkswagen|stellantis|ferrari|porsche|volvo|renault|toyota|ford|"
                       r"general motors|3m|philips|asml|infineon|stmicro|nxp|schindler|sika|geberit|otis|"
                       r"trane|carrier|johnson controls|eaton|parker|illinois tool|fedex|ups\b", re.I)
SUFFIX = re.compile(r"[,.]?\s*\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|ag|se|nv|n\.v|sa|s\.a|"
                    r"spa|s\.p\.a|ab|asa|oyj|holdings?|holding|group|the|class [abc]|\(class [abc]\))\b\.?", re.I)


def fetch_index(key):
    url, name_col, sector_col = WIKI[key]
    import pandas as pd
    html = requests.get(url, headers=UA, timeout=30).text
    out = []
    for tbl in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in tbl.columns]
        nc = next((c for c in cols if c.lower().startswith(name_col.lower())), None)
        if not nc:
            continue
        sc = next((c for c in cols if sector_col.lower() in c.lower()), None)
        for _, row in tbl.iterrows():
            name = str(row[nc]).strip()
            if not name or name.lower() == "nan" or len(name) > 60:
                continue
            sector = str(row[sc]).strip() if sc else ""
            out.append((name, sector))
        if len(out) >= 15:
            break
    return out


def keep(name, sector):
    if NAME_DENY.search(name):
        return False
    if NAME_KEEP.search(name):
        return True
    if not sector or sector.lower() == "nan":
        return True                       # index without a sector column: let the denylist decide
    if NAME_DENY.search(sector):
        return False
    return bool(SECTOR_OK.search(sector))


def slugs(name):
    base = re.sub(r"\(.*?\)", "", name)
    base = SUFFIX.sub("", base).strip(" .,&-")
    words = [w for w in re.split(r"[\s&/-]+", base) if w]
    compact = re.sub(r"[^a-z0-9]", "", "".join(words).lower())
    out = []
    if compact:
        out.append(compact)
    if len(words) >= 2:
        two = re.sub(r"[^a-z0-9]", "", "".join(words[:2]).lower())
        first = re.sub(r"[^a-z0-9]", "", words[0].lower())
        for c in (two, first):
            if c and c not in out and len(c) >= 4:
                out.append(c)
    return out[:3]


def exists(domain):
    try:
        r = requests.head(f"https://{domain}", allow_redirects=True, timeout=6, headers=UA)
        return r.status_code < 500
    except Exception:
        return False


QUICK = ["https://careers.{d}", "https://{d}/careers", "https://www.{d}/careers", "https://jobs.{d}",
         "https://{d}/en/careers", "https://www.{d}/en/careers", "https://{d}/careers/jobs", "https://{d}/join-us"]


def careers_for(name, tld_hint="com"):
    from jobpilot.discover.find_careers import probe_url
    tlds = ["com"] + ([tld_hint] if tld_hint != "com" else []) + ["ai", "io"]
    for slug in slugs(name):
        for tld in tlds:
            d = f"{slug}.{tld}"
            if not exists(d):
                continue
            for pat in QUICK:
                u = probe_url(pat.format(d=d), timeout=7)
                if u:
                    return u
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", default=",".join(list(WIKI) + ["gulf"]))
    ap.add_argument("--budget", type=int, default=cfg("discovery.listed_budget_s", 2400))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    tld_of = {"dax": "de", "cac40": "fr", "aex": "nl", "smi": "ch", "ftse100": "co.uk", "ibex35": "es",
              "omxs30": "se", "bel20": "be", "iseq": "ie", "eurostoxx": "com", "gulf": "ae"}
    companies = {}
    for key in [k.strip() for k in a.indices.split(",") if k.strip()]:
        if key == "gulf":
            rows = [(n, "") for n, _ in GULF]
        else:
            try:
                rows = fetch_index(key)
            except Exception as ex:
                print(f"  {key}: could not read Wikipedia table ({type(ex).__name__})")
                continue
        kept = [(n, s) for n, s in rows if keep(n, s)]
        print(f"  {key:<10} {len(rows):>4} constituents, {len(kept):>4} kept")
        for n, s in kept:
            companies.setdefault(n, {"index": key, "sector": s, "tld": tld_of.get(key, "com")})
    print(f"  {len(companies)} companies after sector / pay filter")

    prev = json.load(open(OUT)) if os.path.isfile(OUT) else {}
    todo = [n for n in companies if n not in prev]
    print(f"  {len(todo)} not tried before")
    if a.dry_run:
        for n in sorted(companies)[:400]:
            print(f"    {n[:36]:<36} {companies[n]['index']:<10} {companies[n]['sector'][:30]}")
        return

    from jobpilot.discover.tenants import register_one
    found = {}
    registered = []
    started = time.time()

    def log(msg):
        print(f"  {msg}")

    ex = ThreadPoolExecutor(max_workers=a.workers)
    futs = {ex.submit(careers_for, n, companies[n]["tld"]): n for n in todo}
    try:
        for fut in as_completed(futs, timeout=a.budget):
            n = futs[fut]
            try:
                url = fut.result()
            except Exception:
                url = None
            rec = dict(companies[n], careers=url, tried=dt.datetime.now().isoformat(timespec="seconds"))
            if url:
                k = register_one(url, log)
                rec["registered"] = k
                if k:
                    registered.append((n, k))
                    print(f"    + {n[:30]:<30} {k}")
            found[n] = rec
            prev[n] = rec
            if len(found) % 25 == 0:
                json.dump(prev, open(OUT, "w"), indent=1)
    except _TO:
        print(f"  budget {a.budget}s reached")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
        json.dump(prev, open(OUT, "w"), indent=1)
    with_page = sum(1 for r in found.values() if r.get("careers"))
    print(f"\n  {len(found)} companies tried in {int(time.time() - started)}s: "
          f"{with_page} careers pages, {len(registered)} boards registered")
    for n, k in registered:
        print(f"    {n[:30]:<30} {k}")


if __name__ == "__main__":
    main()
