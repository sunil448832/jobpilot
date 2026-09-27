"""reuse.py — page maps kept for reuse, so a page already seen needs no Claude.

    data/maps/<platform>/<company>/<page>.json   one page of one company's form
    data/maps/<platform>/_standard.json          fields every company on a platform
                                                 asks (name, email, resume, EEO...):
                                                 platforms with STANDARD = True
                                                 (Greenhouse, Lever, Ashby)

A map holds entries — name, kind, fact KEY — never a value, so it is safe to
reuse across roles. It is always checked against the live page before use
(mapper.check); when the page has changed, the corrected map REPLACES the
company's one (docs/exploration-plan.md, decisions 1-4).
"""
import datetime as dt
import json
import os
import re

from jobpilot.core.paths import DATA
from jobpilot.apply import platforms

ROOT = os.path.join(DATA, "maps")
# facts every company's form asks the same way: shared across companies
STANDARD_FACT = re.compile(r"^(personal\.|location\.|links\.|eeo\.|file:resume|job\.current_location)")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "page"


def company_key(platform_id, url, company):
    """The tenant for Workday ('adobe'), the board for Greenhouse, else the company."""
    fn = getattr(platforms.get(platform_id), "company_key", None)
    try:
        k = fn(url) if fn else None
    except Exception:
        k = None
    return slug(k or company)


def _path(platform_id, company, step, accepted=True):
    """<page>.json: the map of a page the portal ACCEPTED (it moved on with it).
    <page>.draft.json: the map being worked on — used only while no accepted one exists."""
    return os.path.join(ROOT, slug(platform_id), company, slug(step) + ("" if accepted else ".draft") + ".json")


def _std_path(platform_id):
    return os.path.join(ROOT, slug(platform_id), "_standard.json")


def _read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("entries") or []
    except (OSError, ValueError):
        return []


def load(platform_id, company, step):
    """The cached entries for this page: the company's own map, then the platform's
    standard fields for names the company map does not have."""
    own = _read(_path(platform_id, company, step)) or _read(_path(platform_id, company, step, accepted=False))
    if getattr(platforms.get(platform_id), "STANDARD", False):
        names = {e["name"] for e in own}
        own += [e for e in _read(_std_path(platform_id)) if e["name"] not in names]
    return own


def save(platform_id, company, step, entries, source="", accepted=False):
    """Save the company's map of this page. A draft while it is worked on; ACCEPTED
    once the portal took the page — that one replaces the accepted map, is trusted by
    later runs (still checked control by control), and feeds the platform's standard
    fields. A partial run never overwrites an accepted map with a draft."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    p = _path(platform_id, company, step, accepted)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"platform": platform_id, "company": company, "page": step, "updated": now,
                   "from": source, "accepted": accepted, "entries": entries}, f, indent=1, ensure_ascii=False)
    if accepted:
        try:
            os.remove(_path(platform_id, company, step, accepted=False))
        except OSError:
            pass
    if accepted and getattr(platforms.get(platform_id), "STANDARD", False):
        std = {e["name"]: e for e in _read(_std_path(platform_id))}
        for e in entries:
            if e.get("fact") and STANDARD_FACT.search(e["fact"]):
                std[e["name"]] = e
        with open(_std_path(platform_id), "w", encoding="utf-8") as f:
            json.dump({"platform": platform_id, "updated": now, "entries": list(std.values())},
                      f, indent=1, ensure_ascii=False)
    return p


# ---------------------------------------------------------------- using a saved map on a live page

GROUPED = ("radio-group", "yes-no-buttons", "checkbox-group")


KIND_ROLE = {"text": "textbox", "number": "spinbutton", "search-and-pick": ("combobox", "textbox"),
             "native-select": "combobox", "dropdown": "button", "checkbox": "checkbox", "file": "button"}


def _q(s):
    return re.sub(r"[\s*✱]+$", "", s or "").strip()


def covered(control, entries):
    """Does some entry already stand for this control?"""
    for e in entries:
        # named by its question (the control's own name says nothing): the control under it
        roles = KIND_ROLE.get(e["kind"], ())
        if control.role in (roles if isinstance(roles, tuple) else (roles,)) and _q(e["name"]) \
                and _q(e["name"]) in (_q(control.group), _q(control.above)) and _q(e["name"]) != _q(control.name):
            return True
        grp = e["name"].rsplit(" › ", 1)[0] if " › " in e["name"] else None
        m = re.match(r"^(.*?)\s*#(\d+)$", e["name"].rsplit(" › ", 1)[-1])
        base, nth = (m.group(1), int(m.group(2))) if m else (e["name"].rsplit(" › ", 1)[-1], 1)
        if e["kind"] in GROUPED and control.group and control.group == e["name"]:
            return True
        if grp is not None:
            # "<section> › <label>": the section's list button with that label; a name that
            # is only what it shows ("Select One Required"): the group's one list button
            if control.group == grp and control.role == "button" and e["kind"] == "dropdown":
                lists = [x for x in entries if x["kind"] == "dropdown" and x["name"].startswith(grp + " › ")]
                if control.name.startswith(base) or len(lists) == 1:
                    return True
            continue
        q = lambda s: re.sub(r"[\s*✱]+$", "", s or "")
        if e["kind"] not in GROUPED and control.name != base and q(base) and (
                q(control.above) == q(base)
                or (control.role == "button" and e["kind"] == "dropdown" and q(control.group) == q(base))):
            return True                               # named by its question: the control under that text
        if getattr(control, "nth", 1) != nth:
            continue                                  # "Year #3" stands for the third "Year" only
        # a dropdown the map names by its label ("State") covers the page's "State Delhi Required"
        if control.name == base or (e["kind"] == "dropdown" and control.name.startswith(base + " ")):
            return True
    return False


def apply(frame, cached, facts, log=print):
    """The entries of a saved map that still fit the live page (mapper.check);
    the rest are dropped — their controls go to Claude as unmapped."""
    from jobpilot.apply.explore.mapper import check
    good, stale = check(frame, cached or [], facts)
    if stale:
        log(f"    [reuse] {len(stale)} saved entr{'y' if len(stale) == 1 else 'ies'} no longer fit: "
            + "; ".join(f"{e['name'][:30]!r} ({err[:40]})" for e, err in stale[:4]))
    elif cached:
        log(f"    [reuse] saved map: {len(good)} entr{'y' if len(good) == 1 else 'ies'} fit")
    return good
