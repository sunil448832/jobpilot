#!/usr/bin/env python3
"""
sponsors.py — employers the government lists as sponsoring work permits, so a posting silent
on visas is still a known yes (Meta, Google, Booking ... rarely say it in the JD).

    The registers are listed in config.yaml visa.sponsor_registers (market, kind, label,
    url or path) — add one there, no code:
    netherlands   ind_html: IND public register of recognised sponsors, regular labour and
                  highly skilled migrants (~13,000 employers): only these can sponsor
    ireland       dete_xlsx: Department of Enterprise, employment permits issued to companies,
                  this year and last (~8,000 employers, with the count)
    australia     csv: data/registers/australia_sponsors.csv (a Home Affairs FOI snapshot,
                  filled by hand — no official live dataset exists, 2026-10-19)

Germany and Luxembourg have no such list: an EU Blue Card needs no licensed sponsor, and a
silent posting there already counts as implicit (visa.implicit_markets).

Cached in data/sponsors.json, fetched again when older than visa.sponsor_refresh_days (30).

    known(company, market)   "<register>: <its entry>" when the employer is on that market's
                             list, else None
    candidates()             employers worth finding a job board for (tenants.py source "sponsors")
    export()                 data/sponsoring_companies.csv: both registers, best leads first
    python -m jobpilot.rank.sponsors [--refresh] [--export] [--who <company>]
"""
import argparse
import datetime as dt
import html
import io
import json
import os
import re

import requests

from jobpilot.core.config import cfg
from jobpilot.core.paths import DATA

CACHE = os.path.join(DATA, "sponsors.json")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jobpilot"}
IND = "https://ind.nl/en/public-register-recognised-sponsors/public-register-regular-labour-and-highly-skilled-migrants"
DETE = "https://enterprise.gov.ie/en/publications/publication-files/{name}.xlsx"
DETE_NAMES = ("employment-permits-issued-to-companies-{y}", "permits-issued-to-companies-{y}")

# legal forms and place words: "Meta Platforms Ireland Limited" and "Meta" are one employer
NOISE = re.compile(r"\b(b\.?v\.?|n\.?v\.?|limited|ltd|dac|plc|inc|llc|gmbh|uc|unlimited|company|co|holding|holdings|"
                   r"group|international|europe|emea|ireland|netherlands|nederland|the)\b", re.I)


def words(name):
    """An employer's name as plain words, legal forms and place words dropped."""
    n = html.unescape(name or "").lower().replace(".com", "com").replace("&", " and ")
    return [w for w in re.sub(r"[^a-z0-9 ]+", " ", NOISE.sub(" ", n)).split() if w]


def registers():
    """config.yaml visa.sponsor_registers, with the two original registers as the default."""
    regs = cfg("visa.sponsor_registers", None)
    if not regs:
        regs = [{"market": "netherlands", "kind": "ind_html", "label": "IND recognised sponsor", "url": IND},
                {"market": "ireland", "kind": "dete_xlsx", "label": "Irish employment permits issued",
                 "url": DETE, "names": list(DETE_NAMES)}]
    return [r for r in regs if r.get("market") and r.get("kind")]


def register_markets():
    return sorted({r["market"] for r in registers()})


def label_of(market):
    return " / ".join(r.get("label") or r["kind"] for r in registers() if r["market"] == market) or "sponsor register"


def fetch(reg):
    """One register -> {name: permits or None}."""
    kind = reg["kind"]
    if kind == "ind_html":
        return {n: None for n in fetch_nl(reg.get("url") or IND)}
    if kind == "dete_xlsx":
        return fetch_ie(reg.get("url") or DETE, reg.get("names") or DETE_NAMES)
    if kind == "csv":
        return fetch_csv(reg["path"])
    raise ValueError(f"unknown register kind {kind!r}")


def fetch_csv(path):
    """A local file: company,permits (permits optional); '#' lines are comments."""
    import csv
    from jobpilot.core.paths import TOOL
    out = {}
    full = path if os.path.isabs(path) else os.path.join(TOOL, path)
    with open(full, encoding="utf-8") as f:
        rows = [r for r in csv.reader(l for l in f if not l.lstrip().startswith("#")) if r]
    for r in rows:
        co = (r[0] or "").strip()
        if not co or co.lower() == "company":
            continue
        n = r[1].strip() if len(r) > 1 and r[1] else ""
        out[co] = int(float(n)) if re.match(r"^\d+(\.\d+)?$", n) else None
    return out


