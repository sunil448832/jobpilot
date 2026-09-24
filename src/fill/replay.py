#!/usr/bin/env python3
"""
replay.py — the per-application record of HOW a form was filled, so the submit
pass can do it again the same way with the approved answers.

Two passes share it:

  explore  (browser.fill_application, pass 1)
      Walks the form to its last page. Every control the filler touches gets a
      RECIPE: how it was identified (stable id, name, label, page), what kind
      of widget it is, what it offered, which strategy made a value stick, and
      the value used. A required control nobody has an answer for takes a
      PLACEHOLDER so the walk is not blocked; the recipe says so, the
      placeholder never reaches the item's `fields`, and the control surfaces
      as a question on the phone. The ROUTE is recorded too: the frame the form
      lives in, the button that revealed it, each page and the button that led
      on, and the submit button (seen, never pressed). Nothing on this pass can
      submit.

  replay   (browser.submit_approved, pass 2)
      Loads the record. Before a browser opens, every control that took a
      placeholder, and every required one, must resolve to a true value — an
      approved answer or a stored one — or the item goes back to the phone
      untouched. Then the walker follows the recorded route page by page with
      the recipes as hints: the recorded strategy is tried first, a placeholder
      box ticked on pass 1 is unticked when it is not the answer, and anything
      the record does not cover is discovered exactly as on pass 1.

Storage: applications/<slug>/replay.json, next to the resume and JD. Forms
differ per application, so the record does too. The queue item only points at
it (`replay`) and carries the placeholders for the review page.

    {
      "url": ..., "platform": "greenhouse", "explored_at": ..., "reached_end": true,
      "frame": "https://job-boards.greenhouse.io/embed/job_app?for=acme",   # or null
      "start": {"text": "Apply now"},                                     # or null
      "pages": [{"n": 1, "heading": "Apply", "url": ..., "controls": 14,
                 "next": {"text": "Next", "id": "..."}, "submit": null}, ...],
      "submit": {"text": "Submit application", "id": "..."},
      "recipes": [
        {"step": "page 2: Questions",
         "label": "Are you willing to relocate?",
         "ask": "<label + option labels>",      # groups only: the second resolve key
         "id": "question_123", "name": "q123", "type": "radio", "tag": "input",
         "combobox": false, "rs": false, "required": true,
         "options": ["Yes", "No"],
         "strategy": "label-click",             # fill|typed|react-select|select|listbox|
                                                # check|label-click|force-click|js-set|
                                                # click|set_input_files
         "chosen": "Yes", "value": "yes",
         "dummy": true}                         # placeholder — must be replaced
      ],
      "placeholders": {"<label>": "<placeholder used>"},   # emptied as he answers
      "answers": {"<question label>": "<his answer>"},      # written when he answers on the phone
      "attempts": [{"at": ..., "status": ..., "replayed": n, "discovered": n, "outcome": ...}],
      "submit_procedure": {                     # how the real submit went, written by pass 2
        "resolved_by": "code" | "claude",       # code: the replay alone filed it
        "pressed": {"text": "Submit application", ...},   # the button that filed it
        "verification_code": true,              # the portal asked for an emailed code
        "steps": ["..."],                       # claude: what was wrong and what it did
        "code_written": ["applications/<slug>/hooks.py"],
        "at": ...}
    }
"""
import datetime as dt
import json
import os
import re

from jobpilot.core.paths import TOOL, APPLICATIONS

DUMMY_TEXT = "To be confirmed"
GROUP_TYPES = ("radio", "checkbox", "buttongroup")


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[*∗]", "", s or "")).strip().lower()


def path_for(company_slug):
    return os.path.join(APPLICATIONS, company_slug, "replay.json")


def _path(item):
    return os.path.join(TOOL, item["replay"]) if item.get("replay") else path_for(item["company_slug"])


def load(item):
    """The application's replay document, or an empty one."""
    try:
        return json.load(open(_path(item), encoding="utf-8"))
    except (OSError, ValueError):
        return {"recipes": [], "placeholders": {}, "attempts": [], "pages": []}


