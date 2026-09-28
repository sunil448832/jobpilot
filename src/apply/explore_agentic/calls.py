"""explore_agentic/calls.py — the record of an application's agent sessions, and redoing it.

    applications/<slug>/calls.json   every tool call of the agent, in order: tool, arguments,
                                     page, the lasting identity of each control it names, what
                                     it did. Saved after EVERY call, so a session stopped at any
                                     point is resumed from it: while a session has not finished,
                                     the record keeps the former record's pages it has not
                                     reached yet; a finished one is the record whole.

    Redo      the replay tool (session.py) and replay.py: from the page shown now, each page's
              calls that change it (act, clear, press) in their order with their arguments,
              each control found again by its identity; a check that every field holds what
              it held; the page's recorded Next — until a page differs, a page the record does
              not know, or the record's last page. The calls redone join the new record.
"""
import json
import os

from jobpilot.apply.explore import act as A, record as R

CHANGES = ("act", "clear", "press")
NOT_CHECKED = ("add", "file", "press", "button", "search")


def calls_path(slug):
    return os.path.join(os.path.dirname(R.path_for(slug)), "calls.json")


def load(slug):
    """(agentic.json or {}, calls.json or [])."""
    rec, calls = {}, []
    path = os.path.join(os.path.dirname(R.path_for(slug)), "agentic.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    if os.path.exists(calls_path(slug)):
        with open(calls_path(slug), encoding="utf-8") as f:
            calls = json.load(f)
    return rec, calls


def save(slug, calls, former, finished):
    """The record now: this session's calls; while it has not finished, then the former
    record's calls on pages this session has not reached."""
    reached = {c.get("page") for c in calls if c["tool"] in CHANGES}
    rest = [] if finished else [c for c in former if c.get("page") not in reached]
    with open(calls_path(slug), "w", encoding="utf-8") as f:
        json.dump([{**c, "n": i + 1} for i, c in enumerate(calls + rest)], f, indent=1, ensure_ascii=False)


def by_page(calls):
    """{page: [its calls that change it, in order]}."""
    pages = {}
    for c in calls:
        if c["tool"] in CHANGES:
            pages.setdefault(c["page"], []).append(c)
    return pages


def approved_answers(slug, rec):
    """{normalised question: his answer} — from the phone (explore.json placeholders) and any
    answer written into the agentic record's placeholders."""
    out = {}
    for src in (R.load(slug).get("placeholders") or {}, rec.get("placeholders") or {}):
        for q, p in src.items():
            if (p.get("answer") or "").strip():
                out[R.norm(q)] = p["answer"].strip()
    return out


class Redo:
    """A former record redone on a live Form, on the browser's thread."""

    def __init__(self, form, rec, calls, approved, dry, log):
        self.form, self.rec, self.approved, self.dry, self.log = form, rec, approved, dry, log
        self.pages = by_page(calls)
        self.submit_page = (rec.get("submit_control") or {}).get("page")
        self.unanswered, self.redone = [], set()

    def answer(self, row, o):
        """The answer a row runs with: a placeholder's -> his approved answer."""
        q = (o or {}).get("question")
        if not q:
            return row[2]
        mine = self.approved.get(R.norm(q))
        if mine is None:
            self.unanswered.append(q)
            return row[2] if self.dry else None          # not filing: on with what stood in
        return ("option:" if row[1] == "select" else "text:") + mine

    def last_acts(self, page):
        """For each control acted on in this page: its last row's outcome, with the identity it
        had then."""
        last = {}
        for call in self.pages.get(page, []):
            if call["tool"] != "act":
                continue
            for o in call.get("outcomes") or []:
                if o.get("how") not in NOT_CHECKED and o["id"] in call["controls"]:
                    last[o["id"]] = (call["controls"][o["id"]], o)
        return last

    def nav(self, page):
        return next((c for c in reversed(self.pages.get(page, [])) if c["tool"] == "press" and c.get("moved_to")), None)

    # ------------------------------------------------------------ one page
    def replay(self, page):
        """Redo one page's calls (not its Next). Returns [what differs]."""
        f = self.form
        f.read()
        differs = []
        nav = self.nav(page)
        for call in self.pages.get(page, []):
            if call is nav:
                continue
            found = {old: f.find(ident) for old, ident in call["controls"].items()}
            if call["tool"] == "act":
                outs = {o["id"]: o for o in call.get("outcomes") or []}
                rows, then = [], {}
                for row in call["args"].get("rows") or []:
                    o, c = outs.get(row[0]), found.get(row[0])
                    if o is None or o.get("how") == "search":
                        continue                          # not on the page then, or only searched
                    if c is None:
                        differs.append(f"{row[0]} ({call['controls'].get(row[0], {}).get('name')!r}: {row[2]}) "
                                       "is not on the page")
                        continue
                    if o.get("how") == "add" and o.get("section_count") is not None and \
                            (f.section_count(o.get("section")) or 0) >= o["section_count"]:
                        continue                          # the section has its blocks already
                    ans = self.answer(row, o)
                    if ans is not None:
                        rows.append([c.id, row[1], ans] + list(row[3:]))
                        then[c.id] = o
                if rows:
                    f.act(rows)
                    for o in f.outcomes or []:
                        was = then.get(o["id"])
                        if was and was["ok"] and not o["ok"] and o.get("how") not in NOT_CHECKED:
                            differs.append(f"{o['id']} ({o['answer']}) took then, not now: shows {o['shown'][:80]!r}")
            else:
                c = found.get(call["args"].get("id"))
                if c is None:
                    continue                              # gone already (a draft kept it so)
                f.clear(c.id) if call["tool"] == "clear" else f.press(c.id)
                f.read()
            f.calls.append({k: v for k, v in call.items() if k != "n"})   # redone: part of the new record
        return differs + self.check(page)

    def check(self, page):
        """Every control acted on shows what its last act showed."""
        f = self.form
        frame, out = f.read(), []
        for ident, o in self.last_acts(page).values():
            c = f.find(ident)
            if c is None or o.get("reads") is None:
                continue
            q = o.get("question")
            want = self.approved.get(R.norm(q), o["reads"]) if q else o["reads"]
            now = A.current(frame, c)
            if now is None:
                continue
            a, b = A.plain(want), A.plain(now)
            if a != b and not (a and b and (a in b or b in a)):
                out.append(f"{c.id} {c.role} {c.name!r}: should hold {want[:80]!r}, holds {now[:80]!r}")
        return out

    def next(self, page):
        """The page's recorded Next: (moved on, what the page said)."""
        nav = self.nav(page)
        if nav is None:
            return False, "the record has no Next for this page"
        self.form.read()
        ident = nav["controls"].get(nav["args"].get("id"))
        c = self.form.find(ident) if ident else None
        if c is None:
            return False, f"its Next {(ident or {}).get('name')!r} is not on the page"
        said = self.form.press(c.id)
        self.form.calls.append({k: v for k, v in nav.items() if k != "n"})
        return said.startswith("moved on"), said

    # ------------------------------------------------------------ page after page
    def forward(self):
        """Redo the record from the page shown now, page after page, until a page differs, a
        page the record does not know, a Next refused, or the record's last page. A page
        redone before (then fixed by the agent) is not redone: only its Next is pressed.
        {"redone": [pages], "at": page, "end": the record's last page reached, "why", "differs"}."""
        f, redone = self.form, []

        def stop(why, differs=(), end=False):
            return {"redone": redone, "at": f.step, "end": end, "why": why, "differs": list(differs)}

        for _ in range(40):
            f.read()
            page = f.step
            if page not in self.pages:
                return stop("the record does not know this page")
            if page not in self.redone:
                self.redone.add(page)
                self.log(f"  redo '{page}'")
                differs = self.replay(page)
                if differs:
                    return stop("it differs from the record", differs)
                redone.append(page)
            if page == self.submit_page or f.is_last(page) or \
                    (self.nav(page) is None and all(p in self.redone for p in self.pages)):
                return stop("the record's last page", end=True)
            if self.nav(page) is None:                    # stopped before its Next: later pages are recorded
                return stop("the record has no Next for this page (its session stopped here): press it "
                            "yourself, then call replay again")
            moved, said = self.next(page)
            if not moved:
                return stop(f"its Next did not move on: {said[:300]}")
        return stop("too many pages")

    def text(self, r):
        """A forward() report, for the agent."""
        out = [f"redone: {r['redone'] or '(no page)'}; now on page {r['at']!r} — {r['why']}"]
        if r["differs"]:
            out.append("what differs on it:\n" + "\n".join(f"- {d}" for d in r["differs"]))
        if r["end"]:
            out.append("this is where the record ends: see the page, fill what is left, and carry on.")
        else:
            out.append("see the page and fix it (what the record did here is below); then call replay again "
                       "to go on with the record, or carry on by hand.")
        acts = self.last_acts(r["at"])
        if acts and not r["end"]:
            out.append("the record's values on this page:\n" + "\n".join(
                f"- {i['role']} {i['name']!r} (under {i.get('above') or i.get('group')!r}): "
                f"{self.approved.get(R.norm(o['question']), o['answer']) if o.get('question') else o['answer']}"
                for i, o in acts.values()))
        return "\n".join(out)