def fetch_nl(url=IND):
    t = requests.get(url, headers=UA, timeout=60).text
    return sorted({html.unescape(m).strip() for m in re.findall(r'<th scope="row">([^<]+)</th>', t)})


def fetch_ie(url=DETE, names=DETE_NAMES):
    import openpyxl
    out = {}
    year = dt.date.today().year
    for y in (year - 1, year):
        for name in names:
            r = requests.get(url.format(name=name.format(y=y)), headers=UA, timeout=60)
            if r.status_code != 200 or not r.content.startswith(b"PK"):
                continue
            rows = list(openpyxl.load_workbook(io.BytesIO(r.content), read_only=True).worksheets[0].iter_rows(values_only=True))
            head = [str(c or "").strip().lower() for c in rows[0]]
            total = head.index("grand total") if "grand total" in head else None
            for row in rows[1:]:
                co = str(row[0] or "").strip()
                if not co or co.lower() == "grand total":
                    continue
                n = row[total] if total is not None and total < len(row) else None
                n = n if isinstance(n, (int, float)) else sum(x for x in row[1:] if isinstance(x, (int, float)))
                out[co] = out.get(co, 0) + int(n or 0)
            break
    return out


def refresh():
    """Every register in config, merged per market: {market: {name: permits or None}}. A
    remote register that fails keeps its last cached copy; a local one may be empty."""
    try:
        old = json.load(open(CACHE))
    except (OSError, ValueError):
        old = {}
    doc = {"fetched": dt.date.today().isoformat()}
    for reg in registers():
        m = reg["market"]
        try:
            got = fetch(reg)
        except Exception as e:
            print(f"  [sponsors] {m} ({reg['kind']}) failed: {type(e).__name__}: {e} — keeping the cached list")
            got = None
        if got is None:
            prev = old.get(m)
            got = prev if isinstance(prev, dict) else {n: None for n in (prev or [])}
        if not got and reg["kind"] != "csv":
            raise RuntimeError(f"the {m} register came back empty")
        doc.setdefault(m, {}).update(got)
    json.dump(doc, open(CACHE, "w"), indent=0)
    try:
        export()
    except Exception as e:
        print(f"  [sponsors] export failed: {e}")
    return doc


_INDEX = None


