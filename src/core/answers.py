#!/usr/bin/env python3
"""
answers.py — the applicant's stored answers and what the stages read about a job:

    load(name)            a config file (answers.yaml, targets.yaml, ...)
    load_tenant(tenant)   one employer portal's learned answers: applications/_tenants/<tenant>.yaml —
                          its answers (short answers to questions no stored fact covers) and its
                          entries (the list entry that stands for a stored fact it cannot hold)
    question_key(q)       the stable key of a learned question: employer:<key> / learned:<key>
    preflight(answers)    refuse to open a browser while answers.yaml holds a TODO
    read_jd(slug)         the meta block tailor/scaffold.py wrote into applications/<slug>/JD.md
    detect_market(...)    which market (salary block, sponsorship row) a job is in
"""
import hashlib
import os
import re
import sys

import yaml

from jobpilot.core.paths import CONFIG, APPLICATIONS

TENANTS = os.path.join(APPLICATIONS, "_tenants")


def load(name):
    with open(os.path.join(CONFIG, name)) as f:
        return yaml.safe_load(f)


def read_jd(company):
    """Pull the meta block tailor/scaffold.py wrote into applications/<company>/JD.md."""
    path = os.path.join(APPLICATIONS, company, "JD.md")
    if not os.path.isfile(path):
        sys.exit(f"No JD.md for '{company}'. Run: ./jobpilot apply <url>")
    text = open(path, encoding="utf-8").read()
    meta = {}
    for key in ("Company", "Role / Title", "Location", "Platform / How applying",
                "Autofill route", "Apply URL", "Link"):
        m = re.search(rf"^- \*\*{re.escape(key)}:\*\*\s*(.*)$", text, re.M)
        if m:
            meta[key] = m.group(1).strip()
    return meta, text


# Checked against the LOCATION first and only then the JD body. Order matters:
# specific markets before the USA, whose old " us " hint matched ordinary prose
# like "work with us" and priced an Abu Dhabi role in USD.
MARKET_HINTS = [
    ("uae", ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah"]),
    ("saudi", ["saudi", "riyadh", "neom", "jeddah", "dhahran", "ksa"]),
    ("netherlands", ["netherlands", "amsterdam", "eindhoven", "utrecht",
                     "rotterdam", "holland", "the hague", "den haag"]),
    ("australia", ["australia", "sydney", "melbourne", "brisbane", "canberra",
                   "perth", "adelaide"]),
    ("usa", ["united states", "u.s.a", "usa", "california", "new york",
             "seattle", "austin", "boston", "san francisco", "chicago",
             ", ca", ", ny", ", wa", ", tx", ", ma", ", il", ", va"]),
]


def market_hints():
    """MARKET_HINTS plus every market's country and hubs from targets.yaml, so a
    city the research already lists (Munich, Dublin, Zurich) finds its salary
    block. Munich once fell through to the USD default on a German form."""
    out = [(m, list(h)) for m, h in MARKET_HINTS]
    try:
        t = yaml.safe_load(open(os.path.join(CONFIG, "targets.yaml"))) or {}
        for m in t.get("markets") or []:
            if m.get("id") == "remote_india":
                continue
            hints = [str(m.get("country") or "").lower()] + [str(h).lower() for h in (m.get("hubs") or [])]
            hints = [h for h in hints if len(h) > 3]
            row = next((r for r in out if r[0] == m["id"]), None)
            if row:
                row[1].extend(h for h in hints if h not in row[1])
            else:
                out.append((m["id"], hints))
    except Exception:
        pass
    return out


def detect_market(location, jd_text=""):
    """Which salary block applies. The LOCATION decides; the JD is a fallback only."""
    loc = (location or "").lower()
    hints_by_market = market_hints()
    for market, hints in hints_by_market:
        if any(h in loc for h in hints):
            return market
    if "remote" in loc:
        return "remote_india"
    # Only if the location said nothing useful, and only on the opening lines
    # where a real posting states where the role sits.
    head = (jd_text or "")[:1200].lower()
    for market, hints in hints_by_market:
        if any(h in head for h in hints):
            return market
    return "default"


def preflight(answers):
    """Refuse to open a browser if any value would type the literal 'TODO'."""
    bad = []

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif o == "TODO" and not path.endswith(".gpa"):
            bad.append(path)

    walk(answers)
    if bad:
        sys.exit("Unresolved TODO fields in answers.yaml:\n  " + "\n  ".join(bad))


def question_key(question):
    """A learned question's stable key: from its words, never its place in the file — a
    record's learned:<key> must mean the same answer after the file changes."""
    q = re.sub(r"\s+", " ", re.sub(r"[*∗]", "", question or "")).strip().lower()
    return hashlib.sha1(q.encode()).hexdigest()[:8]


def tenant_path(tenant):
    return os.path.join(TENANTS, re.sub(r"[^a-z0-9]+", "-", (tenant or "").lower()).strip("-") + ".yaml")


def load_tenant(tenant):
    """{"tenant", "company", "answers": [...], "entries": {stored fact key: {entry, stored, source}}}."""
    p = tenant_path(tenant)
    if not tenant or not os.path.isfile(p):
        return {"tenant": tenant, "answers": [], "entries": {}}
    with open(p) as f:
        d = yaml.safe_load(f) or {}
    return {"tenant": tenant, "company": d.get("company"), "answers": d.get("answers") or [],
            "entries": d.get("entries") or {}}


def load_tenant_facts(tenant):
    """This portal's entries for stored facts it cannot hold as written."""
    return load_tenant(tenant)["entries"]





# --------------------------------------------------------------------------
