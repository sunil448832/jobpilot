"""explore_agentic/form.py — the live application one agent works on, and what each of its
tools does on the page. Every method runs on the browser's own thread (Playwright's sync
API cannot run inside the agent's event loop; session.py hands each call to that thread).

    see(scope, name, ids, snapshot)   the page, a section, an outline, or given controls
    act(rows, report)                 only these rows; what else the page changed comes back
    options(id) / search(id, pats)    a list read whole / searched — nothing picked
    clear(id)                         one field emptied
    inspect(id)                       one control's field HTML, read-only
    press(id)                         one page button or link; never one that sends
    finish(outcome, note, submit_id)  the end (and, on the last page, which button submits)
    submit(id)                        FILING only (mode "submit"): press the button that sends
                                      the application, behind code checks, and watch what the
                                      portal does — at most MAX_PRESSES presses in a filing
    (replay)                          when a record exists: calls.Redo.forward, via session.py

Every tool call goes through call(): done, and RECORDED — the tool, its arguments, the page,
the lasting identity of each control it names (role, name, which one of that name, its
group, the question above it) and what it did — into calls.json, which calls.py redoes in a
later session, where ids mean nothing.

Ids are stable: an element keeps its id (data-agent-id) while it is on the page, so acting
on one field never renumbers the others; a field that appears gets the next free id.
see.describe reads the page, act.act_rows acts.
"""
import datetime as dt
import re
import time

from jobpilot.core.answers import read_jd, detect_market
from jobpilot.core.config import cfg
from jobpilot.apply import platforms
from jobpilot.apply.explore_agentic import browser as B, see as S, act as A, facts as F, record as R
from jobpilot.apply.explore_agentic.controls import LINE, unquote

MAX_PRESSES = 3
# what a portal says after a press: taken, or refused for good (the filing's watch)
CONFIRMED_RX = re.compile(r"thank(s| you) for (applying|your application)|application (has been )?(submitted|received)|"
                          r"successfully submitted|we('ve| have) received your application|congratulations|"
                          r"your application (was|has been) (successfully )?(submitted|sent|received)", re.I)
REFUSED_RX = re.compile(r"(couldn.t|could not|cannot|can.t|unable to) (submit|process) (your )?application|"
                        r"may not apply more than|application limit|already applied|you have already applied", re.I)
ID_IN_LINE = re.compile(r"^(\s*- )\[(c\d+)\] ")
SECTIONS = ("group", "heading", "dialog", "region", "form", "radiogroup")


def line_info(line):
    """(indent, role, name, attrs) of a snapshot line, its id mark taken off; None for a
    line that is not a node."""
    m = LINE.match(unquote(ID_IN_LINE.sub(r"\1", line)))
    if not m:
        return None
    return len(m[1]), m[2], (m[3] or "").replace('\\"', '"'), m[4] or ""


def level(attrs):
    m = re.search(r"level=(\d+)", attrs or "")
    return int(m[1]) if m else 9


def section_range(lines, name):
    """(start, end) of the lines of the section named `name` (a heading or a group, its name
    containing the words, case aside): a group's own lines, or a heading's lines down to the
    next heading of its level or higher."""
    want = (name or "").strip().lower()
    for i, line in enumerate(lines):
        info = line_info(line)
        if not info or info[1] not in SECTIONS or not info[2] or want not in info[2].lower():
            continue
        ind, role, _, attrs = info
        end = i + 1
        while end < len(lines):
            nxt = line_info(lines[end])
            if nxt and (nxt[0] < ind or (nxt[0] == ind and role != "heading")
                        or (role == "heading" and nxt[1] == "heading" and level(nxt[3]) <= level(attrs))):
                break
            end += 1
        return i, end
    return None


def outline(lines, controls):
    """The page's sections — each group and heading, indented as the page nests them — and how
    many controls each holds."""
    out, last = [], None
    for i, line in enumerate(lines):
        info = line_info(line)
        if not info or info[1] not in ("group", "heading", "dialog", "radiogroup") or not info[2]:
            continue
        if info[1] == "heading" and info[2] == last:
            continue                                     # a group's own heading: the group already names it
        last = info[2]
        rng = section_range(lines[i:], info[2])
        n = sum(1 for c in controls if c.id and rng and i + rng[0] <= c.line < i + rng[1])
        if n:
            out.append(f"{'  ' * (info[0] // 2)}{info[1]} {info[2]!r}: {n} control(s)")
    return "\n".join(out) or "(no named sections)"