def index():
    """{market: {first word: [(words, entry), ...]}}, refreshed when the cache is old."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    try:
        doc = json.load(open(CACHE))
        old = (dt.date.today() - dt.date.fromisoformat(doc["fetched"])).days > int(cfg("visa.sponsor_refresh_days", 30))
    except (OSError, ValueError, KeyError):
        doc, old = None, True
    if old:
        try:
            doc = refresh()
        except Exception as e:                     # offline: an old list beats none
            print(f"  [sponsors] refresh failed ({type(e).__name__}: {e}); "
                  + ("using the list of " + doc["fetched"] if doc else "no list"))
    _INDEX = {}
    for market in register_markets():
        entries = (doc or {}).get(market) or {}
        idx = _INDEX.setdefault(market, {})
        for name in entries:
            w = words(name)
            if w:
                n = entries[name] if isinstance(entries, dict) else None
                label = f"{name} ({n} permits)" if n else name
                idx.setdefault(w[0], []).append((w, label))
    return _INDEX


# words a register entry may add to a one-word employer name and still be that employer
# ("Meta Platforms", "Uber Eats", "Microsoft Operations") — not "Meta-Skills", "Booking Experts"
GENERIC = {"platforms", "platform", "technology", "technologies", "tech", "services", "service", "operations",
           "payments", "cloud", "software", "labs", "ai", "systems", "global", "digital", "solutions", "central",
           "core", "amsterdam", "dublin", "cork", "galway", "eindhoven", "rotterdam", "utrecht", "distribution",
           "development", "r", "d", "and", "rd", "eats", "cs", "product", "products", "engineering", "research",
           "data", "center", "centre", "corporation", "corp", "eu", "uk", "nl", "ie", "benelux", "emea", "ops"}


# an employer registered under a former or parent name
ALIASES = {"meta": ["Facebook"], "x": ["Twitter"], "alphabet": ["Google"], "block": ["Square"],
           "jci": ["Johnson Controls"], "justeattakeaway": ["Takeaway"], "salesforce": ["SFDC"]}


def known(company, market):
    """The register entry that shows this employer sponsors in this market, else None.
    One-word names ("Meta") match an entry whose first word is that name, or the name plus
    .com / ai / io, and whose other words are generic ("Meta Platforms Ireland Limited",
    "Booking.com B.V.") — never "Meta-Skills B.V." or "Booking Experts B.V.". Longer names
    match word for word from the start ("Flow Traders" -> "Flow Traders Amsterdam Services")."""
    w = words(company)
    if not w or market not in register_markets():
        return None
    for alias in ALIASES.get(" ".join(w), []):   # registered under a former name
        hit = known(alias, market)
        if hit:
            return hit
    reg = label_of(market)
    idx = index().get(market, {})
    if len(w) > 1:
        for rw, label in idx.get(w[0], []):
            if len(rw) >= len(w) and rw[:len(w) - 1] == w[:-1] and rw[len(w) - 1].startswith(w[-1]):
                return f"{reg}: {label}"
        return None
    one = w[0]
    for first in (one, one + "com", one + "ai", one + "io"):
        for rw, label in idx.get(first, []):
            if all(x in GENERIC for x in rw[1:]):
                return f"{reg}: {label}"
    if len(one) >= 8:                              # "Justeattakeaway" -> "Just Eat Takeaway.com N.V."
        for entries in idx.values():
            for rw, label in entries:
                if "".join(rw).startswith(one):
                    return f"{reg}: {label}"
    return None


# what an employer's name says about it: not the kind of work he does / likely tech
NOT_TECH = re.compile(r"hospital|health ?service|\bhse\b|nursing|\bcare\b|homecare|clinic|medical centre|dental|pharmac|"
                      r"hotel|hospitality|restaurant|catering|takeaway food|\bfood|bakery|cafe|\bbar\b|\bpub\b|"
                      r"construction|builders|farm|meat|dairy|poultry|mushroom|horticult|cleaning|security services|"
                      r"recruit|staffing|personnel|uitzend|school|universit|college|church|haulage|transport|"
                      r"logistics|retail|beauty|salon|garage|motors|nursing|veterin|kindergarten|kinderopvang|"
                      r"healthcare|care\b|\bbeef|processors|abattoir|\bbus\b|coaches|inspection|children|"
                      r"electricity|nursing home|payroll|adecco|randstad|manpower|personeel|"
                      # consultancies and outsourcers: out, on his instruction (2026-09-23, discover/listed.py)
                      r"consult|outsourc|ernst|deloitte|accenture|kpmg|\bpwc\b|pricewaterhouse|tata |cognizant|"
                      r"infosys|wipro|\bhcl|capgemini|mahindra|mphasis|\bltimindtree|genpact|\bntt data", re.I)
TECH = re.compile(r"tech|software|digital|data|\bai\b|cloud|\blabs?\b|systems|computing|analytics|platform|"
                  r"internet|online|cyber|semiconductor|robot|fintech|payments|\bbank|trading|games|gaming|studio|"
                  r"research|quantum|\bbio|engineering|\bnetworks?|\bit\b|informat|intelligence|automation", re.I)
EXPORT = os.path.join(DATA, "sponsoring_companies.csv")


def candidates():
    """[(name, market, permits or None, why)] — employers worth looking for a job board at,
    best first: the largest Irish sponsors, then employers on both lists (multinationals),
    then tech-looking Dutch names. Care homes, restaurants, builders, staffing are left out."""
    index()
    doc = json.load(open(CACHE))
    nl, ie = doc.get("netherlands") or [], doc.get("ireland") or {}
    if isinstance(nl, dict):
        nl = list(nl)
    # a multinational: its first word (4+ letters) names an Irish employer with 5+ permits
    ie_first = {(words(n) or [""])[0] for n, c in ie.items() if c >= 5 and len((words(n) or [""])[0]) >= 4}
    out = []
    for name, n in sorted(ie.items(), key=lambda kv: -kv[1]):
        if name.strip().lower() != "total" and not NOT_TECH.search(name):
            out.append((name, "ireland", n, "Irish permits"))
    for name in nl:
        if NOT_TECH.search(name):
            continue
        w = words(name)
        if w and w[0] in ie_first:
            out.append((name, "netherlands", None, "on both lists"))
    for name in nl:
        if not NOT_TECH.search(name) and TECH.search(name) and not (words(name) and words(name)[0] in ie_first):
            out.append((name, "netherlands", None, "tech name"))
    for market in register_markets():                # any other register (a csv): every tech-looking name
        if market in ("netherlands", "ireland"):
            continue
        entries = doc.get(market) or {}
        for name in (entries if isinstance(entries, dict) else list(entries)):
            if not NOT_TECH.search(name):
                out.append((name, market, entries.get(name) if isinstance(entries, dict) else None, label_of(market)))
    return out


def export():
    """data/sponsoring_companies.csv — every employer on both registers, the best leads first:
    company, country, register, permits (Ireland), lead (why it is a candidate, else blank),
    in_boards (its jobs are already scanned), careers_url and platform (what discovery found:
    ats = a job board intake reads, custom = its own site, read by the generic reader)."""
    import csv
    import yaml
    from jobpilot.core.paths import CONFIG
    boards = yaml.safe_load(open(os.path.join(CONFIG, "boards.yaml"))) or {}
    have = set()
    for entries in boards.values():
        for e in entries or []:
            for k in ("slug", "tenant", "name", "url"):
                if isinstance(e, dict) and e.get(k):
                    have.add(re.sub(r"[^a-z0-9]", "", str(e[k]).lower().split("/")[-1].split(".")[0])[:20])
    doc = json.load(open(CACHE))
    lead = {(n, m): why for n, m, _, why in candidates()}
    try:                                             # careers pages discovery found (tenants.py)
        found = json.load(open(os.path.join(DATA, "sponsor_careers.json")))
    except (OSError, ValueError):
        found = {}
    by_word = {}
    for emp, v in found.items():
        w = words(emp)
        if w:
            by_word.setdefault(w[0], v)
    rows = []
    for name, n in sorted((doc.get("ireland") or {}).items(), key=lambda kv: -kv[1]):
        if name.strip().lower() == "total":
            continue
        rows.append((name, "Ireland", "employment permits issued to companies", n, lead.get((name, "ireland"), "")))
    for name in doc.get("netherlands") or []:
        rows.append((name, "Netherlands", "IND recognised sponsor", "", lead.get((name, "netherlands"), "")))
    for market in register_markets():
        if market in ("netherlands", "ireland"):
            continue
        entries = doc.get(market) or {}
        for name in (entries if isinstance(entries, dict) else list(entries)):
            rows.append((name, market.title(), label_of(market), (entries.get(name) if isinstance(entries, dict) else "") or "",
                         lead.get((name, market), "")))
    with open(EXPORT, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["company", "country", "register", "permits", "lead", "in_boards", "careers_url", "platform", "fetched"])
        for name, country, reg, n, why in rows:
            w = words(name)
            inb = "yes" if w and (w[0][:20] in have or "".join(w)[:20] in have) else ""
            c = by_word.get(w[0], {}) if w else {}
            wr.writerow([name, country, reg, n, why, inb, c.get("careers_url", ""), c.get("platform") or "", doc.get("fetched")])
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--who", help="look one employer up in both registers")
    ap.add_argument("--export", action="store_true", help="write data/sponsoring_companies.csv")
    a = ap.parse_args()
    if a.refresh:
        d = refresh()
        print("  " + ", ".join(f"{m} {len(d.get(m) or {})}" for m in register_markets()))
    if a.export or a.refresh:
        print(f"  {export()} employers -> {EXPORT}")
    if a.who:
        for m in register_markets():
            print(f"  {m:12} {known(a.who, m) or '-'}")


if __name__ == "__main__":
    main()