class Replay:
    """State for one fill pass. Exploring records; replaying hints."""

    def __init__(self, explore=False, recipes=None):
        self.explore = bool(explore)
        self.recipes = list(recipes or [])
        self.record = []            # recipes written by this pass
        self.placeholders = {}      # label -> placeholder value (explore only)
        self.step = None            # page / wizard step, set by the walker or driver
        self.replayed = 0           # controls a recipe covered
        self.discovered = 0         # controls no recipe covered
        # the route
        self.platform = None
        self.frame = None           # URL of the iframe holding the form, or None
        self.start = None           # button that revealed the form, or None
        self.pages = []             # [{n, heading, step, url, controls, next, submit, stuck?}]
        self.submit = None          # the submit button descriptor (seen on pass 1, pressed on pass 2)
        self.reached_end = False

    @classmethod
    def from_doc(cls, doc):
        r = cls(explore=False, recipes=doc.get("recipes"))
        for k in ("platform", "frame", "start", "pages", "submit", "reached_end"):
            if doc.get(k) is not None:
                setattr(r, k, doc[k])
        return r

    # ---- recording -------------------------------------------------------

    def recipe(self, f, strategy, value, dummy=False, chosen=None, ask=None, options=None):
        """Record how control `f` (an EXTRACT_JS record, or a group dict with
        label/name/type/required/options) was filled."""
        v = os.path.basename(str(value)) if f.get("type") == "file" else str(value)
        r = {"step": self.step, "label": (f.get("label") or f.get("name") or "").strip(),
             "ask": ask, "id": f.get("id") or "", "name": f.get("name") or "",
             "type": f.get("type"), "tag": f.get("tag"),
             "combobox": bool(f.get("combobox")), "rs": bool(f.get("rs")),
             "required": bool(f.get("required")),
             "options": [o for o in (options or f.get("options") or []) if o][:20],
             "strategy": (strategy or "").split(" ")[0], "chosen": chosen,
             "value": v[:4000], "dummy": bool(dummy)}
        # One recipe per control: a page filled again (a session retry) replaces
        # the control's earlier recipe instead of stacking a second one.
        key = (r["type"], norm(r["label"]), r["id"], r["name"])
        self.record = [x for x in self.record
                       if (x.get("type"), norm(x.get("label")), x.get("id", ""), x.get("name", "")) != key]
        self.record.append({k: x for k, x in r.items() if x not in (None, "", [], False)})

    def settle(self, label_rx, value, by="platform driver"):
        """A placeholder the generic filler put in, later filled for real by a
        platform driver (Workday's own phone step picks "Mobile" after the
        generic pass missed the menu). The field is right; the record says so."""
        rx = re.compile(label_rx, re.I)
        n = 0
        for r in self.record:
            if r.get("dummy") and rx.search(r.get("label") or ""):
                r["value"] = r["chosen"] = str(value)[:4000]
                r.pop("dummy", None); r.pop("mismatch", None)
                r["answered_by"] = by
                n += 1
        for k in [k for k in self.placeholders if rx.search(k)]:
            self.placeholders.pop(k)
        return n

    def placeholder(self, f, strategy, value, chosen=None, ask=None, options=None):
        label = (f.get("label") or f.get("name") or "").strip()
        self.placeholders[label] = value
        self.recipe(f, strategy, value, dummy=True, chosen=chosen, ask=ask, options=options)

    # ---- hints -----------------------------------------------------------

    def hint(self, f):
        """The pass-1 recipe for one live single control: id, then name, then label."""
        if not self.recipes:
            return {}
        t = f.get("type")
        singles = [r for r in self.recipes if r.get("type") == t and t not in GROUP_TYPES]
        found = None
        if f.get("id"):
            found = next((r for r in singles if r.get("id") == f["id"]), None)
        if not found and f.get("name"):
            found = next((r for r in singles if r.get("name") == f["name"]), None)
        if not found:
            L = norm(f.get("label") or f.get("name") or "")
            found = next((r for r in singles if L and norm(r.get("label")) == L), None)
        return self._count(found)

    def hint_group(self, g):
        """The pass-1 recipe for a radio/checkbox/button group (one per question)."""
        if not self.recipes:
            return {}
        found = next((r for r in self.recipes
                      if r.get("type") == g["type"] and r.get("type") in GROUP_TYPES
                      and ((g.get("name") and r.get("name") == g["name"])
                           or norm(r.get("label")) == norm(g["label"]))), None)
        return self._count(found)

    def _count(self, found):
        if found:
            self.replayed += 1
        else:
            self.discovered += 1
        return found or {}

    # ---- the gate --------------------------------------------------------

    def gaps(self, resolve):
        """Explored controls with no true value now. In the `missing` shape so
        questions_from_missing can put them on the phone. A file control is
        excluded: the resume path is deterministic."""
        out, seen = [], set()
        for r in self.recipes:
            if r.get("type") == "file" or not (r.get("dummy") or r.get("required")):
                continue
            if r.get("answered_by") and not r.get("dummy"):
                continue                       # he (or a platform driver) settled it
            # A mismatch placeholder stands in for a stored answer the menu did not
            # offer: only his own pick (backfilled, which clears `dummy`) settles it.
            if not r.get("mismatch") and (resolve(r["label"]) is not None or
                                          (r.get("ask") and resolve(r["ask"]) is not None)):
                continue
            key = norm(r["label"])[:90]
            if key in seen:
                continue
            seen.add(key)
            opts = list(r.get("options") or [])
            kind = "choice" if r["type"] in GROUP_TYPES else "dropdown" if opts else "text"
            out.append({"label": r["label"], "required": True, "kind": kind, "options": opts,
                        "reason": (f"explored with placeholder {str(r.get('value'))[:30]!r} — needs your real answer"
                                   if r.get("dummy") else "no stored answer")})
        return out

    # ---- storage ---------------------------------------------------------

    def document(self, ctx):
        return {"url": ctx.get("url"), "platform": self.platform or ctx.get("portal"),
                "explored_at": dt.datetime.now().isoformat(timespec="seconds"),
                "reached_end": self.reached_end, "frame": self.frame, "start": self.start,
                "pages": self.pages, "submit": self.submit,
                "recipes": self.record, "placeholders": self.placeholders, "attempts": []}

    def save(self, ctx):
        """Write applications/<slug>/replay.json; returns the tool-relative path."""
        p = path_for(ctx["company_slug"])
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            json.dump(self.document(ctx), open(p, "w", encoding="utf-8"), indent=2)
        except OSError:
            return None
        return os.path.relpath(p, TOOL)

    def note_attempt(self, item, outcome):
        """Append what a submit pass did to the application's replay.json."""
        doc = load(item)
        doc.setdefault("attempts", []).append({
            "at": dt.datetime.now().isoformat(timespec="seconds"), "status": item.get("status"),
            "replayed": self.replayed, "discovered": self.discovered, "outcome": str(outcome)[:200]})
        try:
            json.dump(doc, open(_path(item), "w", encoding="utf-8"), indent=2)
        except OSError:
            pass