# every pinned control gets its lasting id: kept when it has one, the next free one when not
AGENT_IDS_JS = r"""(start) => {
  let next = start;
  const out = {};
  for (const el of document.querySelectorAll('[data-jp]')) {
    if (!el.getAttribute('data-agent-id')) el.setAttribute('data-agent-id', 'c' + (next++));
    out[el.getAttribute('data-jp') + '\u0000' + (el.getAttribute('role') || el.tagName.toLowerCase())] = el.getAttribute('data-agent-id');
  }
  return {ids: out, next};
}"""

FIELD_HTML_JS = r"""el => {
  const CTRL = 'input:not([type="hidden"]), textarea, select, button, [role="combobox"], [role="textbox"], [role="button"]';
  let box = el;
  for (let i = 0; i < 4 && box.parentElement; i++) {
    const p = box.parentElement;
    if ([...p.querySelectorAll(CTRL)].filter(x => x !== el && !el.contains(x)).length > 3) break;
    box = p;
  }
  const c = box.cloneNode(true);
  c.querySelectorAll('script, style, svg, path').forEach(n => n.remove());
  return c.outerHTML.replace(/\s+/g, ' ').replace(/ class="[^"]{30,}"/g, ' class="…"').slice(0, 4000);
}"""


class Form:
    """The live application: one browser, one page, the controls the agent last saw."""

    def __init__(self, slug, answers, learned, log=print, mode="explore"):
        self.slug, self.log, self.mode = slug, log, mode
        meta, jd = read_jd(slug)
        self.url = meta.get("Apply URL") or meta.get("Link")
        self.pid = R.load(slug).get("platform") or platforms.detect(self.url)[0]
        self.mod = platforms.get(self.pid)
        self.ctx = {"market": detect_market(meta.get("Location", ""), jd), "company": meta.get("Company", slug),
                    "role": meta.get("Role / Title", ""), "location": meta.get("Location", ""),
                    "company_slug": slug, "url": self.url}
        approved = {q: p["answer"] for q, p in (R.load(slug).get("placeholders") or {}).items()
                    if (p.get("answer") or "").strip()}
        base = len(learned or [])
        self.learned = list(learned or []) + [{"match": q, "answer": a} for q, a in approved.items()]
        self.approved = [(f"learned:{base + i}", q, a) for i, (q, a) in enumerate(approved.items())]
        self.resume = B.resume_path(answers, slug)
        self.facts = F.job_facts(answers, self.ctx, self.learned, self.resume)
        self.controls, self.lines, self.snap = {}, {}, []
        self.step, self.seen, self.next_id = None, set(), 1
        self.placeholders, self.filled, self.pages, self.done = {}, {}, [], None
        self.calls, self.outcomes, self.submit_control = [], None, None   # the record replay.py replays
        self.item, self.presses, self.sent = None, [], None              # filing: its queue item, the presses
        self.redo, self.save_calls = None, None      # a former record to redo (calls.Redo); the record's saver

    # ------------------------------------------------------------ the browser
    def open(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.br = B.open_browser(self.pw)
        self.page = self.br.new_page()
        url = self.mod.form_url(self.url) if self.mod and hasattr(self.mod, "form_url") else self.url
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        B.wait_quiet(self.page.main_frame, max_s=8)
        gone = B.dead_posting(self.page)
        return f"the posting is closed ({gone})" if gone else None

    def close(self):
        try:
            self.br.close()
            self.pw.stop()
        except Exception:
            pass

    def frame(self):
        return S.form_frame(self.page, getattr(self.mod, "FRAME_PATTERNS", ()))

    def drawn(self, wait_s=20, settle_s=4):
        t0, last, still = time.time(), None, 0
        frame = self.frame()
        while time.time() - t0 < wait_s:
            snap = S.snapshot(frame)
            if sum(1 for c in S.parse(snap) if c.role in S.FORM_ROLES) >= 2:
                break
            still = still + 1 if snap == last else 0
            if still >= 6 and time.time() - t0 >= settle_s:
                break
            last = snap
            self.page.wait_for_timeout(250)
            frame = self.frame()
        B.wait_quiet(frame, max_s=4)
        return frame

    def step_name(self, frame):
        if self.mod and hasattr(self.mod, "step"):
            s = self.mod.step(self.page)
            if s:
                return s
        try:
            return (frame.get_by_role("heading").first.inner_text(timeout=1500) or "").strip()[:60] or "page"
        except Exception:
            return "page"

    def is_last(self, step):
        return bool(self.mod and hasattr(self.mod, "is_last") and self.mod.is_last(step))

    def pass_gate(self):
        """A platform's account gate (Workday's sign-in / create account) is passed by code,
        with credentials the agent never sees."""
        if not (self.mod and hasattr(self.mod, "at_gate") and self.mod.at_gate(self.page)):
            return False
        self.log("  [platform] account gate: signing in")
        ok = self.mod.start(self.page, self.ctx, self.log, lambda p: False)
        self.log(f"  [platform] account gate {'passed' if ok else 'NOT passed'}")
        return ok

    # ------------------------------------------------------------ reading the page
    def read(self):
        """The page read afresh: every control with its lasting id, its line for the agent, the
        snapshot with ids in place. Returns the frame."""
        self.pass_gate()
        frame = self.drawn()
        step = self.step_name(frame)
        if step != self.step:
            S.forget_lists()
            if step not in self.pages:
                self.pages.append(step)
        self.step = step
        fields = sum(1 for c in S.parse(S.snapshot(frame)) if c.role in S.FORM_ROLES)
        try:                                             # pins of an earlier read: the new read pins afresh
            frame.evaluate("() => document.querySelectorAll('[data-jp]').forEach(e => e.removeAttribute('data-jp'))")
        except Exception:
            pass
        controls, text, snap = S.describe(frame, open_lists=True, chrome=fields < 2)
        # describe numbered the controls c1, c2 ... for this read: each gets its lasting id instead
        try:
            got = frame.evaluate(AGENT_IDS_JS, self.next_id)
            self.next_id = got["next"]
            lasting = got["ids"]
        except Exception:
            lasting = {}
        remap = {}
        for c in controls:
            if not c.id:
                continue
            role = (c.facts or {}).get("role") or (c.facts or {}).get("tag") or c.role
            new = lasting.get(c.ref + "\u0000" + role) or lasting.get(c.ref + "\u0000" + c.role)
            if new:
                remap[c.id] = new
        for c in controls:
            if c.id in remap:
                c.id = remap[c.id]
        sub = lambda m: m.group(1) + remap.get(m.group(2), m.group(2))
        text = "\n".join(re.sub(r"^()(c\d+)(?=\s)", sub, l) for l in text.splitlines())
        snap = "\n".join(re.sub(r"^(\s*- \[)(c\d+)(?=\])", sub, l) for l in snap.splitlines())
        self.controls = {c.id: c for c in controls if c.id}
        self.lines = {l.split()[0]: l for l in text.splitlines() if l.startswith("c")}
        self.snap = snap.splitlines()
        self.all_controls = controls
        return frame

    def identity(self, c):
        """A control as a later session can find it again: role, name, which one of that name,
        its section and the question above it, and which one it is among the controls of that
        role under that same question."""
        same = [x for x in self.all_controls if x.role == c.role and x.group == c.group and x.above == c.above]
        return {"role": c.role, "name": c.name, "nth": c.nth, "group": c.group, "above": c.above,
                "qn": next((i for i, x in enumerate(same) if x is c), 0)}

    def find(self, ident):
        """The control of the latest read that a recorded identity names, looked for where it
        was: the same role and name in the same group (block); else the same role under the
        same question in that group, the same one of those (a list button's name grows with
        what it shows). Only a control in no group (Next, Apply) is taken by its name and
        number on the page alone. None when it is not there."""
        cs = [c for c in self.all_controls if c.id]
        g, above = ident.get("group") or "", ident.get("above")
        named = [c for c in cs if c.role == ident["role"] and c.name == ident["name"]]
        here = [c for c in named if c.group == g]
        hit = next((c for c in here if c.nth == ident["nth"]), None) or (here[0] if len(here) == 1 else None)
        if hit is None and (g or above):
            same = [c for c in cs if c.role == ident["role"] and c.group == g and c.above == above]
            q = ident.get("qn", 0)
            hit = same[q] if len(same) > q else None
        if hit is None and not g:
            hit = next((c for c in named if c.nth == ident["nth"]), None)
        return hit

    def section_count(self, name):
        """How many controls the section of this name holds now (a repeated section's blocks)."""
        rng = section_range(self.snap, name) if name else None
        return sum(1 for c in self.all_controls if c.id and rng and rng[0] <= c.line < rng[1]) if rng else None

    def values(self):
        """{id: what the control shows} for the controls of the latest read."""
        return {cid: (S.shown(c) or (c.facts or {}).get("shows") or "") for cid, c in self.controls.items()}

    def header(self, frame):
        errs = S.errors(frame)
        held = [f"- {p['field']} holds {p['used']!r} — asked as {q!r}" for q, p in self.placeholders.items()
                if p.get("page") == self.step]
        return "\n".join([
            f"PAGE: {self.step}" + ("   — the form's LAST PAGE: do not submit; call finish when it is complete"
                                    if self.is_last(self.step) else ""),
            "PAGE ERRORS: " + ("; ".join(errs) if errs else "(none)"),
            "PLACEHOLDERS ON THIS PAGE (not the applicant's answers): " + ("\n" + "\n".join(held) if held else "(none)")])

    # ------------------------------------------------------------ the record
    def call(self, tool, args, fn, *a):
        """One tool call of the agent, done and recorded as it was asked: the tool, its
        arguments, the page it was on, the lasting identity of every control it names (so a
        later session finds them again), and what it did. calls.json; replay.py runs them again."""
        if not self.controls and tool not in ("see", "finish"):
            self.read()
        page = self.step
        ids = ([r[0] for r in args.get("rows") or [] if isinstance(r, list) and r] + list(args.get("ids") or [])
               + [args[k] for k in ("id", "submit_id") if args.get(k)])
        named = {i: self.identity(self.controls[i]) for i in ids if i in self.controls}
        self.outcomes = None
        result = fn(*a)
        entry = {"n": len(self.calls) + 1, "tool": tool, "args": args, "page": page, "controls": named}
        if tool == "act":
            entry["outcomes"] = self.outcomes
        if tool == "press":
            entry["moved_to"] = self.step if self.step != page else None
            if not entry["moved_to"] and named:          # a Delete / Add: its section's size after it
                sec = next(iter(named.values())).get("group")
                entry["section"], entry["section_count"] = sec, self.section_count(sec)
        entry["result"] = str(result)[:1500]
        self.calls.append(entry)
        if self.save_calls:                              # saved after every call: resumable anywhere
            self.save_calls(self.calls)
        return result

    # ------------------------------------------------------------ the tools
    def see(self, scope="page", name=None, ids=None, snapshot=False):
        frame = self.read()
        first = self.step not in self.seen
        self.seen.add(self.step)
        out = [self.header(frame)]
        if scope == "outline":
            out.append("SECTIONS\n" + outline(self.snap, self.all_controls))
        elif scope == "section":
            rng = section_range(self.snap, name)
            if rng is None:
                out.append(f"no section named {name!r} on this page. SECTIONS\n" + outline(self.snap, self.all_controls))
            else:
                mine = [c.id for c in self.all_controls if c.id and rng[0] <= c.line < rng[1]]
                out.append(f"CONTROLS OF {name!r}\n" + ("\n".join(self.lines[i] for i in mine if i in self.lines) or "(none)"))
                if snapshot:
                    out.append("SNAPSHOT OF THE SECTION\n" + "\n".join(self.snap[rng[0]:rng[1]]))
        elif scope == "ids":
            out.append("CONTROLS\n" + "\n".join(self.lines.get(i, f"{i}: not on the page") for i in (ids or [])))
        else:
            out.append("CONTROLS\n" + ("\n".join(self.lines.values()) or "(none)"))
            if snapshot or first:
                out.append("SNAPSHOT\n" + "\n".join(self.snap))
        self.log(f"  see {scope}{' ' + repr(name) if name else ''} on '{self.step}': {len(self.controls)} controls")
        return "\n\n".join(out)

    def act(self, rows, report="changes"):
        if not self.controls:
            self.read()
        good = [r for r in rows if isinstance(r, list) and len(r) >= 3]
        unknown = [r[0] for r in good if r[0] not in self.controls]
        before = self.values()
        acting = {r[0]: (self.controls[r[0]], r) for r in good if r[0] in self.controls}
        outcomes = A.act_rows(self.frame(), list(self.controls.values()), [r for r in good if r[0] in self.controls],
                              self.facts, self.resume, log=lambda s: self.log("  " + s.strip()))
        lines = [self.outcome_line(o) for o in outcomes]
        if unknown:
            lines.append(f"ids not on the page: {unknown} — see again for the current ids")
        B.wait_quiet(self.frame(), max_s=3)
        frame = self.read()
        self.outcomes = []                               # each row's outcome, for the record
        for o in outcomes:
            if o.get("id") not in acting:
                continue
            c, row = acting[o["id"]]
            done = {"id": o["id"], "kind": row[1], "answer": row[2], "how": o.get("how"), "nature": o.get("nature"),
                    "ok": bool(o.get("ok")),
                    "shown": str(o.get("shown") or "")[:300], "question": (o.get("placeholder") or {}).get("question")}
            if o.get("how") == "add":
                done["section"], done["section_count"] = c.group, self.section_count(c.group)
            elif o["id"] in self.controls:               # read back as a later check reads it
                done["reads"] = A.current(frame, self.controls[o["id"]])
            self.outcomes.append(done)
        after = self.values()
        acted = {r[0] for r in good}
        changed = [i for i in after if i in before and i not in acted and after[i] != before[i]]
        new = [i for i in after if i not in before]
        gone = [i for i in before if i not in after]
        out = ["\n".join(lines) or "(no rows)"]
        if changed:
            out.append("OTHER FIELDS THE PAGE CHANGED (not acted on):\n" + "\n".join(
                f"{self.lines.get(i, i)}   (was {before[i]!r})" for i in changed))
        if new:
            out.append("NEW FIELDS:\n" + "\n".join(self.lines.get(i, i) for i in new))
        if gone:
            out.append(f"FIELDS GONE: {gone}")
        if report == "page":
            out.append(self.header(frame) + "\n\nCONTROLS\n" + "\n".join(self.lines.values()))
        else:
            out.append(self.header(frame))
        return "\n\n".join(out)

    def outcome_line(self, o):
        p = o.get("placeholder")
        if p:
            self.placeholders[p["question"] or o.get("control", "")] = {
                "used": p["used"], "field": o.get("control", ""), "candidates": p.get("candidates") or [], "page": self.step}
        elif o.get("ok") and o.get("how") not in ("search", "add"):
            self.filled[f"{self.step} :: {o.get('control', '')}"] = str(o.get("shown") or "")[:120]
        offered, wanted, shown = o.get("offered"), o.get("wanted"), str(o.get("shown") or "")
        check = not o.get("ok") and not offered and not o.get("error") and wanted and shown.strip()
        detail = (("searched: " + "; ".join(f"{t!r} -> {S.compact(h)}" for t, h in offered.items()))
                  if isinstance(offered, dict) else
                  (f"{o.get('error')}; the list holds {S.compact(offered)}" if offered else
                   (f"set to {wanted!r}; the field now shows {shown[:200]!r} — the same entry (a short or cut "
                    "form of it)? if not, act on it again" if check else (o.get("error") or f"shows {shown[:200]!r}"))))
        label = "ok" if o.get("ok") else "CHECK" if check else "NOT DONE"
        return f"{o.get('id')}  {label}  {o.get('control', '')}: {detail}" + \
            (f"   (a placeholder: {p['used']!r}, asked later)" if p else "")

    def control(self, cid):
        if not self.controls:
            self.read()
        c = self.controls.get(cid)
        if c is None:
            raise KeyError(f"{cid!r} is not on the page — see again for the current ids")
        return c

    def options(self, cid):
        c = self.control(cid)
        tree, more = S.read_list_once(self.frame(), c)
        if not tree:
            return f"{cid}: no list opened from it (a box that fills in only as you type: search it)"
        rows = S.rows_once(self.frame(), c) if more else None
        if rows:
            return f"{cid}: {len(rows)} entries, read by scrolling: {S.compact(rows)}"
        return f"{cid}: {S.compact_tree(tree)}"

    def search(self, cid, patterns):
        hits = A.search_list(self.frame(), self.control(cid), [p for p in patterns if p][:5])
        return f"{cid} searched: " + "; ".join(f"{t!r} -> {S.compact(h)}" for t, h in hits.items())

    def clear(self, cid):
        c = self.control(cid)
        r = A.clear_field(self.frame(), c)
        self.log(f"  clear {c.ref!r}: {r}")
        return f"{cid} {'emptied' if r.get('ok') else 'NOT emptied'}: shows {r.get('shown')!r}" + \
            (f" — {r['error']}" if r.get("error") else "")

    def inspect(self, cid):
        c = self.control(cid)
        try:
            return f"{cid} {c.role} {c.ref!r} — its field's HTML:\n" + A.locate(self.frame(), c).evaluate(FIELD_HTML_JS)
        except Exception as e:
            return f"{cid}: could not read its HTML ({type(e).__name__})"

    def press(self, cid):
        c = self.control(cid)
        name = (c.name or "").lower()
        if self.is_last(self.step) or "submit" in name or "send application" in name:
            return "refused: this session never sends the application — call finish"
        before_step, before_snap = self.step, S.snapshot(self.frame())
        S.tap(A.locate(self.frame(), c))
        B.wait_quiet(self.page.main_frame, max_s=4)
        self.pass_gate()
        t0 = time.time()
        while time.time() - t0 < 12:                     # the page moves on, or says why not
            B.wait_quiet(self.page.main_frame, max_s=3)
            frame = self.frame()
            if self.step_name(frame) != before_step or S.snapshot(frame) != before_snap or S.errors(frame):
                break
        frame = self.read()
        if self.step != before_step:
            self.log(f"  press {c.ref!r}: moved to '{self.step}'")
            return f"moved on: now on page {self.step!r}" + \
                (" — the form's LAST PAGE" if self.is_last(self.step) else "") + ". see it."
        errs = S.errors(frame)
        self.log(f"  press {c.ref!r}: stayed on '{self.step}' {errs[:3]}")
        return (f"still on page {self.step!r}. " + ("The page says: " + "; ".join(errs) if errs else
                "It shows no error message") + ". see it for what changed.")

    def finish(self, outcome, note, submit_id=None):
        """The end. On the last page the agent names the button that sends the application: it
        is recorded (never pressed here) for the submit to press, after its checks."""
        if submit_id and submit_id in self.controls:
            self.submit_control = {"page": self.step, **self.identity(self.controls[submit_id])}
        self.done = (outcome, note)
        self.log(f"  finish: {self.done}" + (f" submit button: {self.submit_control['name']!r}" if self.submit_control else ""))
        return "finished."

    # ------------------------------------------------------------ filing: the submit
    def submit(self, cid):
        """Press the button that sends the application — only in a filing of an approved item,
        with no placeholder set while filing, a submit button, fewer than
        MAX_PRESSES presses, and no press whose outcome was sent or unclear. Then watch."""
        c = self.control(cid)
        why = self.submit_refused(c)
        if why:
            return "refused: " + why
        from jobpilot.apply.explore_agentic.card import save as _save
        page_was = self.step
        self.item.setdefault("submit_presses", []).append(dt.datetime.now().isoformat(timespec="seconds"))
        _save(self.item)                                 # written BEFORE the press: a rerun never presses blind
        standing = self.page_lines()                     # what the page said before: never read as its answer
        S.tap(A.locate(self.frame(), c))
        self.log(f"  submit {c.name!r} (press {len(self.presses) + 1} of {MAX_PRESSES})")
        outcome, detail = self.watch(page_was, c, standing)
        self.presses.append({"page": page_was, "button": c.name, "outcome": outcome, "detail": detail})
        if outcome == "captcha":                         # certainly not sent: the press does not block a later filing
            self.item["submit_presses"].pop()
            self.item.setdefault("captcha_at", []).append(dt.datetime.now().isoformat(timespec="seconds"))
            _save(self.item)
        if outcome in ("submitted", "refused", "unclear", "captcha"):
            self.sent = (outcome, detail)
        self.log(f"  submit -> {outcome}: {detail[:300]}")
        return {"submitted": "SUBMITTED: the portal confirmed it. call finish('submitted', note).",
                "refused": f"the portal REFUSED it: {detail}. Do not press again; call finish('refused', note).",
                "unclear": f"UNCLEAR: {detail}. It may have been sent: do not press again; call finish('unclear', note).",
                "captcha": f"CAPTCHA: {detail}. Do not press again; call finish('captcha', note).",
                }.get(outcome, f"NOT ACCEPTED: {detail}. see the page, fix what it says, then submit again "
                               f"({MAX_PRESSES - len(self.presses)} press(es) left).")

    def submit_refused(self, c):
        """Why this press may not happen (None when it may)."""
        if self.mode != "submit" or self.item is None:
            return "this session does not send the application — call finish"
        if self.item.get("status") != "approved" or self.item.get("submitted_at"):
            return f"the application is not approved for sending ({self.item.get('status')})"
        if self.sent:
            return f"a press already ended as {self.sent[0]} — never press again"
        if len(self.presses) >= MAX_PRESSES:
            return f"Submit was pressed {MAX_PRESSES} times — call finish('stuck', note)"
        if self.placeholders:
            return (f"a placeholder was set while filing ({list(self.placeholders)[:3]}): the applicant must answer "
                    "it first — call finish('needs-answer', note)")
        rec = self.submit_control
        if not (rec and self.find(rec) is c) and not re.search(r"submit|send|apply", c.name or "", re.I):
            return f"{c.name!r} is not the button that sends the application"
        return None

    def page_lines(self):
        """Every line of text the page's frames show now."""
        return {ln.strip() for fr in self.page.frames for ln in S.body_text(fr, 20000).splitlines() if ln.strip()}

    def watch(self, page_was, c, standing=frozenset()):
        """What the portal did after the press: (submitted | refused | not-accepted | unclear,
        detail), read only from text that was NOT on the page before the press (`standing`: a
        banner about application limits is not a refusal). Not accepted only when the form is
        plainly still there — errors on it, or sent back to an earlier page; anything else
        without a confirmation is unclear."""
        page, ident = self.page, self.identity(c)
        page.wait_for_timeout(2500)
        if B.enter_verification_code(self.frame(), self.item, lambda: S.tap(A.locate(self.frame(), c))):
            page.wait_for_timeout(2500)
        erred = 0
        for _ in range(int(cfg("browser.submit_poll_s", 30))):
            if B.captcha(page):
                return "captcha", "the portal shows a captcha after Submit: nothing is sent until a person solves it"
            text = "\n".join(ln for ln in self.page_lines() if ln not in standing)
            m = REFUSED_RX.search(text)
            if m:
                return "refused", text[max(0, m.start() - 60):m.end() + 140].strip()
            if CONFIRMED_RX.search(text):
                return "submitted", CONFIRMED_RX.search(text).group(0)
            frame = self.frame()
            step = self.step_name(frame)
            if step != page_was and step in self.pages:
                self.read()
                return "not-accepted", f"the portal sent the form back to page {step!r}: " + \
                    ("; ".join(S.errors(self.frame())[:6]) or "no message shown")
            errs = S.errors(frame)
            erred = erred + 1 if errs else 0
            if erred >= 3:                               # errors that stay: the form was not taken
                self.read()
                still = self.find(ident) is not None
                if still:
                    return "not-accepted", "the page says: " + "; ".join(errs[:6])
            page.wait_for_timeout(1000)
        self.read()
        return "unclear", ("no confirmation and no error; the submit button is " +
                           ("still there" if self.find(ident) is not None else "gone") + f", page {self.step!r}")
