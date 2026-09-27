#!/usr/bin/env python3
"""
submit/replay.py — PASS 2: file an approved application by replaying its recorded
actions (applications/<slug>/explore.json) with the true values. Code only.

    value of an action    fact:<key>      the fact, looked up again for this job
                          placeholder:<q> his approved answer to q — the submit
                                          does not open while one is unanswered
                          shown           the value the page itself held (a resume parse)
    a page that no longer fits (an action fails, or the page refuses Next): that
    page only is re-mapped — the same see / map / act as exploration — and the
    replay goes on. A question that appears only now has no approved answer: the
    item goes back to the phone.
    success               a positive confirmation from the portal. A refusal
                          (OpenAI: "We couldn't submit your application…") or an
                          error on the page is a failure, never a submission.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL, DATA
from jobpilot.apply.explore import browser as B, record as R
from jobpilot.apply.explore import act as A, see as S
from jobpilot.apply.explore.walk import Walk, questions_from

CONFIRMED_RX = re.compile(r"thank you for (applying|your application)|application (has been )?(submitted|received)|"
                          r"successfully submitted|we('ve| have) received your application|congratulations|"
                          r"your application (was|has been) (successfully )?(submitted|sent|received)", re.I)
REFUSED_RX = re.compile(r"(couldn.t|could not|cannot|can.t|unable to) (submit|process) (your )?application|"
                        r"may not apply more than|application limit|already applied|you have already applied", re.I)


def _save(it):
    with open(os.path.join(B.QUEUE_DIR, f"{it['id']}.json"), "w") as f:
        json.dump(it, f, indent=2)


def ctx_of(it):
    return {"market": it["market"], "company": it["company"], "company_slug": it["company_slug"],
            "role": it["role"], "portal": it["portal"], "location": it.get("location", ""), "url": it["url"]}


class Replay(Walk):
    """A Walk that replays the record instead of mapping from scratch."""

    def __init__(self, it, answers, learned, log=print):
        super().__init__(ctx_of(it), answers, learned, log, stage="submit")
        self.it = it
        old = R.load(self.slug)
        self.record = old                        # the same record: a re-mapped page is written back into it
        self.record.setdefault("placeholders", {})
        self.answered = {q: p["answer"] for q, p in self.record["placeholders"].items() if p.get("answer")}

    def value_for(self, a):
        src = a.get("source") or ""
        if src.startswith("placeholder:"):
            return self.answered.get(src.split(":", 1)[1])
        if src.startswith("fact:"):
            return self.facts.get(src[5:])
        return a.get("value")

    def replay_page(self, frame, step):
        """Every recorded action of this page, with the true values. Returns failures."""
        page_rec = next((p for p in self.record.get("pages", []) if p["step"] == step), None)
        if page_rec is None:
            return [(None, f"page '{step}' was never explored")]
        failures, done = [], []
        for a in page_rec["actions"]:
            v = self.value_for(a)
            kind, path = a["kind"], tuple(a.get("path") or ())
            if a["source"].startswith("placeholder:"):
                if v is None:
                    return [(None, f"'{a['source'][12:]}' has no approved answer")]
                if kind in ("search-and-pick", "dropdown", "native-select"):
                    hit = A.find_path(A.map_menu(frame, kind, a["name"]), v)
                    path = tuple(hit[:-1]) if hit else ()
            try:
                r = A.act(frame, kind, a["name"], v, path)
                ok = r.get("ok")
                err = None if ok else f"the value did not take (shows {r.get('shown')!r})"
            except Exception as ex:
                ok, err = False, f"{type(ex).__name__}: {str(ex).splitlines()[0][:120]}"
            self.log(f"      replay {kind:15} {a['name'][:48]!r:50} {'✓' if ok else '✗ ' + err}")
            if ok:
                done.append((kind, a["name"], v))
            if not ok:
                failures.append(({"name": a["name"], "kind": kind, "fact": (a["source"][5:] if a["source"].startswith("fact:") else None)}, err))
        A.readback(frame, done, self.log)
        return failures

    def run(self, page):
        """Replay to the last page. Returns (reached, problem)."""
        if not self.start(page):
            return False, "could not get from the posting to the form"
        last = None
        for n in range(1, int(cfg("browser.max_pages", 12)) + 1):
            frame = self.frame(page)
            step = self.step_name(page, frame, n)
            self.log(f"\n  page {n}: {step}")
            if self.mod and hasattr(self.mod, "is_last") and self.mod.is_last(step):
                return True, None
            if step == last:
                return False, f"'{step}' came back after Next"
            bad = self.replay_page(frame, step)
            if bad:
                before = set(R.unanswered(self.record))
                self.log(f"    {len(bad)} recorded action(s) no longer fit — re-mapping this page")
                cached = [{"name": x[0], "kind": x[1], "fact": x[2], "question": (x[3] if len(x) > 3 else None)} for x in
                          next((p["entries"] for p in self.record["pages"] if p["step"] == step), [])]
                self.do_page(frame, step, failures=[b for b in bad if b[0]], cached=cached)
                new = set(R.unanswered(self.record)) - before
                if new:
                    return False, "NEEDS_INPUT"
            ok, errs, is_last = self.next(page, frame, step)
            for _ in range(2):
                if ok or is_last or not errs:
                    break
                self.log(f"    the page refused: {errs[:3]} -> re-mapping")
                before = set(R.unanswered(self.record))
                self.do_page(frame, step, failures=[(None, f"Next refused the page: {e}") for e in errs[:6]])
                if set(R.unanswered(self.record)) - before:
                    return False, "NEEDS_INPUT"
                ok, errs, is_last = self.next(page, frame, step)
            if is_last:
                return True, None
            if not ok:
                return False, f"'{step}' would not save: {errs[:3]}"
            last = step
        return False, "too many pages"

    def press_submit(self, page):
        """Press Submit and read the outcome: (submitted, why)."""
        frame = self.frame(page)
        name = self.record.get("submit")
        try:
            A.locate(frame, "button", name or "")
        except A.NotFound:
            # not the recorded one: the page's map says which button submits
            step = (self.record.get("pages") or [{}])[-1].get("step") or "form"
            self.do_page(frame, step)
            name = self.button(step, "submit")
        if not name:
            return False, "the map found no Submit button on the last page"
        A.act(frame, "button", name)
        self.log(f"    pressed {name!r}")
        page.wait_for_timeout(2500)
        if B.enter_verification_code(frame, self.it, name):
            page.wait_for_timeout(2500)
        for _ in range(int(cfg("browser.submit_poll_s", 30))):
            text = " ".join(S.body_text(f, 4000) for f in page.frames)
            if REFUSED_RX.search(text):
                m = REFUSED_RX.search(text)
                return False, "the portal refused it: " + text[max(0, m.start() - 60):m.end() + 140].strip()
            if CONFIRMED_RX.search(text):
                return True, None
            page.wait_for_timeout(1000)
        errs = S.errors(self.frame(page))
        return False, ("the portal shows: " + "; ".join(errs[:4])) if errs else "no confirmation from the portal"


def submit_one(it, answers, learned, log=print):
    """File one approved item. Updates and saves the item; returns it. The card is
    read again first — an Undo on the phone since the list was read wins — and is
    `submitting` while it is filed, so it cannot be undone half way."""
    path = os.path.join(B.QUEUE_DIR, it["id"] + ".json")
    try:
        cur = json.load(open(path))
        if cur.get("status") != "approved" or cur.get("submitted_at"):
            log(f"  [submit] {it['id']} — skipped: no longer approved ({cur.get('status')})")
            return cur
        it.update(cur)
    except (OSError, ValueError):
        pass
    it["status"] = "submitting"
    _save(it)
    try:
        return _submit_one(it, answers, learned, log)
    finally:
        if it.get("status") == "submitting":         # crashed before an outcome: approved again
            it["status"] = "approved"
            _save(it)


def _submit_one(it, answers, learned, log=print):
    log(f"  [submit] {it['id']} — {it['company']} / {it['role']}")
    rec = R.load(it["company_slug"])
    if it.get("reached_end") is False or not rec.get("pages"):
        it["status"], it["fail_reason"] = "failed", "its exploration never reached the last page — use Re-explore"
        _save(it)
        return it
    missing = R.unanswered(rec)
    if missing:
        it["questions"] = questions_from(rec)
        it["status"] = "needs_input"
        it["fail_reason"] = f"{len(missing)} question(s) still without your answer — nothing opened, nothing sent"
        _save(it)
        B.notify_outcome(it)
        return it
    w = Replay(it, answers, learned, log)
    url = w.mod.form_url(it["url"]) if w.mod and hasattr(w.mod, "form_url") else it["url"]
    ok, why = False, None
    with B.session() as br:
        try:
            page = br.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            gone = B.dead_posting(page)
            if gone:
                it.update(status="expired", fail_reason=f"posting is closed ({gone!r})")
                _save(it)
                B.notify_outcome(it)
                return it
            reached, why = w.run(page)
            if reached:
                ok, why = w.press_submit(page)
            shot = os.path.join(DATA, "queue", f"{it['id']}-submitted.png")
            try:
                page.screenshot(path=shot, full_page=True)
                it["submit_screenshot"] = os.path.relpath(shot, TOOL)
            except Exception:
                pass
        except Exception as e:
            why = f"submit error: {type(e).__name__}: {str(e)[:200]}"
    R.save(w.slug, w.record)                      # re-mapped pages and new placeholders, if any
    now = dt.datetime.now().isoformat(timespec="seconds")
    if ok:
        it.update(status="submitted", submitted_at=now, fail_reason=None)
    elif why == "NEEDS_INPUT":
        it.update(status="needs_input", questions=questions_from(w.record),
                  fail_reason="the form asked something new at submit — answer it, then Approve")
    else:
        it["attempts"] = (it.get("attempts") or 0) + 1
        it["last_attempt_at"] = now
        code_wait = any("verification code not received" in x for x in it.get("warnings", []))
        it["status"] = "approved" if code_wait and it["attempts"] < cfg("pipeline.submit_attempts", 3) else "failed"
        it["fail_reason"] = (why or "unknown")[:300]
    it.setdefault("warnings", []).extend(w.warnings)
    _save(it)
    R.note_attempt(w.slug, it["status"], why=why)
    log(f"    -> {it['status']}" + (f": {why}" if why and not ok else ""))
    B.notify_outcome(it)
    if ok:
        # an application in an ATS queue is the weakest form of applying: line up referrals now
        try:
            subprocess.run([sys.executable, "-m", "jobpilot.outreach.referral_tracker", "--for", it["company_slug"]],
                           cwd=TOOL, timeout=300)
        except Exception as e:
            log(f"    [warn] referral targets: {type(e).__name__}")
    return it


def submit_approved(answers, learned, one=None, limit=None, log=print):
    """Every approved item (or just `one`), oldest first; `limit` caps how many now."""
    items = []
    for fn in sorted(os.listdir(B.QUEUE_DIR)) if os.path.isdir(B.QUEUE_DIR) else []:
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        it = json.load(open(os.path.join(B.QUEUE_DIR, fn)))
        if (one and it.get("id") != one) or it.get("status") != "approved" or it.get("submitted_at"):
            continue
        items.append(it)
    if not items:
        log("  nothing approved to submit")
        return
    if limit is not None and limit < len(items):
        log(f"  {len(items)} approved; filing {limit} now, {len(items) - limit} stay approved")
        items = items[:limit]
    for it in items:
        submit_one(it, answers, learned, log)
