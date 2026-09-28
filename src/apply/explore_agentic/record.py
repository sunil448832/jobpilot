"""record.py — one application's record of his answers, applications/<slug>/explore.json:

    {"url", "platform", "reached_end",
     "placeholders": {question: {"used", "field", "candidates", "page", "answer": his or absent}},
     "attempts": [{"at", "outcome", "why"}]}

Written by exploration (session.keep_placeholders), answered from the phone (apply_answers:
review/serve.py, review/bot.py), read by filing (calls.approved_answers), each submit
attempt noted (note_attempt). What the agent did is calls.json (calls.py), not this file.
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
    """A skeleton record when the application is scaffolded (tailor/scaffold.py)."""
    if not os.path.isfile(path_for(slug)):
        save(slug, {"url": url, "platform": platform, "placeholders": {}, "attempts": []})
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


def note_attempt(slug, outcome, **extra):
    doc = load(slug)
    doc.setdefault("attempts", []).append({"at": dt.datetime.now().isoformat(timespec="seconds"),
                                           "outcome": outcome, **extra})
    save(slug, doc)
