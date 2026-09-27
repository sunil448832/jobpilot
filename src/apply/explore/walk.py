#!/usr/bin/env python3
"""
explore.py — PASS 1: walk an application form to its last page by see / map /
act, record every action, never press Submit. docs/exploration-plan.md.

Per page:
    see     the form's frame, its accessibility snapshot            (see.py)
    map     cached page map -> the rest by Claude, checked, corrected (mapper.py, reuse.py)
    act     each entry with its kind's routine, checked; a menu is mapped first and
            the answer picked by its path; a failed action goes back to the map
            (act.py)
    record  the action and where its value comes from                (record.py)
    ask     a required control no fact answers: a placeholder, and a question
            for the applicant with the real choices
    next    the platform's (Workday: Save and Continue) or the page's Next; the
            portal's refusal goes back to the map too

do_page() is shared with submit/replay.py, which re-maps a page whose recorded actions
no longer fit.
"""
import datetime as dt
import json
import os

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL
from jobpilot.apply import platforms
from jobpilot.apply.explore import browser as B, facts as F, record as R
from jobpilot.apply.explore import act as A, see as S
from jobpilot.apply.explore import mapper as M, reuse

PLACEHOLDER_TEXT = "To be confirmed"
# what the map is told when a control offers choices and the stored answer is not one of
# them (or nothing is stored): pick one of the choices, never type a value of its own
CHOOSE = ("answer option:<the choice> when one means the same; otherwise guess:<the nearest choice> | "
          "<the question> — it is filled in as a placeholder and asked on the phone; for a long list, "
          "search:<term>; <term> to see what it holds. Never type your own value.")


