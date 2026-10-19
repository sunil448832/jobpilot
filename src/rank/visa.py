#!/usr/bin/env python3
"""
visa.py — what a posting says about visa sponsorship, read by keywords (no Claude).

Runs after intake and before rank, so ranking and the tailor pick both know it:

    explicit  it offers sponsorship or visa support ("we sponsor visas",
              "visa sponsorship available", "support for relocation and visas")
    implicit  a hint, not a promise: sponsorship case by case, relocation help,
              immigration support, hiring internationally — or a market where every
              expat hire is sponsored (visa.implicit_markets in config.yaml)
    silent    says nothing either way; "sponsorship is not guaranteed" is silent too
    no        it will not sponsor, or wants existing work authorisation — and
              offers sponsorship nowhere in the posting

A `no` is rejected here (status 'rejected') and never ranked. The screen agent reads
the full eligibility text later and gives its own answer (jobs.screen_visa), which
wins over this one where both exist.

Usage:
    python -m jobpilot.rank.visa            # tag every untagged role, reject the `no`s
    python -m jobpilot.rank.visa --all      # tag again (after changing the patterns)
    python -m jobpilot.rank.visa --report   # counts per tag
"""
import argparse
import html
import re
import sys

from jobpilot.core.config import cfg
from jobpilot.discover.intake import db

# a refusal: these win over everything
NO = [
    r"(not|unable to|cannot|can't|won't|will not|do not|does not|don't|doesn't|no longer|are not able to)"
    r"\s+(\w+\s+){0,3}(sponsor|offer\w* (\w+ )?sponsorship|provide (\w+ )?sponsorship|support (\w+ )?(visa|sponsorship))",
    r"\bno\s+(visa\s+|immigration\s+)?sponsorship",
    r"sponsorship\s+(is\s+)?(not|un)\s*(available|offered|provided|possible)",
    r"without\s+(the\s+)?(need\s+(for|of)\s+)?(\w+\s+){0,2}sponsorship",
    r"(citizens?|green card holders?|permanent residents?)\s+only",
]
# needs existing authorisation: a `no` unless the posting also offers sponsorship
AUTH_ONLY = [
    r"must\s+(already\s+)?(be|have)\s+(\w+\s+){0,2}(authori[sz]ed|eligible|the right|permission)\s+to\s+work",
    r"(existing|current|valid)\s+(\w+\s+)?(work authori[sz]ation|right to work|work permit)\s+(is\s+)?required",
]
# hedged: "sponsorship for this role is not guaranteed" offers nothing either way
HEDGED = [r"sponsorship[^.]{0,40}not\s+guaranteed", r"not\s+guaranteed[^.]{0,40}sponsorship"]
EXPLICIT = [
    r"(visa|immigration|work permit)\s+sponsorship\s+(is\s+)?(available|offered|provided|possible|included)",
    r"sponsorship\s+(is\s+)?(available|offered|provided)",
    r"\bwe\s+(will\s+|can\s+|do\s+|are able to\s+|are happy to\s+|gladly\s+)?sponsor",
    r"(offer|offers|provide|provides|include|includes)\s+(\w+\s+){0,4}(visa|immigration)\s+(sponsorship|support|assistance)",
    r"support\s+(for|with)\s+(\w+\s+){0,3}visas?\b",
    r"sponsor(ing|s)?\s+(\w+\s+){0,2}(visas?|work permits?|h-?1b|blue cards?|employment pass|critical skills|"
    r"kennismigrant|highly skilled migrant|subclass 482)",
    r"(relocation\s+(and|&)\s+visa|visa\s+(and|&)\s+relocation)",
    r"recogni[sz]ed\s+sponsor",
    r"open\s+to\s+sponsor",
]
IMPLICIT = [
    r"(sponsor|immigration|visa)[^.]{0,80}case[- ]by[- ]case|case[- ]by[- ]case[^.]{0,80}(sponsor|immigration|visa)",
    r"sponsorship\s+(may|might|could|can)\s+be\s+(available|considered|possible|discussed)",
    r"\b(may|might|could)\s+(be able to\s+)?sponsor",
    r"relocation\s+(package|assistance|support|bonus|stipend|allowance|budget)",
    r"(help|assist|support)\w*\s+(\w+\s+){0,2}(with\s+)?(\w+\s+)?relocat",
    r"immigration\s+(support|assistance|help|counsel|lawyers?|attorneys?)",
    r"global\s+mobility",
    r"(we\s+)?(hire|hiring|recruit\w*)\s+(globally|internationally|worldwide|from anywhere)",
    r"open\s+to\s+(international|global|overseas)\s+(candidates|applicants|talent)",
]
ORDER = ("explicit", "implicit", "silent", "no")    # best first


