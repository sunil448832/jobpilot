#!/usr/bin/env python3
"""
salary.py — parse posted pay in ANY currency, convert, compare to expectations.

Currency is never a reason to reject a job. A role posted in INR, SGD or PLN can
still beat the target once converted — so parse whatever the posting states,
normalise it to annual USD, and judge it on VALUE.

Rates come from frankfurter.app (free, ECB-backed, no key) and are cached for a
day; a static fallback keeps everything working offline. Gulf currencies are
USD-pegged, so those rates are effectively fixed.

Usage:
    python jobs/salary.py --test
    python jobs/salary.py --text "AED 45,000 - 55,000 per month"
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

import requests
import yaml

from jobpilot.core.paths import (SRC as JOBS_DIR, TOOL, CONFIG, DATA, TRACKING, POLICY,  # noqa: E402
                   RESUME, APPLICATIONS, MEMORY)
CACHE = os.path.join(DATA, ".rates.json")

# Fallback only. USD-pegged currencies (AED 3.6725, SAR 3.75) are fixed by policy.
STATIC = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "AED": 0.2723, "SAR": 0.2667,
          "AUD": 0.655, "SGD": 0.745, "INR": 0.0120, "CAD": 0.735, "CHF": 1.13,
          "JPY": 0.0067, "PLN": 0.25, "SEK": 0.095, "NOK": 0.094, "DKK": 0.145,
          "ZAR": 0.055, "BRL": 0.185, "MXN": 0.055, "QAR": 0.2747, "KWD": 3.26,
          "BHD": 2.65, "OMR": 2.60, "NZD": 0.60, "HKD": 0.128, "ILS": 0.27}

SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY",
          "د.إ": "AED", "﷼": "SAR", "A$": "AUD", "S$": "SGD", "C$": "CAD",
          "₪": "ILS", "HK$": "HKD", "NZ$": "NZD"}

CODES = set(STATIC) | {"RS", "RM", "KR", "ZL", "DHS", "AED", "SR"}
ALIAS = {"RS": "INR", "DHS": "AED", "SR": "SAR", "US$": "USD", "USD$": "USD"}


def rates():
    """Live USD-per-unit rates, cached a day, static fallback."""
    today = dt.date.today().isoformat()
    if os.path.isfile(CACHE):
        try:
            c = json.load(open(CACHE))
            if c.get("date") == today:
                return c["rates"]
        except Exception:
            pass
    out = dict(STATIC)
    try:
        syms = ",".join(k for k in STATIC if k != "USD")
        d = requests.get(f"https://api.frankfurter.app/latest?from=USD&to={syms}",
                         timeout=12).json()
        for cur, per_usd in (d.get("rates") or {}).items():
            if per_usd:
                out[cur] = 1.0 / per_usd          # frankfurter gives units per USD
        out["USD"] = 1.0
        # Pegged: keep the peg, not a drifting quote.
        out["AED"], out["SAR"] = 0.2723, 0.2667
        json.dump({"date": today, "rates": out}, open(CACHE, "w"))
    except Exception:
        pass
    return out


PERIOD = [
    (r"per\s*hour|/\s*h(?:r|our)?\b|hourly|an hour", 1920),      # 40h x 48wk
    (r"per\s*day|/\s*day|daily|a day", 240),
    (r"per\s*week|/\s*w(?:k|eek)|weekly|a week", 48),
    (r"per\s*month|/\s*mo(?:nth)?\b|monthly|a month|p\.?m\.?\b", 12),
    (r"per\s*(?:annum|year)|/\s*(?:yr|year)|annual(?:ly)?|a year|p\.?a\.?\b", 1),
]

# Indian grouping (45,00,000 = 4.5M) alternates 2-2-3 and must be tried BEFORE
# the western 3-3-3 form, which would otherwise match only part of it.
NUM = (r"(\d{1,2}(?:,\d{2})+,\d{3}"          # 1,20,00,000  (Indian)
       r"|\d{1,3}(?:[,\s]\d{3})+"             # 120,000,000  (western)
       r"|\d+(?:\.\d+)?)"                     # 120000 / 1.2
       r"\s*([kKmM]|lakh|lakhs|lac|crore|crores)?(?![a-zA-Z])")


MULT = {"k": 1_000, "m": 1_000_000, "lakh": 100_000, "lakhs": 100_000,
        "lac": 100_000, "crore": 10_000_000, "crores": 10_000_000}


def _val(num, suffix):
    v = float(re.sub(r"[,\s]", "", num))
    return v * MULT.get((suffix or "").lower(), 1)


def parse_salary(text):
    """Find posted pay. Returns a list of {min,max,currency,period_mult,raw}."""
    if not text:
        return []
    t = re.sub(r"\s+", " ", text)
    found = []
    cur_alt = "|".join(sorted((re.escape(c) for c in list(SYMBOL) + list(CODES)),
                              key=len, reverse=True))
    # currency before the number, or immediately after it
    pat = re.compile(
        rf"(?:(?P<c1>{cur_alt})\s*)?{NUM}"
        rf"(?:\s*(?:-|–|—|to)\s*(?:(?:{cur_alt})\s*)?{NUM})?"
        rf"(?:\s*(?P<c2>{cur_alt}))?", re.I)
    for m in pat.finditer(t):
        cur = (m.group("c1") or m.group("c2") or "").strip()
        if not cur:
            continue
        cur = SYMBOL.get(cur, ALIAS.get(cur.upper(), cur.upper()))
        if cur not in STATIC:
            continue
        lo = _val(m.group(2), m.group(3))
        hi = _val(m.group(4), m.group(5)) if m.group(4) else lo
        if lo < 100:                       # "3 USD" is not a salary
            continue
        tail = t[m.end():m.end() + 40].lower()
        head = t[max(0, m.start() - 40):m.start()].lower()
        mult = None
        for rx, mm in PERIOD:
            if re.search(rx, tail) or re.search(rx, head):
                mult = mm
                break
        if mult is None:
            # No period stated: infer from magnitude in that currency.
            usd_guess = lo * STATIC.get(cur, 1)
            mult = 1 if usd_guess >= 25_000 else 12
        found.append({"min": lo, "max": hi, "currency": cur,
                      "period_mult": mult, "raw": m.group(0).strip()[:48]})
    return found


def annual_usd(entry, rt=None):
    rt = rt or rates()
    r = rt.get(entry["currency"], STATIC.get(entry["currency"], 1.0))
    return entry["min"] * entry["period_mult"] * r, entry["max"] * entry["period_mult"] * r


def expectation_usd(market, answers, rt=None):
    """Sunil's ask for a market, in annual USD."""
    rt = rt or rates()
    blocks = answers["compensation"]["by_market"]
    b = blocks.get(market) or answers["compensation"]["default"]
    cur = b.get("currency", "USD")
    if b.get("expected_annual"):
        amount = float(str(b["expected_annual"]).replace(",", ""))
    elif b.get("expected_monthly"):
        amount = float(str(b["expected_monthly"]).replace(",", "")) * 12
    else:
        return None
    return amount * rt.get(cur, STATIC.get(cur, 1.0))