class Walk:
    """Everything one form walk carries: the job, its facts, the record being written."""

    def __init__(self, ctx, answers, learned, log=print, stage="explore"):
        self.ctx, self.answers, self.learned, self.log, self.stage = ctx, answers, learned, log, stage
        self.slug = ctx["company_slug"]
        self.pid = ctx.get("portal") or "unknown"
        self.mod = platforms.get(self.pid)
        self.company = reuse.company_key(self.pid, ctx.get("url"), ctx.get("company"))
        self.resume = B.resume_path(answers, self.slug)
        self.facts = F.job_facts(answers, ctx, learned, self.resume)
        old = R.load(self.slug)
        self.answered = {q: p["answer"] for q, p in (old.get("placeholders") or {}).items() if p.get("answer")}
        self.record = {"url": ctx.get("url"), "platform": self.pid, "company": self.company,
                       "explored_at": dt.datetime.now().isoformat(timespec="seconds"), "reached_end": False,
                       "pages": [], "placeholders": {}, "submit": None,
                       "attempts": old.get("attempts") or []}
        self.warnings, self.filled, self.unfilled = [], {}, {}      # unfilled: {page: [question]}, its last try
        self.budget = {}                                  # step -> Claude calls left on that page
        self.maps = {}                                    # step -> the page's entries (dicts), as last mapped

    # ------------------------------------------------------------ one control
    def required(self, frame, e):
        """A '*' on the name or its group, or the control says so."""
        if "*" in e["name"]:
            return True
        try:
            if e["kind"] in M.GROUPED:
                g = S.group_scope(frame, e["name"])
                if g is None:
                    return False
                return bool(g.evaluate("el => !!el.querySelector('[required], [aria-required=\"true\"]') || "
                                       "el.getAttribute('aria-required') === 'true'"))
            loc = A.locate(frame, M.role_of(frame, e["kind"], e["name"]), e["name"])
            return bool(loc.evaluate("el => el.required || el.getAttribute('aria-required') === 'true'"))
        except Exception:
            return False

    def options_of(self, frame, e):
        """A group's choices as a person reads them (the radios' / buttons' labels)."""
        return S.group_options(frame, e["name"])

    def guess_note(self, e):
        """What the phone says about a choice the map guessed."""
        stored = self.facts.get(e["fact"]) if e.get("fact") else None
        return (f"picked the closest choice to your stored answer {stored!r} — confirm or change" if stored
                else "nothing stored: picked the likeliest choice — confirm or change")

    def search(self, frame, e, page_rec):
        """A long list searched with the map's terms: the top hits of each go back to the
        map, which picks one (the next see). A searchable box is typed into; a list read
        whole is matched against each term (a pattern, case-insensitive)."""
        import re
        kind, name, hits = e["kind"], e["name"], {}
        tree = None
        for term in e["search"]:
            try:
                if kind == "search-and-pick":
                    found = A.search_options(frame, name, term, limit=5)
                else:
                    tree = tree if tree is not None else A.map_menu(frame, kind, name, max_options=5000)
                    try:
                        rx = re.compile(term, re.I)
                    except re.error:
                        rx = re.compile(re.escape(term), re.I)
                    found = [x for x in A.leaves(tree) if rx.search(x)][:5]
                    if not found and kind == "dropdown":   # drawn only on screen: typed to
                        found = A.search_options(frame, name, term, limit=5, kind="dropdown")
            except Exception as ex:
                found = [f"(could not search: {type(ex).__name__})"]
            hits[term] = found
        self.log(f"      search          {M.question(e)[:50]!r:52} {hits}")
        # a hit that is word for word one of the terms is the entry meant: picked, no round
        exact = next((h for t in e["search"] for h in hits.get(t, []) if h.strip().lower() == t.strip().lower()), None)
        if exact:
            e.pop("search", None)
            e["option"] = exact
            return self.act_entry(frame, e, page_rec)
        if not any(hits.values()):
            return (f"searching the list for {list(hits)} found nothing — try other terms "
                    "(search:<term>; <term>), or guess:<the likeliest entry> | <the question>")
        return ("searching the list found: " + "; ".join(f"{t!r} -> {h}" for t, h in hits.items())
                + " — answer option:<one of these, copied exactly> for the entry that is the applicant's "
                "answer in the list's own words ('India +91' for +91, 'Artificial Intelligence and Robotics' "
                "is not AI alone: guess); guess:<the nearest of these> | <the question> | <other near ones>; ... "
                "only when none of them is his answer.")

    def nearest(self, frame, e, kind, choices, picked):
        """The choices the phone offers for a guessed pick. A short list: all of it. A long
        one (the phone cannot show it, and only its start was read): the pick and the
        entries the map named as nearest, each looked up in the list — only what the
        list really holds. The pick is set again after the lookups."""
        if len(choices) <= 15 and not e.get("near"):
            return choices
        out = [picked]
        for term in e.get("near") or []:
            found = [c for c in choices if term.lower() in c.lower()]
            if not found and kind == "search-and-pick":
                try:
                    found = A.search_options(frame, e["name"], term)
                except Exception:
                    found = []
            out += [f for f in found if f not in out]
        if kind == "search-and-pick" and len(out) > 1:
            A.act(frame, kind, e["name"], picked)             # the searches emptied the box
        return out[:12] if len(out) > 1 else choices

    def placeholder(self, q, used, options, kind):
        field, page = getattr(self, "_at", ("", ""))
        self.record["placeholders"][q] = {"value": used, "options": options, "kind": kind,
                                          "answer": self.answered.get(q),
                                          "field": field, "page": page}      # where the card says it is

    def act_entry(self, frame, e, page_rec):
        """Fill one mapped control. Returns None, or the error for the map."""
        kind, name, q = e["kind"], e["name"], M.question(e)
        other = self.record["placeholders"].get(q)
        if other and other.get("field") not in (None, "", name):
            q = f"{q} ({name})"                                   # the same question in another section
        self._at = (name, page_rec["step"])
        if kind == "skip" or kind in M.BUTTONS:                   # buttons are pressed by the walk, not filled
            return None
        A.close_menus(frame)
        if e.get("search") and kind in ("search-and-pick", "dropdown", "native-select"):
            return self.search(frame, e, page_rec)
        fact = e.get("fact")
        value = self.facts.get(fact) if fact else None
        source = f"fact:{fact}" if fact else None
        if e.get("option"):                                       # the map chose a choice the page offers
            value, source = e["option"], "choice"
            if e.get("guess"):                                     # ...its nearest guess: a placeholder, asked
                source = f"placeholder:{q}"
            if " › " in value:                                     # inside a category: "Social Media › LinkedIn"
                *cats, value = [p.strip() for p in value.split(" › ")]
                e["_path"] = tuple(cats)
        if value is None and q in self.answered:                  # his answer from an earlier round
            value, source = self.answered[q], f"placeholder:{q}"
        path, used, searched = (), value, {}
        try:
            if kind == "file":
                used, source = self.resume, "fact:file:resume"
                if not used:
                    return "no resume file built for this application"

            elif kind in ("search-and-pick", "dropdown", "native-select"):
                if value is None and not self.required(frame, e):
                    return None                                    # optional, nothing stored: not even opened
                tree = A.map_menu(frame, kind, name)
                page_rec["menus"][q] = tree
                choices = A.leaves(tree)
                hit = A.find_path(tree, value) if value is not None else None
                if not hit and e.get("_path") and value is not None:
                    hit = [*e["_path"], value]                     # the category the map named
                if hit:
                    used, path = hit[-1], tuple(hit[:-1])
                    if e.get("guess"):
                        self.placeholder(q, " › ".join(hit), self.nearest(frame, e, kind, choices, hit[-1]), "select")
                        self.record["placeholders"][q]["note"] = self.guess_note(e)
                elif value is not None and tree and kind == "search-and-pick" and (searched := A.act(frame, kind, name, value)).get("ok"):
                    # not a top-level choice, but the box's own search found it (inside a
                    # category the page does not mark: "Social Media" › "LinkedIn")
                    page_rec["actions"].append({"kind": kind, "name": name, "fact": e.get("fact"), "source": source or "fact",
                                                "value": str(value), "path": []})
                    if e.get("guess"):
                        self.placeholder(q, str(value), self.nearest(frame, e, kind, choices, str(value)), "select")
                        self.record["placeholders"][q]["note"] = self.guess_note(e)
                    else:
                        self.filled[q] = str(value)
                    self.log(f"      {kind:15} {q[:50]!r:52} = {str(value)[:40]!r}   [{source}, found by searching]")
                    return None
                elif value is not None and tree:
                    # not one of the choices: back to the map, which picks one of them
                    return (f"the answer {value!r} is not one of the choices; the menu offers {choices[:40]}"
                            + (f" — only its first entries: the list is longer and searchable; searching it for "
                               f"{value!r} found {searched.get('offered', [])[:25]}"
                               if any(o not in choices for o in searched.get("offered", [])) else "")
                            + " — " + CHOOSE)
                elif value is None:
                    if not self.required(frame, e):
                        return None                                # optional, nothing stored: leave it
                    if not choices:
                        return "the menu offered nothing to pick"
                    # no stored answer: the map picks the likeliest choice as the placeholder
                    return (f"no stored answer; the menu offers {choices[:40]} — "
                            + CHOOSE)
                # else: a search-only menu — search for the stored value itself (a miss comes back with what it offered)

            elif kind in M.GROUPED:
                options = self.options_of(frame, e)
                if value is not None:
                    used = next((o for o in options if o.strip().lower() == str(value).strip().lower()), None)
                    if used is None:
                        return (f"the answer {value!r} is not one of the choices {options} — "
                                + CHOOSE)
                    if e.get("guess"):
                        self.placeholder(q, used, options, "choice")
                        self.record["placeholders"][q]["note"] = self.guess_note(e)
                else:
                    if not self.required(frame, e):
                        return None
                    if not options:
                        return "the group has no options"
                    return (f"no stored answer; the choices are {options} — " + CHOOSE)

            elif kind == "checkbox":
                if value is None:
                    if not self.required(frame, e):
                        return None
                    used, source = "Yes", f"placeholder:{q}"            # a required acknowledgement: asked
                    self.placeholder(q, "Yes", ["Yes"], "choice")
                else:                                                    # stored "No": untick it (a resume parse may have ticked it)
                    used = "Yes" if str(value).strip().lower() in ("yes", "true", "1") else "No"

            else:                                                        # text / number
                if value is None:
                    loc = A.locate(frame, M.ROLE[kind], name)
                    shown = (loc.input_value() or "").strip()
                    if shown:                                            # the page's own value (a resume parse)
                        used, source = shown, "shown"
                    elif not self.required(frame, e):
                        return None
                    else:
                        used, source = ("1" if kind == "number" else PLACEHOLDER_TEXT), f"placeholder:{q}"
                        self.placeholder(q, used, [], "text")

            r = A.act(frame, kind, name, used, path)
        except Exception as ex:
            # everything the browser said (an element covering it, a timeout) and the
            # control's own HTML: the map decides what it really is
            lines = [x.strip() for x in str(ex).splitlines() if x.strip()]
            why = [x for x in lines if any(w in x for w in ("intercepts", "not visible", "disabled", "detached", "outside"))]
            said = " | ".join(lines[:1] + why[-2:] + [x for x in lines[1:] if x not in why][:3])[:500]
            html = A.field_html(frame, M.role_of(frame, kind, name), name)
            return f"acting on it failed — {type(ex).__name__}: {said}" + (f"\n      its HTML: {html}" if html else "")
        if not r.get("ok"):
            return (f"the value did not take (shows {r.get('shown')!r})"
                    + (f"; the menu offers {r['offered'][:40]} — " + CHOOSE if r.get("offered") else ""))
        page_rec["actions"].append({"kind": kind, "name": name, "fact": e.get("fact"), "source": source or "fact",
                                    "value": str(used), "path": list(path)})
        if not (source or "").startswith("placeholder"):
            self.filled[q] = str(used)
        self.log(f"      {kind:15} {q[:50]!r:52} = {str(used)[:40]!r}"
                 + (f" via {' › '.join(path)}" if path else "") + f"   [{source}]")
        return None

    # ------------------------------------------------------------ one page
    def do_page(self, frame, step, failures=None, cached=None, chrome=False):
        """Map the page and act on it; failed actions go back to the map. Returns the page record."""
        page_rec = next((p for p in self.record["pages"] if p["step"] == step), None)
        if page_rec is None:
            page_rec = {"step": step, "entries": [], "menus": {}, "actions": [], "rounds": []}
            self.record["pages"].append(page_rec)
        if cached is None:
            cached = reuse.load(self.pid, self.company, step)
        self.capture(frame, step)
        B.wait_quiet(frame, max_s=10)
        # an action counts as done for this exact entry: a corrected entry (same name,
        # a new kind or fact) is acted on again
        done = {(a["name"], a["kind"], a.get("fact")) for a in page_rec["actions"]}
        entries = cached
        for attempt in range(int(cfg("fill.map_rounds", 3))):
            entries, bad = M.map_page(frame, step, entries, self.facts, self.learned, log=self.log,
                                      failures=failures, rounds=page_rec["rounds"],
                                      budget=self.budget.setdefault(step, {"calls": int(cfg("fill.map_calls_per_page", 5))}),
                                      chrome=chrome)
            # what the map could not get right in its rounds: never acted on, still unfilled
            failures = [(e, err) for e, err in bad if e and e.get("kind") not in M.BUTTONS]
            # choices first: a tick or a pick may add or hide fields ("I currently work here"
            # hides an end date) — then the page is seen again before anything is typed
            shape, moved = self.shape(frame), None
            for e in sorted(entries, key=lambda x: x["kind"] in ("text", "number")):
                key = (e["name"], e["kind"], e.get("fact"))
                if key in done or e["kind"] == "skip":
                    continue
                if e["kind"] in ("text", "number") and moved is None:
                    now = self.shape(frame)
                    moved = {n for n in set(now) | set(shape) if now.get(n) != shape.get(n)}
                if moved and A._split(e["name"])[1] in moved:
                    continue                                   # its numbering moved: mapped again below
                err = self.act_entry(frame, e, page_rec)
                if err:
                    self.log(f"      ✗ {e['name'][:50]!r}: {err}")
                    failures.append((e, err))
                else:
                    done.add(key)
            if moved:
                self.log(f"    the page changed as it was filled ({sorted(moved)[:6]}) -> seen again")
                entries = [e for e in entries if A._split(e["name"])[1] not in moved]
                failures.append((None, "the page changed as it was filled — fields appeared or went away "
                                       f"({sorted(moved)[:6]}): map the controls as they are now"))
            if not failures:
                break
            self.log(f"    -> {len(failures)} failed action(s) back to the map")
        self.recheck(frame, page_rec)
        for e, err in failures or []:
            self.warnings.append(f"'{M.question(e)}' could not be filled: {err}")
        self.unfilled[step] = [M.question(e) for e, _ in failures or [] if e]
        page_rec["entries"] = [[e["name"], e["kind"], e.get("fact"), e.get("question")] for e in entries]
        self.maps[step] = entries
        reuse.save(self.pid, self.company, step, entries, source=self.slug)          # a draft until the page is taken
        return page_rec

    def shape(self, frame):
        """{name: how many} of the page's typing fields — numbered names ("Year #4") hold
        only while this stays the same."""
        out = {}
        for c in S.form_controls(S.snapshot(frame)):
            if c.role in ("textbox", "spinbutton", "combobox"):
                out[c.name] = out.get(c.name, 0) + 1
        return out

    def recheck(self, frame, page_rec):
        """The page's typed values read back once it is filled (act.readback)."""
        A.readback(frame, [(a["kind"], a["name"], a["value"]) for a in page_rec["actions"]], self.log)

    # ------------------------------------------------------------ the walk
    def frame(self, page):
        return S.form_frame(page, getattr(self.mod, "FRAME_PATTERNS", ()))

    def step_name(self, page, frame, n):
        if self.mod and hasattr(self.mod, "step"):
            return self.mod.step(page) or f"page {n}"
        if n == 1:
            return "form"                  # a one-page form's key: the same for every company on the platform
        try:
            h = frame.get_by_role("heading").first.inner_text(timeout=1500).strip()
        except Exception:
            h = ""
        return h[:60] or f"page {n}"

    def accept(self, step):
        """The portal took this page (it moved on): its map is the accepted one."""
        if self.maps.get(step):
            reuse.save(self.pid, self.company, step, self.maps[step], source=self.slug, accepted=True)

    def capture(self, frame, step):
        """The page as it was when mapped — HTML and accessibility snapshot, under
        applications/<slug>/pages/ — so its mapping can be replayed offline
        (tests/replay_page.py) without a live run."""
        d = os.path.join(os.path.dirname(R.path_for(self.slug)), "pages")
        try:
            os.makedirs(d, exist_ok=True)
            base = os.path.join(d, reuse.slug(step))
            with open(base + ".html", "w", encoding="utf-8") as f:
                f.write(frame.content())
            with open(base + ".aria.txt", "w", encoding="utf-8") as f:
                f.write(S.snapshot(frame))
        except Exception as e:
            self.log(f"    [capture] {step}: {type(e).__name__}")

    def button(self, step, kind):
        """The name of this page's `kind` button (next / submit / start) from its map."""
        page_rec = next((p for p in self.record.get("pages", []) if p["step"] == step), None)
        for x in (page_rec or {}).get("entries", []):
            if x[1] == kind:
                return x[0]
        return None

    def press_start(self, page):
        """On the job posting (or an 'apply how?' dialog): map it and press the
        button the map calls `start`. True when one was pressed."""
        frame = self.frame(page)
        step = "posting" if not frame.get_by_role("dialog").count() else "apply dialog"
        self.do_page(frame, step, chrome=True)             # a posting's Apply may sit in a sticky header
        name = self.button(step, "start")
        if not name:
            # a map that marks no start button failed its one job: never keep it — map the
            # page again from nothing, telling the map what was missing
            self.log(f"    [map] no start button marked on '{step}' — mapping it again")
            self.do_page(frame, step, cached=[], chrome=True,
                         failures=[(None, "no button was marked start: which button opens the application form?")])
            name = self.button(step, "start")
        if not name and step == "apply dialog":
            # a dialog that opens nothing (a cookie banner): the start is on the posting behind it
            step = "posting"
            self.do_page(frame, step, chrome=True)
            name = self.button(step, "start")
        if not name:
            return False
        A.act(frame, "button", name)
        B.wait_quiet(page.main_frame, max_s=10)
        self.accept(step)                                 # the page did what its start button promised
        return True

    def start(self, page):
        """The posting -> the form: the platform's start (Workday's account gate), else
        the map's `start` button while the page shows no form yet."""
        if self.mod and hasattr(self.mod, "start"):
            return self.mod.start(page, self.ctx, self.log, self.press_start)
        for _ in range(3):
            frame = self.frame(page)
            if len([c for c in S.form_controls(S.snapshot(frame)) if c.role not in ("button", "link")]) >= 2:
                return True
            if not self.press_start(page):
                return True                          # no start button: the form is the page itself
        return True

    def next(self, page, frame, step):
        """(moved on, errors, is_last) — by the button the page's map calls `next`;
        a page whose map has no `next` but a `submit` is the last page."""
        nxt, sub = self.button(step, "next"), self.button(step, "submit")
        if sub:
            self.record["submit"] = sub
        if not nxt:
            return False, [] if sub else ["the map found no next or submit button on this page"], bool(sub)
        if self.mod and hasattr(self.mod, "next_page"):
            ok, errs = self.mod.next_page(page, step, nxt)
            return ok, errs, False
        before = S.snapshot(frame)
        A.act(frame, "button", nxt)
        B.wait_quiet(frame, max_s=10)
        if S.snapshot(self.frame(page)) == before:
            return False, S.errors(frame) or [f"the page did not change after {nxt!r}"], False
        return True, [], False

    def run(self, page):
        """Walk from the posting to the last page. Returns reached_end."""
        if not self.start(page):
            self.warnings.append("could not get from the posting to the form" +
                                 (f": {self.ctx['warnings'][-1]}" if self.ctx.get("warnings") else ""))
            return False
        last_step = None
        for n in range(1, int(cfg("browser.max_pages", 12)) + 1):
            frame = self.frame(page)
            step = self.step_name(page, frame, n)
            self.log(f"\n  page {n}: {step}")
            if self.mod and hasattr(self.mod, "is_last") and self.mod.is_last(step):
                self.do_page(frame, step)                   # a review page: its map names the submit button
                self.record["submit"] = self.button(step, "submit")
                return True
            if step == last_step:
                self.warnings.append(f"'{step}' came back after Next — stopped")
                return False
            self.do_page(frame, step)
            ok, errs, last = self.next(page, frame, step)
            for attempt in range(2):
                if ok or last or not errs:
                    break
                self.log(f"    the page refused: {errs[:3]} -> back to the map")
                # the page's own complaint always gets a Claude call, whatever corrections used
                self.budget.setdefault(step, {"calls": 0})["calls"] += 1
                self.do_page(frame, step, failures=[(None, f"Save / Next refused the page: {e}") for e in errs[:6]])
                ok, errs, last = self.next(page, frame, step)
            if last:
                self.accept(step)
                return True
            if not ok:
                self.warnings.append(f"'{step}' would not save: {errs[:4]}")
                return False
            self.accept(step)
            last_step = step
        self.warnings.append("too many pages — stopped")
        return False


