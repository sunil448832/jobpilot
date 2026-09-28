#!/usr/bin/env python3
"""
walk.py — PASS 1: walk an application form page by page to its last page, never press
Submit, and queue it for approval (docs/exploration-plan.md).

    start     the posting -> the form: the platform's own start (Workday's sign-in), else
              the button the map marks `start`
    per page  see -> map -> act -> see, in rounds:
                  see   see.describe: the page's controls, their HTML facts, their lists
                  map   mapper.map_page: one Claude call — [id, write|select, answer] rows
                  act   act.act_rows: every row on its control, read back
                  see   the page again: when every action took, nothing was turned back and no
                        field appeared or went away, the page is done — no further Claude
                        call; else the next round, with the placeholders this page holds and
                        what the last act could not finish (LAST ACT)
    next      the button the map marks `next` (a platform may press it its own way); a
              page that refuses goes back to its rounds, the refusal under LAST ACT
    last      the platform's last page (Workday: Review), or a page whose map marks submit
              and no next: its submit button is recorded, never pressed
    record    applications/<slug>/explore.json: each page's rounds (rows, outcomes) and
              the placeholders — the questions for the applicant, with their candidates
    queue     data/queue/<id>.json: the card the phone shows
"""
import datetime as dt
import json
import os
import time

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL
from jobpilot.apply import platforms
from jobpilot.apply.explore import browser as B, facts as F, record as R
from jobpilot.apply.explore import act as A, see as S, mapper as M, reuse


def trim(o):
    """An act outcome as the record keeps it."""
    keep = ("id", "control", "kind", "answer", "how", "ok", "kept", "shown", "source", "placeholder", "error", "offered")
    return {k: o[k] for k in keep if o.get(k) not in (None, "", [], {})}