# Bands for a senior ML/AI engineer (~5 yrs) with a GenAI premium, in LOCAL
# currency per year. Sourced from Sunil's own market research in target-companies/
# (target-companies-*.md) plus published recruiter guides. These are ESTIMATES
# and are always labelled as such — never presented as a posted figure.
MARKET_BANDS = {
    "uae":          ("AED", 420_000, 600_000),   # AED 35-50k/month, GenAI premium
    "saudi":        ("SAR", 360_000, 480_000),   # SAR 30-40k/month
    "netherlands":  ("EUR",  75_000,  95_000),
    "ireland":      ("EUR",  80_000, 105_000),   # Dublin; US big-tech EMEA base lifts it
    "germany":      ("EUR",  75_000, 100_000),   # Munich > Berlin by ~10%
    "luxembourg":   ("EUR",  85_000, 115_000),   # high-wage, finance-inflated, tiny market
    "switzerland":  ("CHF", 135_000, 180_000),   # highest band anywhere, and taxed lightly
    "australia":    ("AUD", 165_000, 200_000),
    "usa":          ("USD", 180_000, 250_000),
    "remote_india": ("USD",  90_000, 150_000),   # foreign employer, EOR/contract
    "default":      ("USD",  90_000, 150_000),
}


def company_comparables(company, market, con):
    """Strongest estimate: what THIS company pays for similar roles elsewhere."""
    if con is None:
        return None
    # MUST be same-market: OpenAI's San Francisco bands say nothing about an
    # Abu Dhabi role, and using them estimated a Gulf job at $325k.
    try:
        rows = con.execute(
            "SELECT jd FROM jobs WHERE company=? AND market=? AND jd IS NOT NULL "
            "LIMIT 60", (company, market)).fetchall()
    except Exception:
        return None
    rt = rates()
    highs = []
    for (jd,) in rows:
        for e in parse_salary(jd or ""):
            lo, hi = annual_usd(e, rt)
            if 20_000 <= hi <= 1_500_000:
                highs.append(hi)
    if len(highs) < 2:
        return None
    highs.sort()
    return highs[len(highs) // 2]              # median of stated highs


def estimate_pay(market, company=None, con=None):
    """Estimate annual USD when a posting states nothing. Always labelled."""
    comp = company_comparables(company, market, con) if company else None
    if comp:
        return {"estimate_usd": comp, "confidence": "medium",
                "basis": f"median of stated pay in other {company} {market} postings"}
    cur, lo, hi = MARKET_BANDS.get(market, MARKET_BANDS["default"])
    rt = rates()
    r = rt.get(cur, STATIC.get(cur, 1.0))
    return {"estimate_usd": ((lo + hi) / 2) * r, "confidence": "low",
            "basis": f"{market} senior AI/ML band {cur} {lo:,.0f}-{hi:,.0f}/yr"}


def assess(jd_text, market, answers, company=None, con=None):
    """Compare posted pay to the ask. Currency alone never rejects anything."""
    rt = rates()
    want = expectation_usd(market, answers, rt)
    entries = parse_salary(jd_text)
    if not entries:
        est = estimate_pay(market, company, con)
        hi = est["estimate_usd"]
        return {"posted": False, "estimated": True, "hi_usd": hi,
                "verdict": ("est. above ask" if want and hi >= want else
                            "est. near ask" if want and hi >= want * 0.85 else
                            "est. below ask"),
                "want_usd": want, **est}
    best = None
    for e in entries:
        lo, hi = annual_usd(e, rt)
        if hi < 15_000 or hi > 2_000_000:        # parse noise
            continue
        if best is None or hi > best["hi_usd"]:
            best = {"lo_usd": lo, "hi_usd": hi, **e}
    if best is None:
        est = estimate_pay(market, company, con)
        hi = est["estimate_usd"]
        return {"posted": False, "estimated": True, "hi_usd": hi,
                "verdict": ("est. above ask" if want and hi >= want else
                            "est. near ask" if want and hi >= want * 0.85 else
                            "est. below ask"),
                "want_usd": want, **est}
    verdict = "unknown"
    if want:
        verdict = ("above ask" if best["hi_usd"] >= want else
                   "near ask" if best["hi_usd"] >= want * 0.85 else "below ask")
    return {"posted": True, "estimated": False, "verdict": verdict,
            "want_usd": want, **best}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text")
    ap.add_argument("--market", default="usa")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()

    if a.test:
        rt = rates()
        print(f"  rates loaded ({'live' if os.path.isfile(CACHE) else 'static'}): "
              f"EUR={rt['EUR']:.3f} AED={rt['AED']:.4f} INR={rt['INR']:.5f} AUD={rt['AUD']:.3f}\n")
        cases = [
            "Salary: AED 45,000 - 55,000 per month",
            "€90,000 - €110,000 per year",
            "The base pay range is $180,000 — $250,000 per year",
            "₹45,00,000 per annum",
            "INR 1,20,00,000 per year",
            "A$170,000 – A$210,000 plus super",
            "SGD 12,000 per month",
            "SAR 40,000 monthly",
            "competitive salary and equity",
        ]
        for c in cases:
            got = parse_salary(c)
            if not got:
                print(f"  {c[:44]:<46} -> not stated")
                continue
            e = got[0]
            lo, hi = annual_usd(e, rt)
            print(f"  {c[:44]:<46} -> {e['currency']} "
                  f"{e['min']:,.0f}-{e['max']:,.0f} x{e['period_mult']} "
                  f"= USD {lo:,.0f}-{hi:,.0f}/yr")
        print()
        with open(os.path.join(CONFIG, "answers.yaml")) as f:
            ans = yaml.safe_load(f)
        for mk in ("usa", "uae", "saudi", "netherlands", "ireland", "germany",
                   "luxembourg", "switzerland", "australia", "remote_india"):
            print(f"  ask {mk:<14} = USD {expectation_usd(mk, ans, rt):,.0f}/yr")
        return

    with open(os.path.join(CONFIG, "answers.yaml")) as f:
        ans = yaml.safe_load(f)
    print(json.dumps(assess(a.text or "", a.market, ans), indent=2, default=str))


if __name__ == "__main__":
    main()