def ensure(company_slug, url=None, platform=None):
    """Every application has a replay.json from the day its folder exists: an
    empty skeleton (URL, platform, no recipes yet) until an exploration fills it.
    Returns its path; an existing file is never touched."""
    p = path_for(company_slug)
    if os.path.isfile(p):
        return p
    if url is None or platform is None:
        try:
            jd = open(os.path.join(APPLICATIONS, company_slug, "JD.md"), encoding="utf-8").read(3000)
            m = re.search(r"\*\*Apply URL:\*\*\s*(\S+)", jd) or re.search(r"\*\*Link:\*\*\s*(\S+)", jd)
            url = url or (m.group(1) if m else None)
            m = re.search(r"\*\*Platform / How applying:\*\*\s*([\w.-]+)", jd)
            platform = platform or (m.group(1) if m else None)
        except OSError:
            pass
    doc = {"url": url, "platform": platform, "explored_at": None, "reached_end": False,
           "frame": None, "start": None, "pages": [], "submit": None,
           "recipes": [], "placeholders": {}, "attempts": []}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(doc, open(p, "w", encoding="utf-8"), indent=2)
    return p


def apply_answers(item, answered):
    """His answers from the review page, written into replay.json the moment he
    gives them: each recipe for that control gets the real value (placeholder
    gone, marked as answered by him), and every answer is kept under `answers`
    — including questions no recipe covers (a platform driver's own steps).
    Returns how many recipes were updated."""
    if not answered:
        return 0
    doc = load(item)
    want = {norm(k)[:90]: v for k, v in answered.items() if (v or "").strip()}
    now = dt.datetime.now().isoformat(timespec="seconds")
    hit = 0
    for r in doc.get("recipes") or []:
        v = want.get(norm(r.get("label"))[:90])
        if v is None and r.get("ask"):
            v = want.get(norm(r["ask"])[:90])
        if v is None:
            continue
        if r.get("dummy"):
            # What exploration put there stays known: a saved draft (Workday)
            # still holds it, and the replay must clear it before the answer.
            r["placeholder"] = r.get("chosen") or r.get("value")
        r["value"] = v[:4000]
        if r.get("type") in GROUP_TYPES or r.get("options"):
            r["chosen"] = v
        r.pop("dummy", None)
        r.pop("mismatch", None)
        r["answered_by"] = "applicant"
        r["answered_at"] = now
        hit += 1
    ph = doc.get("placeholders") or {}
    for k in [k for k in ph if norm(k)[:90] in want]:
        ph.pop(k)
    doc["placeholders"] = ph
    doc.setdefault("answers", {}).update({k: v for k, v in answered.items() if (v or "").strip()})
    try:
        json.dump(doc, open(_path(item), "w", encoding="utf-8"), indent=2)
    except OSError:
        return 0
    return hit