class Walk:
    """Everything one form walk carries: the job, its facts, the record being written."""

    def __init__(self, ctx, answers, learned, log=print):
        self.ctx, self.answers, self.learned, self.log = ctx, answers, learned, log
        self.slug = ctx["company_slug"]
        self.pid = ctx.get("portal") or "unknown"
        self.mod = platforms.get(self.pid)
        self.company = reuse.company_key(self.pid, ctx.get("url"), ctx.get("company"))
        self.resume = B.resume_path(answers, self.slug)
        self.facts = F.job_facts(answers, ctx, learned, self.resume)
        self.model, self.effort = ctx.get("map_model"), ctx.get("map_effort")     # else llm.map's
        old = R.load(self.slug)
        self.answered = {q: p["answer"] for q, p in (old.get("placeholders") or {}).items() if p.get("answer")}
        self.record = {"url": ctx.get("url"), "platform": self.pid, "company": self.company,
                       "explored_at": dt.datetime.now().isoformat(timespec="seconds"), "reached_end": False,
                       "pages": [], "placeholders": {}, "submit": None,
                       "attempts": old.get("attempts") or []}
        self.warnings, self.filled, self.unfilled = [], {}, {}      # unfilled: {page: [control]}

    # ------------------------------------------------------------ one page
    def page_rec(self, step):
        rec = next((p for p in self.record["pages"] if p["step"] == step), None)
        if rec is None:
            rec = {"step": step, "rounds": [], "acted": {}, "tried": []}
            self.record["pages"].append(rec)
        return rec

    def note(self, step, rec, o):
        """What an act outcome leaves: the control's last outcome, what the phone shows as
        filled, and a placeholder as a question for the applicant."""
        rec["acted"][o.get("control", o.get("id"))] = trim(o)
        p = o.get("placeholder")
        if p:
            q = p["question"] or o.get("control", "")
            self.record["placeholders"][q] = {
                "value": p["used"], "options": p.get("candidates") or [],
                "kind": "select" if p.get("candidates") else "text",
                "answer": self.answered.get(q), "field": o.get("control", ""), "page": step,
                "note": "picked the likeliest choice — confirm or change" if p.get("candidates")
                        else "nothing stored: filled in for now — your answer is needed"}
        elif o.get("ok") and o.get("how") not in ("button", "search", "add"):
            self.filled[o.get("control", "")] = str(o.get("shown") or "")[:80]

    @staticmethod
    def shape(frame):
        """How many controls of each role the page shows: a field appearing or going away
        (a block added, a question a choice opened) changes it; a value does not."""
        out = {}
        for c in S.parse(S.snapshot(frame)):
            out[c.role] = out.get(c.role, 0) + 1
        return out

    def do_page(self, frame, step, last_act="(none)", chrome=False):
        """The page's rounds of see -> map -> act, until nothing is left to do. Returns the
        page's buttons as the map marked them: {"next" | "submit" | "start": control}."""
        rec = self.page_rec(step)
        B.wait_quiet(frame, max_s=6)
        self.capture(frame, step)
        held = [o for o in rec["acted"].values() if o.get("placeholder")]    # set on this page earlier
        buttons, left = {}, []
        for _ in range(int(cfg("fill.map_rounds", 6))):
            m = M.map_page(frame, step, self.facts, self.learned, placeholders=M.placeholders_text(held),
                           last_act=self.with_history(rec, last_act), chrome=chrome,
                           model=self.model, effort=self.effort, log=self.log)
            self.keep_io(step, len(rec["rounds"]) + 1, m)
            by_id = {c.id: c for c in m["controls"] if c.id}
            rows, buttons = [], {}
            for r in m["rows"]:
                how, what = A.read_answer(r[2], self.facts, self.resume)
                if how == "button":
                    buttons[what] = by_id[r[0]]
                else:
                    rows.append(r)
            shape = self.shape(frame)
            outcomes = A.act_rows(frame, m["controls"], rows, self.facts, self.resume, log=self.log)
            for o in outcomes:
                self.note(step, rec, o)
            held += [o for o in outcomes if o.get("placeholder")]
            rec["rounds"].append({"round": len(rec["rounds"]) + 1, "rows": m["rows"],
                                  "problems": [[p, why] for p, why in m["problems"]],
                                  "outcomes": [trim(o) for o in outcomes]})
            left = [o for o in outcomes if not o.get("ok")]
            if not left and not m["problems"]:
                if not any(o.get("acted") for o in outcomes):
                    break                                      # nothing to do, nothing failed: the page is as it should be
                B.wait_quiet(frame, max_s=3)
                if self.shape(frame) == shape:
                    break                                      # every action took and the page kept its fields: done
            if not left and not m["problems"]:
                last_act = "(none)"                            # new fields showed: they are mapped next
                continue
            last_act = M.last_act_text(outcomes, m["problems"])
            rec["tried"] += [line for line in last_act.splitlines() if line.startswith("- ")]
            rec["tried"] += [f"- {o.get('control')}" + (f" in {o['group']!r}" if o.get("group") else "")
                             + " was pressed: that block was removed on purpose (its required list does not hold "
                               "the fact) — do not add it again"
                             for o in outcomes if o.get("how") == "press" and o.get("ok")]
            B.wait_quiet(frame, max_s=3)
        else:
            left += [{"control": json.dumps(p, ensure_ascii=False), "error": why} for p, why in m["problems"]]
        for o in left:
            self.warnings.append(f"{o.get('control')!s} on '{step}' could not be filled: {o.get('error') or o.get('shown')}")
        self.unfilled[step] = [str(o.get("control")) for o in left]
        return buttons

    def with_history(self, rec, last_act, keep=20):
        """This round's LAST ACT, and what earlier rounds on this page could not finish — every
        search already tried, every entry not found, every refusal — so a new round (after a
        refusal too) does not try again what already failed."""
        now = [] if last_act in (None, "", "(none)") else [last_act]
        seen, earlier = set(last_act.splitlines()) if now else set(), []
        for line in reversed(rec.get("tried") or []):
            if line not in seen and line not in earlier:
                earlier.append(line)
        earlier = list(reversed(earlier[:keep]))
        if earlier:
            now.append("EARLIER ON THIS PAGE (tried before, did not finish):\n" + "\n".join(earlier))
        return "\n".join(now) or "(none)"

    def keep_io(self, step, rnd, m):
        """During the review phase: each round's prompt and Claude's reply, as sent and as
        received, under tests/maps/<slug>/ — page number, page, round."""
        if not m.get("prompt"):
            return
        n = next((i + 1 for i, p in enumerate(self.record["pages"]) if p["step"] == step), 0)
        base = os.path.join(TOOL, "tests", "maps", self.slug, f"page{n:02d}-{reuse.slug(step)}.r{rnd:02d}")
        try:
            os.makedirs(os.path.dirname(base), exist_ok=True)
            for ext, text in ((".prompt.txt", m["prompt"]), (".reply.txt", m["reply"])):
                with open(base + ext, "w", encoding="utf-8") as f:
                    f.write(text)
        except OSError:
            pass

    # ------------------------------------------------------------ the walk
    def frame(self, page):
        return S.form_frame(page, getattr(self.mod, "FRAME_PATTERNS", ()))

    def drawn(self, page, wait_s=20, settle_s=4):
        """The page's frame once its fields are drawn: a wizard page draws its heading first
        and its fields a moment later. A page that shows fewer than two fields (a review
        page) is taken once it has not changed for a moment, after `settle_s`."""
        t0, last, still = time.time(), None, 0
        frame = self.frame(page)
        while time.time() - t0 < wait_s:
            snap = S.snapshot(frame)
            if sum(1 for c in S.parse(snap) if c.role in S.FORM_ROLES) >= 2:
                break
            still = still + 1 if snap == last else 0
            if still >= 6 and time.time() - t0 >= settle_s:
                break
            last = snap
            page.wait_for_timeout(250)
            frame = self.frame(page)
        B.wait_quiet(frame, max_s=4)
        return frame

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

    def capture(self, frame, step):
        """The page as it was when mapped — HTML and accessibility snapshot, under
        applications/<slug>/pages/ — so its see and map can be run again offline
        (tests/see_page.py, tests/map_page.py)."""
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

    def press(self, frame, c):
        S.tap(A.locate(frame, c))
        B.wait_quiet(frame.page.main_frame, max_s=10)

    def press_start(self, page):
        """On the job posting (or an 'apply how?' dialog): the button the map marks start,
        pressed. True when one was."""
        B.wait_quiet(page.main_frame, max_s=8)
        frame = self.frame(page)
        if sum(1 for c in S.parse(S.snapshot(frame)) if c.role in S.FORM_ROLES) >= 2:
            return True                                    # the form is already showing: nothing to start
        if frame.get_by_role("dialog").count():
            step = "apply dialog"
        else:                                              # named by what it shows — never assumed a posting
            try:
                step = (frame.get_by_role("heading").first.inner_text(timeout=1500) or "").strip()[:60] or "page before the form"
            except Exception:
                step = "page before the form"
        m = M.map_page(frame, step, self.facts, self.learned, open_lists=False, chrome=True,
                       model=self.model, effort=self.effort, log=self.log)
        self.keep_io(step, len(self.page_rec(step)["rounds"]) + 1, m)
        by_id = {c.id: c for c in m["controls"] if c.id}
        start = next((by_id[r[0]] for r in m["rows"] if str(r[2]).strip().lower() == "start"), None)
        self.page_rec(step)["rounds"].append({"rows": m["rows"], "problems": [[p, why] for p, why in m["problems"]]})
        for p, why in m["problems"]:
            self.log(f"      turned back {p}: {why}")
        if start is None:
            self.log(f"    [map] no start button marked on '{step}'")
            return False
        self.log(f"    start: {start.role} {start.ref!r}")
        try:
            self.press(frame, start)
        except Exception as ex:
            self.log(f"    start {start.ref!r} could not be pressed: {type(ex).__name__}")
            return False
        return True

    def start(self, page):
        """The posting -> the form: the platform's start (Workday's account gate), else the
        map's start button while the page shows no form yet."""
        if self.mod and hasattr(self.mod, "start"):
            return self.mod.start(page, self.ctx, self.log, self.press_start)
        for _ in range(3):
            if sum(1 for c in S.parse(S.snapshot(self.frame(page))) if c.role in S.FORM_ROLES) >= 2:
                return True
            if not self.press_start(page):
                return True                          # no start button: the form is the page itself
        return True

    def next(self, page, frame, step, buttons):
        """(moved on, errors, is_last) by the button the map marks next. A page with a
        submit and no next is the last one: its submit is recorded, never pressed."""
        if "submit" in buttons:
            self.record["submit"] = buttons["submit"].name
        nxt = buttons.get("next")
        if nxt is None:
            return False, ([] if "submit" in buttons else ["the map marked no next or submit button"]), "submit" in buttons
        if self.mod and hasattr(self.mod, "next_page"):
            ok, errs = self.mod.next_page(page, step, nxt.name)
            return ok, errs, False
        before = S.snapshot(frame)
        self.press(frame, nxt)
        if S.snapshot(self.frame(page)) == before:
            return False, S.errors(frame) or [f"the page did not change after {nxt.name!r}"], False
        return True, [], False

    def run(self, page):
        """Walk from the posting to the last page. Returns reached_end."""
        if not self.start(page):
            self.warnings.append("could not get from the posting to the form")
            return False
        last_step = None
        for n in range(1, int(cfg("browser.max_pages", 12)) + 1):
            frame = self.drawn(page)
            step = self.step_name(page, frame, n)
            self.log(f"\n  page {n}: {step}")
            if step == last_step:
                self.warnings.append(f"'{step}' came back after Next — stopped")
                return False
            if step != last_step:
                S.forget_lists()                               # a new page: its lists are read afresh
            buttons = self.do_page(frame, step)
            if self.mod and hasattr(self.mod, "is_last") and self.mod.is_last(step):
                self.record["submit"] = buttons["submit"].name if "submit" in buttons else None
                return True
            ok, errs, last = self.next(page, frame, step, buttons)
            for _ in range(2):
                if not ok and not last and self.step_name(page, self.frame(page), n) != step:
                    ok = True                                  # it did move on, only slowly
                if ok or last or not errs:
                    break
                self.log(f"    the page refused: {errs[:3]} -> back to its rounds")
                buttons = self.do_page(frame, step, last_act="\n".join(
                    ["- Next was pressed and the page did not move on. What it says:"] + [f"  {e}" for e in errs[:8]]
                    + ["  Read the SNAPSHOT for what it asks, and answer those controls."]))
                ok, errs, last = self.next(page, frame, step, buttons)
            if last:
                return True
            if not ok:
                self.warnings.append(f"'{step}' would not save: {errs[:4]}")
                return False
            last_step = step
        self.warnings.append("too many pages — stopped")
        return False


