"""
inbox.py — jobs Sunil found himself.

He reads LinkedIn, decides a role is worth it, and pastes the EXTERNAL apply
link (the company's own site / ATS — never a LinkedIn URL) into the review
server's /add page or `./jobpilot add <url>`. Each link becomes a jobs row with
source='inbox', already marked keep with a full fit score: he has reviewed it,
so rank and screen are skipped and autotailor takes these before anything the
scanner found. From there it is the normal flow — tailor, build, fill, review
on the phone, submit after Approve.

    python -m jobpilot.discover.inbox <url> [<url> ...] [--note "..."]
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

from jobpilot.core.paths import DATA
from jobpilot.core.config import cfg

DB = os.path.join(DATA, "state.db")


def add(url, note=""):
    """Fetch the JD from the ATS and file it as a reviewed, tailor-ready row.
    Returns a dict describing what happened (for the page / CLI)."""
    from jobpilot.tailor import apply as apply_mod
    from jobpilot.discover import intake
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"url": url, "ok": False, "why": "not a URL"}
    if "linkedin.com" in url:
        return {"url": url, "ok": False, "why": "LinkedIn URL — paste the company-site apply link instead"}
    try:
        jd = apply_mod.fetch_jd(url)
    except SystemExit as ex:                        # EXPIRED: ... from fetch_jd
        return {"url": url, "ok": False, "why": str(ex)[:160]}
    except Exception as ex:
        return {"url": url, "ok": False, "why": f"could not fetch: {type(ex).__name__}"}
    title = (jd.get("title") or "").strip() or "Untitled role"
    company = (jd.get("company") or "").strip() or "Unknown company"
    location = (jd.get("location") or "").strip()
    text = jd.get("text") or ""
    portal = jd.get("portal") or apply_mod.detect_portal(url)
    apply_url = jd.get("apply_url") or url

    # Market: the usual classifier; a role he chose outside the target markets
    # still goes through, just with the default salary block.
    try:
        t = intake.load_targets()
        market, _ = intake.classify({"location": location, "jd": text}, t)
    except Exception:
        market = None
    if not market:
        from jobpilot.fill.autofill import detect_market
        market = detect_market(location, text)

    key = intake.key_for(company, title, location)
    now = dt.datetime.now().isoformat(timespec="seconds")
    con = sqlite3.connect(DB)
    row = con.execute("SELECT status FROM jobs WHERE key=?", (key,)).fetchone()
    if row and row[0] in ("queued", "applied", "submitted"):
        con.close()
        return {"url": url, "ok": False, "why": f"already {row[0]}: {company} — {title}", "company": company, "title": title}
    reason = "inbox: reviewed by Sunil" + (f" — {note.strip()}" if note and note.strip() else "")
    if row:
        con.execute("UPDATE jobs SET status='new', screen=?, fit=100, score=100, reason=?, url=?, jd=?, seen=?, "
                    "source='inbox', board='inbox', market=? WHERE key=?",
                    ("keep: " + reason, reason, apply_url, text[:12000], now, market, key))
    else:
        con.execute("INSERT INTO jobs(key,source,board,company,title,location,url,remote,posted,seen,score,market,"
                    "route,status,reason,jd,screen,fit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, "inbox", "inbox", company, title, location, apply_url,
                     int("remote" in location.lower()), "", now, 100.0, market, "mobile", "new", reason,
                     text[:12000], "keep: " + reason, 100))
    con.commit()
    con.close()
    return {"url": url, "ok": True, "company": company, "title": title, "location": location,
            "portal": portal, "market": market, "chars": len(text),
            "note": ("JD text is short — page may be JS-rendered" if len(text) < 400 else "")}


def pending():
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT company, title, location, status, seen FROM jobs WHERE source='inbox' "
                       "ORDER BY seen DESC LIMIT 40").fetchall()
    con.close()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--note", default="")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.urls:
        for co, ti, loc, st, seen in pending():
            print(f"  {st:<9} {seen[:10]}  {co[:22]:<22} {ti[:44]:<44} {loc[:24]}")
        return
    for u in a.urls:
        r = add(u, a.note)
        if r["ok"]:
            print(f"  + {r['company']} — {r['title']} ({r['portal']}, {r['market']}, {r['chars']} chars)"
                  + (f"  ! {r['note']}" if r.get("note") else ""))
        else:
            print(f"  x {u[:70]}: {r['why']}")


if __name__ == "__main__":
    main()