def values(item):
    """label -> value from replay.json, for the submit resolver: his answers
    first, then every recipe that is not a placeholder (files excluded)."""
    doc = load(item)
    out = {}
    for k, v in (doc.get("answers") or {}).items():
        out.setdefault(norm(k), v)
    for r in doc.get("recipes") or []:
        if r.get("dummy") or r.get("type") == "file" or not r.get("value"):
            continue
        v = r.get("chosen") if (r.get("type") in GROUP_TYPES and r.get("chosen")) else r["value"]
        out.setdefault(norm(r.get("label")), v)
    return out


def record_submit_procedure(item, procedure):
    """Write how the real submit went into the application's replay.json, and
    make the button that actually filed it the recorded submit button."""
    doc = load(item)
    proc = dict(procedure)
    proc["at"] = dt.datetime.now().isoformat(timespec="seconds")
    doc["submit_procedure"] = proc
    if proc.get("pressed"):
        doc["submit"] = proc["pressed"]
    try:
        json.dump(doc, open(_path(item), "w", encoding="utf-8"), indent=2)
    except OSError:
        pass
    return doc


def annotate_questions(questions, placeholders):
    """Tell the phone which questions stand in for a placeholder."""
    by_key = {norm(k)[:90]: v for k, v in (placeholders or {}).items()}
    for q in questions:
        v = by_key.get(norm(q.get("label", ""))[:90])
        if v is not None and not q.get("note"):
            q["note"] = f"Explored with placeholder {str(v)[:30]!r} — your answer replaces it."
    return questions


def dummy_value(f, answers):
    """A placeholder a validator accepts, so the walk continues. Never submitted."""
    t = f.get("type")
    if t == "email":
        return answers["personal"].get("email") or "tbc@example.com"
    if t == "tel":
        return answers["personal"].get("phone") or "0000000000"
    if t == "url":
        return (answers.get("links") or {}).get("linkedin") or "https://example.com"
    if t == "number":
        return "1"
    if t == "date" or "date" in (f.get("name") or "").lower():
        return (answers.get("availability") or {}).get("earliest_start_iso") or dt.date.today().isoformat()
    return DUMMY_TEXT
