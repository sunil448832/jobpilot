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

from jobpilot.apply.explore_agentic import act as A, record as R

CHANGES = ("act", "clear", "press")
NOT_CHECKED = ("add", "file", "search", "press", "button")   # press / button: rows of older records


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


def save(slug, calls, former):
    """The record now: this session's calls, then the former record's calls on pages this
    session has not reached — a page a portal shows only some days (Workday's "Start Your
    Application" before a draft exists) stays known."""
    reached = {c.get("page") for c in calls if c["tool"] in CHANGES}
    rest = [c for c in former if c.get("page") not in reached]
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

    @staticmethod
    def choice(o, ident):
        return o.get("nature") in ("tick", "press") or (not o.get("nature") and ident["role"] in ("radio", "checkbox", "button"))

    def his_choice(self, c, ans):
        """The choice his answer names: this control when its label is his answer, else the
        nearest control of the same role labelled with it. None when there is none."""
        want = A.plain(ans.split(":", 1)[1] if ans.lower().startswith(("option:", "text:")) else ans)
        if A.plain(c.name) == want:
            return c
        same = [x for x in self.form.all_controls if x.id and x.role == c.role and A.plain(x.name) == want]
        return min(same, key=lambda x: abs(x.line - c.line)) if same else None

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
                    if o is None or o.get("how") in ("search", "button"):
                        continue                          # not on the page then, only searched, or a page button
                    if c is None:
                        differs.append(f"{row[0]} ({call['controls'].get(row[0], {}).get('name')!r}: {row[2]}) "
                                       "is not on the page")
                        continue
                    if o.get("how") == "add" and o.get("section_count") is not None and \
                            (f.section_count(o.get("section")) or 0) >= o["section_count"]:
                        continue                          # the section has its blocks already
                    ans = self.answer(row, o)
                    if ans is not None and o.get("question") and self.choice(o, call["controls"][row[0]]):
                        c = self.his_choice(c, ans)           # his answer names another of the choices
                        if c is None:
                            differs.append(f"no choice {ans!r} for {o['question']!r} on the page")
                            continue
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
                if call["tool"] == "press" and call.get("section") and \
                        f.section_count(call["section"]) == call.get("section_count"):
                    continue                              # its section is as the press left it (a Delete done)
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
            if q and (o.get("nature") in ("tick", "press") or ident["role"] in ("radio", "checkbox", "button")):
                continue                                  # a choice holds a state, not his answer's text: act's ok says it
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
            if page not in self.pages and (page == self.submit_page or f.is_last(page)):
                return stop("the record's last page", end=True)   # nothing to redo on it (a Review)
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


# ---------------------------------------------------------------- for the phone card

SOURCE = {"fact": "fact", "file": "fact", "chain": "pick", "guess": "you", "question": "you", "keep": "shown"}


def job_facts(slug):
    """The facts a filing of this job acts on — as form.Form builds them: the answers file,
    the job, learned answers, then his approved answers for this form."""
    from jobpilot.core.answers import load as load_yaml, load_learned, read_jd, detect_market
    from jobpilot.apply.explore_agentic import browser as B, facts as F
    meta, jd = read_jd(slug)
    answers = load_yaml("answers.yaml")
    approved = [{"match": q, "answer": p["answer"]} for q, p in (R.load(slug).get("placeholders") or {}).items()
                if (p.get("answer") or "").strip()]
    ctx = {"market": detect_market(meta.get("Location", ""), jd), "company": meta.get("Company", slug),
           "location": meta.get("Location", "")}
    return F.job_facts(answers, ctx, list(load_learned() or []) + approved, B.resume_path(answers, slug))


def plain_in(value, name):
    """Is the value part of the control's name (a list button's name grows with its pick)?"""
    return A.plain(value) in A.plain(name)


def values_by_page(slug):
    """What a filing will enter, page by page, from the record: each control's last act, its
    label (a choice's question; its section when it has one), the value the filing will give
    it — the fact looked up again, the choice, his answer — and where that comes from (fact /
    pick / you / shown): the phone card's list of values. [{"page", "rows": [{"label", "value", "src"}]}]."""
    rec, calls = load(slug)
    approved = approved_answers(slug, rec)
    held = {R.norm(q): p for q, p in {**(rec.get("placeholders") or {}), **(R.load(slug).get("placeholders") or {})}.items()}
    facts = job_facts(slug)

    def value_of(answer):
        a = str(answer or "").strip()
        a = a[5:].strip() if a.lower().startswith("keep:") else a
        low = a.lower()
        if low.startswith(("option:", "text:")):
            return a.split(":", 1)[1].strip()
        if low == "file:resume":
            return os.path.basename(str(facts.get("file:resume") or ""))
        return facts.get(a)

    pages, order = {}, []
    for call in calls:
        if call["tool"] != "act":
            continue
        page = call["page"]
        if page not in pages:
            pages[page] = {}
            order.append(page)
        for o in call.get("outcomes") or []:
            ident = call["controls"].get(o["id"])
            if ident and o.get("how") not in ("search", "button", "add"):
                pages[page][(ident["role"], ident["name"], ident["nth"], ident["group"])] = (ident, o)
    out = []
    for page in order:
        rows = []
        for ident, o in pages[page].values():
            name, group = ident["name"].rstrip("* "), ident.get("group") or ""
            q = o.get("question")
            given = value_of(o.get("answer"))
            nat = o.get("nature") or ("tick" if ident["role"] in ("radio", "checkbox", "switch") else
                                      "press" if ident["role"] == "button" and given and
                                      plain_in(given, ident["name"]) is False else "")
            if nat == "tick" and str(o.get("reads") or "").lower() in ("off", "false"):
                continue                                  # a choice left unticked: nothing to list
            question = group or ident.get("above") or name
            if q:
                label = q
                stood = (held.get(R.norm(q)) or {}).get("used") or o.get("shown")
                value = approved.get(R.norm(q)) or f"{stood} (to be answered)"
            elif o.get("how") == "file":
                label, value = question if ident["role"] == "button" else name, given or ""
            elif nat == "tick" and ident["role"] == "checkbox" and not group:
                label, value = name, "ticked"             # a lone checkbox ("I currently work here")
            elif nat in ("tick", "press"):
                label, value = question, name             # a choice: the question, and the one chosen
            elif ident["role"] == "button":
                label, value = question, given or str(o.get("reads") or "")   # a list button
            else:
                own = name or ident.get("above") or ""           # a box with no name of its own: its question
                label = f"{group} · {own}" if group and group not in own else own
                value = given or str(o.get("reads") or o.get("shown") or "")
            rows.append({"label": label[:120], "value": str(value)[:300], "src": SOURCE.get(o.get("how"), "")})
        if rows:
            out.append({"page": str(page).title(), "rows": rows})
    return out