# ---------------------------------------------------------------- the queue item

def questions_from(record):
    """The placeholders as review-page questions: the real choices, a note on what stood in."""
    out = []
    for i, (q, p) in enumerate((record.get("placeholders") or {}).items()):
        if p.get("answer"):
            continue
        opts = [o.split(" › ")[-1] for o in (p.get("options") or [])]
        out.append({"qid": i, "label": q, "options": opts if p.get("kind") != "text" else [],
                    "kind": "select" if p.get("kind") in ("select", "choice") else "text",
                    "required": True, "status": "open",
                    "page": p.get("page") or "", "field": p.get("field") or "",
                    "value": str(p.get("value") or "").split(" › ")[-1],
                    "note": p.get("note") or f"explored with placeholder {str(p.get('value'))[:40]!r} — needs your answer"})
    return out


def write_item(w, pay, shot_rel, reached):
    """The queue item the review page shows. An exploration that stopped short is `failed`."""
    ctx = w.ctx
    item_id = f"{w.slug}-{dt.datetime.now():%m%d%H%M}"
    questions = questions_from(w.record)
    unfilled = [q for qs in w.unfilled.values() for q in qs]
    # a re-exploration replaces every earlier item of this application not yet sent: the
    # phone shows one card per application, with this run's questions only
    for f in sorted(os.listdir(B.QUEUE_DIR)) if os.path.isdir(B.QUEUE_DIR) else []:
        if f.startswith(w.slug + "-") and f.endswith(".json") and not f.endswith("-submitted.json"):
            prev = os.path.join(B.QUEUE_DIR, f)
            try:
                d = json.load(open(prev))
                if d.get("status") not in ("submitted", "superseded") and not d.get("submitted_at"):
                    d["status"] = "superseded"
                    json.dump(d, open(prev, "w"), indent=2)
            except (OSError, ValueError):
                pass
    item = {
        "id": item_id, "company": ctx["company"], "company_slug": w.slug, "role": ctx["role"],
        "location": ctx["location"], "url": ctx["url"], "portal": w.pid, "market": ctx["market"],
        "salary_quoted": pay.get("expected_text"), "score": ctx.get("score"), "resume": w.resume,
        "screenshot": shot_rel, "fields": w.filled, "warnings": w.warnings, "questions": questions,
        "pages": len(w.record["pages"]), "reached_end": reached, "replay": os.path.relpath(R.path_for(w.slug), TOOL),
        # a question left unfilled is neither ready to approve nor askable on the phone
        "status": ("needs_input" if questions else "pending") if reached and not unfilled else "failed",
        "fail_reason": (None if reached and not unfilled else
                        f"{len(unfilled)} question(s) could not be filled: {unfilled[:6]}" if reached else
                        "exploration did not reach the last page — " + (w.warnings[-1][:200] if w.warnings else "no reason given")),
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(B.QUEUE_DIR, exist_ok=True)
    with open(os.path.join(B.QUEUE_DIR, f"{item_id}.json"), "w") as f:
        json.dump(item, f, indent=2)
    return item


def explore(ctx, answers, learned, pay, log=print):
    """PASS 1 for one application. Returns the queue item written."""
    w = Walk(ctx, answers, learned, log)
    mod = w.mod
    url = mod.form_url(ctx["url"]) if mod and hasattr(mod, "form_url") else ctx["url"]
    shot_rel = os.path.join("data", "queue", f"{w.slug}-{dt.datetime.now():%m%d%H%M}.png")
    reached = False
    with B.session() as br:
        page = br.new_page()
        log(f"  [explore] {ctx['company']} — {ctx['role']} ({w.pid}, maps: {w.company})")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        gone = B.dead_posting(page)
        if gone:
            raise SystemExit(f"EXPIRED: posting is closed ({gone!r})")
        reached = w.run(page)
        try:
            page.screenshot(path=os.path.join(TOOL, shot_rel), full_page=True)
        except Exception:
            shot_rel = None
    w.record["reached_end"] = reached
    R.save(w.slug, w.record)
    item = write_item(w, pay, shot_rel, reached)
    log(f"\n  [explore] {'reached the last page' if reached else 'STOPPED: ' + (w.warnings[-1] if w.warnings else '?')}"
        f" — {len(w.filled)} filled, {len(item['questions'])} question(s) -> {item['status']}")
    return item