NEGATED = re.compile(r"(\bnot|n't|\bunable|\bcannot|\bno|\bnever)\b[^.]{0,30}$")


def _find(patterns, t, positive=False):
    """The first match, with some context. A positive pattern does not count when the
    words just before it negate it ("will not sponsor visas")."""
    for p in patterns:
        for m in re.finditer(p, t):
            if positive and NEGATED.search(t[max(0, m.start() - 30):m.start()]):
                continue
            return " ".join(t[max(0, m.start() - 40):m.end() + 40].split())
    return None


def classify(jd, market=None, company=None):
    """(tag, evidence) — tag one of ORDER; evidence is the matched text, or why. A posting
    that says nothing, by an employer on its country's sponsor register (sponsors.py: the
    IND list, Ireland's permits issued), is explicit: the government list says it sponsors."""
    tag, why = _classify(jd, market)
    from jobpilot.rank import sponsors
    reg_markets = sponsors.register_markets()            # config.yaml visa.sponsor_registers
    if tag in ("silent", "implicit") and company and market in reg_markets:
        hit = sponsors.known(company, market)
        if hit:
            return "explicit", hit
    # Switzerland publishes no such list (permits are case by case, under quotas): a
    # multinational that sponsors in NL or IE is a likely yes there, a small Swiss firm is not
    if tag == "silent" and company and market in (cfg("visa.proxy_markets", ["switzerland"]) or []):
        hit = next((h for h in (sponsors.known(company, m) for m in reg_markets if m != market) if h), None)
        if hit:
            return "implicit", f"sponsors elsewhere — {hit}"
    return tag, why


def _classify(jd, market=None):
    # boards hand some JDs over as escaped HTML ("aren&#39;t", "<strong>")
    t = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(jd or "")))
    t = " ".join(t.lower().replace("\u2019", "'").split())
    yes = _find(EXPLICIT, t, positive=True)
    hit = _find(NO, t)
    if hit and not yes:          # "we do sponsor visas, but not for every role" is still an offer
        return "no", hit
    if _find(HEDGED, t) and not yes:
        return "silent", _find(HEDGED, t)
    auth = _find(AUTH_ONLY, t)
    if yes:
        return "explicit", yes
    if auth:
        return "no", auth
    hint = _find(IMPLICIT, t, positive=True)
    if hint:
        return "implicit", hint
    if market and market in (cfg("visa.implicit_markets", []) or []):
        return "implicit", f"{market}: sponsorship is the norm"
    return "silent", ""


def ensure_columns(con):
    for col in ("visa TEXT", "visa_why TEXT", "screen_visa TEXT"):
        try:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="tag every role again, not just untagged ones")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    con = db()
    ensure_columns(con)
    if a.report:
        for st in ("new", "queued", "held"):
            row = dict(con.execute("SELECT COALESCE(screen_visa, visa, '?'), COUNT(*) FROM jobs "
                                   "WHERE status=? GROUP BY 1", (st,)).fetchall())
            print(f"  {st:<7} " + "  ".join(f"{k} {row.get(k, 0)}" for k in (*ORDER, "?")))
        return
    where = "status IN ('new','queued','held')" + ("" if a.all else " AND visa IS NULL")
    rows = con.execute(f"SELECT key, company, title, market, status, jd FROM jobs WHERE {where}").fetchall()
    n = dict.fromkeys(ORDER, 0)
    for key, co, title, market, status, jd in rows:
        tag, why = classify(jd, market, co)
        n[tag] += 1
        con.execute("UPDATE jobs SET visa=?, visa_why=? WHERE key=?", (tag, why[:200], key))
        # a refusal ends the role before ranking — unless it is already a card (queued)
        if tag == "no" and status in ("new", "held"):
            con.execute("UPDATE jobs SET status='rejected', reason=? WHERE key=?",
                        (f"visa: {why[:160]}", key))
            print(f"  ✗ {co[:16]:<18} {title[:40]:<42} {why[:70]}")
    con.commit()
    print(f"  tagged {len(rows)}: " + ", ".join(f"{k} {v}" for k, v in n.items())
          + f" — {n['no']} rejected" if rows else "  nothing to tag")


if __name__ == "__main__":
    main()
