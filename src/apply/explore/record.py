"""record.py — one application's record, applications/<slug>/explore.json.

Written by exploration, read by submit:

    {"url", "platform", "company", "explored_at", "reached_end",
     "pages": [{"step", "entries": [[name, kind, fact, question]...], "menus": {question: tree},
                "actions": [{"kind", "name", "source", "value", "path"}], "rounds": [...]}],
     "placeholders": {question: {"value": used, "options": [...], "answer": his or null}},
     "submit": "Submit", "attempts": [...]}

An action's `source` says where its value comes from on the submit pass:
    fact:<key>           the fact, looked up again for this job
    placeholder:<q>      his answer to question q (approval) — never the placeholder
    button               nothing to fill (a click)
Submit replays `actions` page by page; nothing in the record is a DOM index or a
selector, only role/kind + name, so a re-rendered page is found again.
"""
import datetime as dt
import json
import os
import re

from jobpilot.core.paths import APPLICATIONS

FILE = "explore.json"


def path_for(slug):
    return os.path.join(APPLICATIONS, slug, FILE)


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


def load(slug):
    try:
        with open(path_for(slug), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(slug, doc):
    p = path_for(slug)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    return p


def ensure(slug, url=None, platform=None):
    """A skeleton record when the application is scaffolded (tailor/apply.py)."""
    if not os.path.isfile(path_for(slug)):
        save(slug, {"url": url, "platform": platform, "pages": [], "placeholders": {}, "attempts": []})
    return path_for(slug)


def apply_answers(item, answered):
    """His approved answers {question label: text} into the record's placeholders.
    Returns how many placeholders now carry an answer."""
    slug = item.get("company_slug")
    doc = load(slug)
    ph = doc.get("placeholders") or {}
    by_norm = {norm(q): q for q in ph}
    n = 0
    for label, text in (answered or {}).items():
        q = by_norm.get(norm(label))
        if q and str(text).strip():
            ph[q]["answer"] = str(text).strip()
            ph[q]["answered_at"] = dt.datetime.now().isoformat(timespec="seconds")
            n += 1
    if n:
        doc["placeholders"] = ph
        save(slug, doc)
    return n


def unanswered(doc):
    """Placeholders still without his answer: the submit must not open while any exist."""
    return [q for q, p in (doc.get("placeholders") or {}).items() if not (p.get("answer") or "").strip()]


def note_attempt(slug, outcome, **extra):
    doc = load(slug)
    doc.setdefault("attempts", []).append({"at": dt.datetime.now().isoformat(timespec="seconds"),
                                           "outcome": outcome, **extra})
    save(slug, doc)
