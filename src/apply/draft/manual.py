#!/usr/bin/env python3
"""
manual.py — when a portal cannot be autofilled, hand Sunil everything to fill it.

Phenom (TII, G42, Inception42 — the Gulf priorities), Workday and unknown portals
are not driven by browser.py. Until now a good-fit role on one of them produced
NO queue item: tailored, built, then silently dropped. Now it becomes a queue item
with status `manual` carrying a copy-ready pack — every standard field a form
asks for, the tailored summary and skills, the resume files, and two free-text
answers drafted by Claude — served as a phone page with copy buttons. He fills
the form by hand and taps "Mark as submitted".
"""
import datetime as dt
import json
import os
import re

import yaml

from jobpilot.core import cards as CD  # noqa: E402
from jobpilot.core.paths import CONFIG, DATA, APPLICATIONS  # noqa: E402


def _answers():
    return yaml.safe_load(open(os.path.join(CONFIG, "answers.yaml"), encoding="utf-8")) or {}


def _market_country(market):
    return {"usa": "us", "uk": "uk", "netherlands": "eu", "germany": "eu", "ireland": "eu",
            "luxembourg": "eu", "switzerland": "eu", "uae": "uae", "saudi": "saudi",
            "australia": "australia", "remote_india": "india"}.get(market or "", "")


def _tailored_sections(slug):
    """Summary + Skills from the built ats.md (derived from the tailored .tex)."""
    p = os.path.join(APPLICATIONS, slug, "ats.md")
    try:
        md = open(p, encoding="utf-8").read()
    except OSError:
        return "", ""
    def section(name):
        m = re.search(rf"(?ms)^## {name}\n(.*?)(?=^## |\Z)", md)
        return (m.group(1).strip() if m else "")
    return section("Summary"), section("Skills").replace("- ", "")


def pack(p, portal):
    """The copy-ready fields, in the order a form usually asks for them."""
    a = _answers(); per = a.get("personal", {}); loc = a.get("location", {}); links = a.get("links", {})
    wa = a.get("work_authorization", {}); av = a.get("availability", {}); ex = a.get("experience_summary", {})
    comp = (a.get("compensation", {}) or {}).get("by_market", {}).get(p.get("market"), {})
    edu = a.get("education", [])
    country = _market_country(p.get("market"))
    auth = wa.get(country, {}) if isinstance(wa.get(country), dict) else {}
    visa = (f"Citizen of {wa.get('citizenship','India')}, currently based in {wa.get('current_work_country','India')}. "
            + ("Will require visa sponsorship for this role." if auth.get("requires_sponsorship", True)
               else "Authorised to work without sponsorship."))
    summary, skills = _tailored_sections(p["slug"])
    e0 = edu[0] if isinstance(edu, list) and edu else (edu if isinstance(edu, dict) else {})
    fields = [
        ("Full name", per.get("full_name", "")), ("First name", per.get("first_name", "")),
        ("Last name", per.get("last_name", "")), ("Email", per.get("email", "")),
        ("Phone", per.get("phone", "")),
        ("Location", ", ".join(str(loc[k]) for k in ("street", "city", "state", "postal_code", "country") if loc.get(k))),
        ("LinkedIn", links.get("linkedin", "")), ("GitHub", links.get("github", "")),
        ("Current title", ex.get("current_title", "")), ("Total experience", ex.get("total_years_phrase", "")),
        ("Highest degree", (e0.get("degree") if isinstance(e0, dict) else "") or ex.get("highest_degree", "")),
        ("Notice period / earliest start", av.get("notice_period") or av.get("earliest_start") or "2 months"),
        ("Salary expectation", comp.get("expected_text", "")),
        ("Work authorisation / visa", visa),
        ("Employment gap (if asked)", ex.get("employment_gap_explanation", "")),
        ("Professional summary (tailored)", summary),
        ("Skills (tailored)", skills),
    ]
    return [(k, str(v).strip()) for k, v in fields if str(v).strip()]


def create(p, portal, reason=""):
    """Write the manual card. Returns its path."""
    app = os.path.join(APPLICATIONS, p["slug"])
    item_id = f"{p['slug']}-{dt.datetime.now():%m%d%H%M}"
    item = {
        "id": item_id, "company": p["company"], "role": p["title"], "company_slug": p["slug"],
        "location": p.get("location", ""), "market": p.get("market", ""), "portal": portal,
        "url": p["url"], "apply_url": p["url"], "status": "manual", "manual": True,
        "reason": reason or f"portal '{portal}' is not autofillable — apply by hand",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "resume": os.path.join(app, "sunil_resume.docx"),
        "resume_pdf": os.path.join(app, "sunil_resume.pdf"),
        "score": p.get("score"), "fit": p.get("fit"),
        "fields": dict(pack(p, portal)),
        # Two free-text answers most forms ask for. Claude drafts 2-3 options each
        # (draft_questions); Sunil copies the one he likes.
        "questions": [
            {"label": "Cover note / why this role (3-5 sentences, first person)", "kind": "text", "options": []},
            {"label": "Summary of relevant experience for this role (4-6 sentences, first person)", "kind": "text", "options": []},
        ],
        "warnings": [],
    }
    return CD.save(item)