# ---------------------------------------------------------------- the queue item

def questions_from(record):
    """The placeholders as review-page questions: the candidates the map found (whole
    chains for a nested list — "Social Media › LinkedIn" and "Job Board › LinkedIn" are
    different answers), and what stood in while exploring."""
    out = []
    for i, (q, p) in enumerate((record.get("placeholders") or {}).items()):
        if p.get("answer"):
            continue
        out.append({"qid": i, "label": q, "options": list(p.get("options") or []),
                    "kind": "select" if p.get("kind") == "select" else "text",
                    "required": True, "status": "open",
                    "page": p.get("page") or "", "field": p.get("field") or "",
                    "value": str(p.get("value") or ""),
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
        # a control left unfilled is neither ready to approve nor askable on the phone
        "status": ("needs_input" if questions else "pending") if reached and not unfilled else "failed",
        "fail_reason": (None if reached and not unfilled else
                        f"{len(unfilled)} control(s) could not be filled: {unfilled[:6]}" if reached else
                        "exploration did not reach the last page — " + (w.warnings[-1][:200] if w.warnings else "no reason given")),
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(B.QUEUE_DIR, exist_ok=True)
    with open(os.path.join(B.QUEUE_DIR, f"{item_id}.json"), "w") as f:
        json.dump(item, f, indent=2)
    return item


def explore(ctx, answers, learned, pay, log=print):
    """PASS 1 for one application. Returns the queue item written."""
    t0, plain = time.time(), log
    log = lambda s, **kw: plain(f"[{time.time() - t0:5.0f}s]{s}", **kw)     # where the minutes go
    w = Walk(ctx, answers, learned, log)
    mod = w.mod
    url = mod.form_url(ctx["url"]) if mod and hasattr(mod, "form_url") else ctx["url"]
    shot_rel = os.path.join("data", "queue", f"{w.slug}-{dt.datetime.now():%m%d%H%M}.png")
    reached = False
    with B.session() as br:
        page = br.new_page()
        log(f"  [explore] {ctx['company']} — {ctx['role']} ({w.pid})")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        B.wait_quiet(page.main_frame, max_s=8)
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
