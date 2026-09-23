"""
tenants.py — grow boards.yaml by itself.

The goal is company names, not job descriptions: one ATS URL per employer is
enough, because the employer's own board (Workday tenant API, Greenhouse,
Lever, Ashby …) is then pulled in full on every scan by intake.py. So every
source here just has to SEE a careers/apply URL once. Each URL goes through
careers.add(), which fingerprints the ATS, verifies the board answers, and
saves it to boards.yaml — a wrong guess is rejected, never stored.

Sources, cheapest and safest first (each can be switched off):
  sweep     the curated employer lists per market (find_careers.MARKETS),
            probed for a careers page and fingerprinted. No keys, no risk.
  search    Brave Search API (BRAVE_API_KEY) or Google Custom Search
            (GOOGLE_CSE_KEY + GOOGLE_CSE_CX): site:myworkdayjobs.com "<title>" <hub>.
  adzuna    Adzuna API (ADZUNA_APP_ID + ADZUNA_APP_KEY): redirect URLs resolved.
  linkedin  the account-free guest job search: employer NAMES only (guest pages
            hide the apply link), resolved to careers pages by domain guessing —
            existence-checked first, streamed, time-budgeted. Never Sunil's
            account or session. First trial: 12 searches -> 64 names -> 24
            careers pages, which is why it stays.

    python -m jobpilot.discover.tenants                 # all sources, weekly defaults
    python -m jobpilot.discover.tenants --sources linkedin --budget 300
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse

import requests
import yaml

from jobpilot.core.paths import CONFIG, DATA
from jobpilot.core.config import cfg

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
SEEN = os.path.join(DATA, "tenants_seen.json")
ATS_HOST = re.compile(r"https?://[^\s\"'<>)]*?("
                      r"myworkdayjobs\.com|greenhouse\.io|lever\.co|ashbyhq\.com|"
                      r"smartrecruiters\.com|apply\.workable\.com|recruitee\.com)[^\s\"'<>)]*", re.I)


def env():
    from jobpilot.core.daily import env as _env
    return _env()


def load_seen():
    if os.path.isfile(SEEN):
        try:
            return json.load(open(SEEN))
        except Exception:
            pass
    return {}


def save_seen(seen):
    os.makedirs(DATA, exist_ok=True)
    json.dump(seen, open(SEEN, "w"), indent=1)


def board_key(url):
    """One key per employer board: host + first path segment (Workday's site)."""
    p = urllib.parse.urlparse(url)
    segs = [s for s in p.path.split("/") if s and not re.match(r"^[a-z]{2}-[A-Z]{2}$", s)]
    head = segs[0] if segs else ""
    if "myworkdayjobs.com" in p.netloc:
        return f"{p.netloc}/{head}"
    return f"{p.netloc}/{head}" if p.netloc.endswith(("greenhouse.io", "lever.co", "ashbyhq.com",
                                                        "smartrecruiters.com", "workable.com")) else p.netloc


def targets():
    t = yaml.safe_load(open(os.path.join(CONFIG, "targets.yaml")))
    markets = [m for m in t.get("markets", []) if m.get("id") not in ("remote_india",)]
    titles = list((t.get("titles") or {}).get("primary") or [])
    return markets, titles


# ------------------------------------------------------------------ sources

def src_sweep(markets, log):
    """find_careers' employer lists per market -> careers URL -> ATS."""
    from jobpilot.discover import find_careers
    out = []
    for m in markets:
        doms = find_careers.MARKETS.get(m["id"])
        if not doms:
            continue
        for d in doms:
            _, url = find_careers.find_for(d)
            if url:
                out.append(url)
    log(f"sweep: {len(out)} careers pages located")
    return out


def src_search(markets, titles, log, per_market=6):
    """Web search for Workday job pages; one hit per employer is all we need."""
    e = env()
    key, gkey, gcx = e.get("BRAVE_API_KEY"), e.get("GOOGLE_CSE_KEY"), e.get("GOOGLE_CSE_CX")
    if not key and not (gkey and gcx):
        log("search: no BRAVE_API_KEY / GOOGLE_CSE_KEY+CX in ~/.config/jobbot/env — skipped")
        return []
    out = []
    for m in markets:
        hubs = (m.get("hubs") or [m.get("country")])[:2]
        for hub in hubs:
            for title in titles[:per_market // len(hubs) or 1]:
                q = f'site:myworkdayjobs.com "{title}" {hub}'
                try:
                    if key:
                        r = requests.get("https://api.search.brave.com/res/v1/web/search",
                                         params={"q": q, "count": 20},
                                         headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=20)
                        r.raise_for_status()
                        urls = [x.get("url") for x in (r.json().get("web") or {}).get("results", [])]
                    else:
                        r = requests.get("https://www.googleapis.com/customsearch/v1",
                                         params={"key": gkey, "cx": gcx, "q": q, "num": 10}, timeout=20)
                        r.raise_for_status()
                        urls = [x.get("link") for x in r.json().get("items", [])]
                    out.extend(u for u in urls if u)
                except Exception as ex:
                    log(f"search: {q[:50]} -> {type(ex).__name__}")
                    if "429" in str(ex) or "quota" in str(ex).lower():
                        return out
                time.sleep(1.1)
    log(f"search: {len(out)} result urls")
    return out


def src_adzuna(markets, titles, log):
    e = env()
    app_id, app_key = e.get("ADZUNA_APP_ID"), e.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log("adzuna: no ADZUNA_APP_ID/KEY — skipped")
        return []
    cc = {"netherlands": "nl", "ireland": "ie", "germany": "de", "switzerland": "ch",
          "australia": "au", "uae": "ae", "usa": "us", "luxembourg": "lu"}
    out = []
    for m in markets:
        c = cc.get(m["id"])
        if not c:
            continue
        for title in titles[:4]:
            try:
                r = requests.get(f"https://api.adzuna.com/v1/api/jobs/{c}/search/1",
                                 params={"app_id": app_id, "app_key": app_key, "what": title,
                                         "results_per_page": 50, "content-type": "application/json"}, timeout=20)
                r.raise_for_status()
                for j in r.json().get("results", []):
                    u = j.get("redirect_url")
                    if not u:
                        continue
                    try:                       # the wrapper redirects to the employer's ATS
                        h = requests.head(u, allow_redirects=True, timeout=12, headers={"User-Agent": UA})
                        out.append(h.url)
                    except Exception:
                        pass
            except Exception as ex:
                log(f"adzuna: {m['id']}/{title[:20]} -> {type(ex).__name__}")
            time.sleep(1.0)
    log(f"adzuna: {len(out)} resolved urls")
    return out


def src_linkedin(markets, titles, log, max_searches=40, delay=2.0):
    """LinkedIn's guest job search, no account and no session cookie — LinkedIn
    is the channel that converts and the account must never be put at risk.
    Guest pages do not expose the external apply link, so this yields COMPANY
    NAMES only, which is the goal; companies_to_urls() resolves each. Polite
    delay; stops on the first 429."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    names, searches = {}, 0
    for m in markets:
        loc = m.get("country") or ""
        for title in titles[:4]:
            if searches >= max_searches:
                break
            try:
                r = s.get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                          params={"keywords": title, "location": loc, "start": 0}, timeout=20)
            except Exception as ex:
                log(f"linkedin: {type(ex).__name__} on search — stopping")
                return names
            searches += 1
            if r.status_code in (429, 999):
                log(f"linkedin: HTTP {r.status_code} — stopping for this run")
                return names
            time.sleep(delay)
            for co in re.findall(r'base-search-card__subtitle[^>]*>\s*(?:<a[^>]*>)?\s*([^<]+?)\s*<', r.text):
                co = co.strip()
                if co and len(co) < 60:
                    names.setdefault(co, m["id"])
    log(f"linkedin: {searches} searches, {len(names)} employer names")
    return names


COMPANY_NOISE = re.compile(r"\b(inc\.?|ltd\.?|limited|llc|gmbh|ag|plc|b\.?v\.?|s\.?a\.?|pty|co\.?|corp\.?|corporation|group|technologies|technology|labs?)\b\.?$", re.I)
MARKET_TLD = {"ireland": "ie", "netherlands": "nl", "germany": "de", "switzerland": "ch",
              "australia": "com.au", "uae": "ae", "luxembourg": "lu", "usa": "com", "saudi": "sa"}


def domain_exists(domain):
    """One cheap request per candidate domain, before any careers-page probing."""
    try:
        r = requests.head(f"https://{domain}", allow_redirects=True, timeout=6,
                          headers={"User-Agent": UA})
        return r.status_code < 500
    except Exception:
        return False


def companies_to_urls(names, log, workers=8, budget=420, on_url=None):
    """Employer name -> careers page. Domain guesses are checked for existence
    first (one HEAD each) so the 11 careers-page patterns are only probed on
    real domains; results stream to `on_url` as they arrive; `budget` seconds
    caps the whole step so a weekly run always ends."""
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _TO
    from jobpilot.discover import find_careers
    seen = load_seen()
    todo = []
    for name, market in names.items():
        base = COMPANY_NOISE.sub("", name.strip()).strip(" .,-")
        slug = re.sub(r"[^a-z0-9]+", "", base.lower())
        if not slug or len(slug) < 3:
            continue
        key = f"company:{slug}"
        if key in seen:
            continue
        seen[key] = {"first_seen": dt.datetime.now().isoformat(timespec="seconds"), "name": name}
        tlds = ["com", "ai", "io"] + ([MARKET_TLD[market]] if market in MARKET_TLD and MARKET_TLD[market] != "com" else [])
        todo.append((name, slug, [f"{slug}.{t}" for t in tlds]))
    save_seen(seen)
    if not todo:
        log("companies: nothing new to resolve")
        return []

    def resolve(item):
        name, slug, domains = item
        for d in domains:
            if not domain_exists(d):
                continue
            _, url = find_careers.find_for(d)
            if url:
                return name, url
        return name, None

    out, started = [], time.time()
    ex = ThreadPoolExecutor(max_workers=workers)
    futs = [ex.submit(resolve, t) for t in todo]
    try:
        for fut in as_completed(futs, timeout=budget):
            name, url = fut.result()
            if url:
                out.append(url)
                log(f"  {name[:28]:<28} -> {url[:60]}")
                if on_url:
                    on_url(url)
    except _TO:
        log(f"companies: time budget ({budget}s) reached — {len(out)} resolved so far")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    log(f"companies: {len(out)}/{len(todo)} careers pages found in {int(time.time() - started)}s")
    return out


# ----------------------------------------------------------------- register

def register_one(u, log, seen=None, dry=False):
    """careers.add() for one URL, remembered in tenants_seen. Returns key or None."""
    from jobpilot.discover import careers
    seen = load_seen() if seen is None else seen
    k = board_key(u)
    if k in seen:
        return None
    seen[k] = {"first_seen": dt.datetime.now().isoformat(timespec="seconds"), "url": u[:200]}
    ok = False
    if not dry:
        try:
            ok = bool(careers.add(u))
        except Exception as ex:
            log(f"  {k}: {type(ex).__name__}")
    seen[k]["registered"] = ok
    save_seen(seen)
    return k if ok else None


def register(urls, log, dry=False):
    from jobpilot.discover import careers
    seen = load_seen()
    boards = yaml.safe_load(open(os.path.join(CONFIG, "boards.yaml"))) or {}
    have = {board_key(f"https://{b.get('host')}/{b.get('site')}") for b in boards.get("workday", [])}
    added, tried = [], 0
    now = dt.datetime.now().isoformat(timespec="seconds")
    for u in urls:
        k = board_key(u)
        if k in have or k in seen:
            continue
        seen[k] = {"first_seen": now, "url": u[:200]}
        tried += 1
        if dry:
            log(f"  would try {k}")
            continue
        try:
            ok = careers.add(u)
        except Exception as ex:
            ok = False
            log(f"  {k}: {type(ex).__name__}")
        seen[k]["registered"] = bool(ok)
        if ok:
            added.append(k)
        have.add(k)
    save_seen(seen)
    return added, tried


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=cfg("discovery.tenant_sources", "sweep,search,adzuna,linkedin"))
    ap.add_argument("--max-searches", type=int, default=cfg("discovery.linkedin_max_searches", 40))
    ap.add_argument("--budget", type=int, default=cfg("discovery.tenant_budget_s", 420),
                    help="seconds allowed for resolving employer names to careers pages")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    def log(msg):
        if not a.quiet:
            print(f"  {msg}")

    markets, titles = targets()
    srcs = [s.strip() for s in a.sources.split(",") if s.strip()]
    urls = []
    if "sweep" in srcs:
        urls += src_sweep(markets, log)
    if "search" in srcs:
        urls += src_search(markets, titles, log)
    if "adzuna" in srcs:
        urls += src_adzuna(markets, titles, log)
    urls = list(dict.fromkeys(u for u in urls if u))
    added, tried = register(urls, log, dry=a.dry_run)
    if "linkedin" in srcs:
        # names -> careers pages, each registered the moment it resolves
        streamed = []
        def on_url(u):
            k = register_one(u, log, dry=a.dry_run)
            if k:
                streamed.append(k)
        names = src_linkedin(markets, titles, log, max_searches=a.max_searches)
        companies_to_urls(names, log, budget=a.budget, on_url=on_url)
        added += streamed
        tried += len(streamed)
    print(f"  tenants: {len(urls)} urls seen, {tried} new boards tried, {len(added)} registered"
          + (": " + ", ".join(added) if added else ""))


if __name__ == "__main__":
    main()
