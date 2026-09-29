#!/usr/bin/env python3
"""
quota.py — applications per company: how many were sent, and the limit on them.

Every company takes at most apply.default_quota applications in a rolling window
(5 in 30 days); an entry under apply.quotas replaces it for that company (OpenAI's
portal: 5 in 180 days). A portal that refused a filing over its own limit
(data/quota_blocks.json, written by the filing) is full until the date there.

Counted from the queue cards, the one record of what was sent:
  sent      submitted, unconfirmed (maybe sent), submitting — dated by submitted_at
  waiting   cards that may still be sent: pending, needs_input, later, exploring,
            approved, manual
Filing (replay.py) holds an approved card while its company is at the limit; tailoring
(autotailor.py) counts the waiting cards too, so it prepares no card the limit would hold.
Before ranking (daily.py's "hold" stage), hold() parks a full company's unworked postings as
status 'held' — rank, screen and tailoring read only 'new', so no time or Claude call goes
to a company that cannot take an application — and puts them back to 'new' once it has room.

    python -m jobpilot.core.quota          # the table, one row per company (./jobpilot companies)
    python -m jobpilot.core.quota --hold   # park / release postings by the limit [--dry-run]
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3

from jobpilot.core import cards as CD
from jobpilot.core.config import cfg
from jobpilot.core.paths import DATA

BLOCKS = os.path.join(DATA, "quota_blocks.json")
DB = os.path.join(DATA, "state.db")
SENT = ("submitted", "unconfirmed", "submitting")
WAITING = ("pending", "needs_input", "later", "exploring", "approved", "manual")
COLUMNS = ("Company", "Submitted", "Sent in window", "Limit", "Room now", "Next slot",
           "Waiting", "Last submitted", "Note")


def key(company):
    return re.sub(r"[^a-z0-9]+", "", (company or "").lower())


def limit(k):
    """(max, window_days) for a company key; max 0 is no limit."""
    q = (cfg("apply.quotas", {}) or {}).get(k) or cfg("apply.default_quota", {}) or {}
    return int(q.get("max", 0)), int(q.get("window_days", 30))


def _time(s):
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def companies(now=None):
    """{key: row} for every company with a card or a quota: its name, the send times (all
    time, maybe-sent included) and the confirmed submissions among them, cards waiting, the limit, sent inside the window, room left (None: no limit),
    when the next slot opens, and a portal block."""
    now = now or dt.datetime.now()
    rows = {}
    for d in CD.cards():
        k = key(d.get("company"))
        if not k:
            continue
        r = rows.setdefault(k, {"company": d.get("company"), "sent": [], "submitted": [], "waiting": 0})
        if d.get("status") in SENT:
            # an unparseable date counts as now, erring toward caution
            when = _time(d.get("submitted_at") or d.get("created")) or now
            r["sent"].append(when)                   # counts toward the limit, maybe-sent included
            if d.get("status") == "submitted":
                r["submitted"].append(when)          # the portal confirmed it, or he filed it by hand
        elif d.get("status") in WAITING:
            r["waiting"] += 1
    try:
        blocks = json.load(open(BLOCKS))
    except (OSError, ValueError):
        blocks = {}
    for k in set(blocks) | set(cfg("apply.quotas", {}) or {}):
        rows.setdefault(k, {"company": k, "sent": [], "submitted": [], "waiting": 0})
    for k, r in rows.items():
        mx, days = limit(k)
        inside = sorted(t for t in r["sent"] if t > now - dt.timedelta(days=days))
        r.update(max=mx, window=days, used=len(inside), last=max(r["submitted"], default=None),
                 room=mx - len(inside) if mx else None, blocked=None,
                 # the send that must leave the window before one more fits
                 opens=inside[len(inside) - mx] + dt.timedelta(days=days) if mx and len(inside) >= mx else None)
        until = _time((blocks.get(k) or {}).get("until"))
        if until and until > now:                    # the portal said its limit is reached
            r.update(room=0, blocked=until, opens=max(until, r["opens"] or until))
    return rows


def room(company, waiting=False, rows=None):
    """Applications the company can still take now (None: no limit). With waiting, the
    cards that may still be sent count against it too."""
    rows = companies() if rows is None else rows
    r = rows.get(key(company))
    if r is None:
        return limit(key(company))[0] or None
    if r["room"] is None:
        return None
    return r["room"] - (r["waiting"] if waiting else 0)


def hold(dry=False):
    """Park the postings of every company that can take no more work ('new' -> 'held'), and put
    back those of a company that has room again ('held' -> 'new'). Full: no room once the
    cards still waiting are counted, as tailoring counts them. Nothing is dropped.
    (held, released, {company: when a slot opens})."""
    rows = companies()
    full = {k: r for k, r in rows.items() if r["room"] is not None and r["room"] - r["waiting"] <= 0}
    con = sqlite3.connect(DB, timeout=30)
    held = released = 0
    opens = {}
    for jk, co, st in con.execute("SELECT key, company, status FROM jobs WHERE status IN ('new', 'held')").fetchall():
        k = key(co)
        if st == "new" and k in full:
            held += 1
            opens[full[k]["company"]] = full[k]["opens"]
            if not dry:
                con.execute("UPDATE jobs SET status='held' WHERE key=?", (jk,))
        elif st == "held" and k not in full:
            released += 1
            if not dry:
                con.execute("UPDATE jobs SET status='new' WHERE key=?", (jk,))
    con.commit()
    con.close()
    return held, released, opens


def table(now=None):
    """COLUMNS rows, the company with the most submitted first."""
    out = []
    for r in companies(now).values():
        day = lambda t: f"{t:%Y-%m-%d}" if t else ""
        maybe = len(r["sent"]) - len(r["submitted"])
        note = "; ".join(x for x in (f"portal refused until {day(r['blocked'])}" if r["blocked"] else "",
                                     f"+{maybe} maybe sent (unconfirmed)" if maybe else "") if x)
        out.append([r["company"], len(r["submitted"]), r["used"],
                    f"{r['max']} / {r['window']} days" if r["max"] else "none",
                    "" if r["room"] is None else max(r["room"], 0), day(r["opens"]), r["waiting"],
                    day(r["last"]), note])
    out.sort(key=lambda x: (-x[1], -x[2], -x[6], str(x[0]).lower()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", action="store_true", help="park / release postings by the limit (before ranking)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.hold:
        n, back, opens = hold(a.dry_run)
        on = ", ".join(f"{c} (room {t:%Y-%m-%d})" if t else c for c, t in sorted(opens.items()))
        print(f"  {'would hold' if a.dry_run else 'held'} {n} posting(s) of companies at their limit"
              + (f": {on}" if on else "") + f"; {'would release' if a.dry_run else 'released'} {back}")
        return
    rows = [[str(c) for c in r] for r in table()]
    w = [max(len(x) for x in col) for col in zip(COLUMNS, *rows)]
    line = lambda cells: "  " + "  ".join(c.ljust(n) for c, n in zip(cells, w)).rstrip()
    print(line(COLUMNS))
    print("  " + "  ".join("-" * n for n in w))
    for r in rows:
        print(line(r))
    full = [r[0] for r in rows if r[4] == "0"]
    print(f"\n  {len(rows)} companies, {sum(int(r[1]) for r in rows)} submitted; at the limit: {', '.join(full) or 'none'}")


if __name__ == "__main__":
    main()
