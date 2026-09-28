#!/usr/bin/env python3
"""
submit/replay.py — PASS 2: file an approved application. The same walk as exploration
(see -> map -> act -> see, page by page, explore/walk.py), with the applicant's approved
answers known, then Submit on the last page.

    answers      each question he answered on the phone goes to the map as an answer of
                 his ("answer to <question>: <his answer>"), the way a learned answer
                 does — so the map picks it; nothing is decided by code
    a draft      a portal that kept the explored draft (Workday) shows most controls
                 filled: the map answers keep: and nothing is done to them
    safety       Submit is pressed only when the walk reached the last page, no control
                 was left unfilled, and no placeholder was set while filing — a question
                 the map could not answer from his answers (new on the form, or asked
                 another way) sends the card back to the phone, nothing sent
    success      a positive confirmation from the portal; a refusal or an error on the
                 page is a failure, never a submission
    dry run      the walk to the last page, nothing pressed there, the card left as it is:
                 a screenshot of the last page and what would have stopped the submit

The filing's own record: applications/<slug>/filing.json (explore.json stays as approved).

    python -m jobpilot.apply.submit.replay [QUEUE_ID] [--limit N] [--dry-run] [--model M --effort E]
        QUEUE_ID             one queue item (default: every approved one, oldest first)
        --limit N            file at most N now
        --dry-run QUEUE_ID   walk that application to its last page and press nothing there;
                             the card is left as it is (any card, approved or not)
        --model / --effort   the map's model and effort for this run (default: config llm.map)
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

from jobpilot.core.config import cfg
from jobpilot.core.paths import TOOL, DATA
from jobpilot.apply.explore import browser as B, record as R, see as S, act as A
from jobpilot.apply.explore.walk import Walk, questions_from

CONFIRMED_RX = re.compile(r"thank you for (applying|your application)|application (has been )?(submitted|received)|"
                          r"successfully submitted|we('ve| have) received your application|congratulations|"
                          r"your application (was|has been) (successfully )?(submitted|sent|received)", re.I)
REFUSED_RX = re.compile(r"(couldn.t|could not|cannot|can.t|unable to) (submit|process) (your )?application|"
                        r"may not apply more than|application limit|already applied|you have already applied", re.I)


def _save(it):
    with open(os.path.join(B.QUEUE_DIR, f"{it['id']}.json"), "w") as f:
        json.dump(it, f, indent=2)


def ctx_of(it, model=None, effort=None):
    return {"market": it["market"], "company": it["company"], "company_slug": it["company_slug"],
            "role": it["role"], "portal": it["portal"], "location": it.get("location", ""), "url": it["url"],
            "map_model": model, "map_effort": effort}


class Filing(Walk):
    """The explore walk, with his approved answers known to the map."""

    def __init__(self, it, answers, learned, log=print, model=None, effort=None):
        approved = {q: p["answer"] for q, p in (R.load(it["company_slug"]).get("placeholders") or {}).items()
                    if (p.get("answer") or "").strip()}
        # his answers reach the map as learned answers do: "answer to <question>: <answer>"
        learned = list(learned or []) + [{"match": q, "answer": a} for q, a in approved.items()]
        super().__init__(ctx_of(it, model, effort), answers, learned, log)
        self.it, self.approved = it, approved
        self.last_buttons = {}

    def do_page(self, frame, step, last_act="(none)", chrome=False):
        self.last_buttons = super().do_page(frame, step, last_act, chrome)
        return self.last_buttons

    def stops(self):
        """What must stop the Submit: controls left unfilled, placeholders set while filing."""
        out = [f"'{step}': {c} could not be filled" for step, cs in self.unfilled.items() for c in cs]
        out += [f"'{p.get('page')}': {q!r} has no answer of yours — filled with {p.get('value')!r} for now"
                for q, p in (self.record.get("placeholders") or {}).items()]
        return out

    def press_submit(self, page):
        """Press the submit button the last page's map marked, then read the outcome:
        (submitted, why)."""
        btn = self.last_buttons.get("submit")
        if btn is None:
            return False, "the map marked no submit button on the last page"
        frame = self.frame(page)
        S.tap(A.locate(frame, btn))
        self.log(f"    pressed {btn.name!r}")
        page.wait_for_timeout(2500)
        if B.enter_verification_code(frame, self.it, lambda: A.locate(self.frame(page), btn).click(timeout=A.WAIT)):
            page.wait_for_timeout(2500)
        for _ in range(int(cfg("browser.submit_poll_s", 30))):
            text = " ".join(S.body_text(f, 4000) for f in page.frames)
            m = REFUSED_RX.search(text)
            if m:
                return False, "the portal refused it: " + text[max(0, m.start() - 60):m.end() + 140].strip()
            if CONFIRMED_RX.search(text):
                return True, None
            page.wait_for_timeout(1000)
        errs = S.errors(self.frame(page))
        return False, ("the portal shows: " + "; ".join(errs[:4])) if errs else "no confirmation from the portal"


def _walk(it, answers, learned, log, model, effort, press):
    """Open the posting and walk it; with `press`, Submit when nothing stops it.
    (w, outcome) — outcome: submitted / expired / not-last-page / stopped / dry-run / failed."""
    w = Filing(it, answers, learned, log, model, effort)
    url = w.mod.form_url(it["url"]) if w.mod and hasattr(w.mod, "form_url") else it["url"]
    tag = "submitted" if press else "dryrun"
    w.outcome, w.why = "failed", None
    with B.session() as br:
        page = br.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            B.wait_quiet(page.main_frame, max_s=8)
            gone = B.dead_posting(page)
            if gone:
                w.outcome, w.why = "expired", f"posting is closed ({gone!r})"
                return w
            if not w.run(page):
                w.outcome, w.why = "not-last-page", (w.warnings[-1] if w.warnings else "the walk stopped")
            elif w.stops():
                w.outcome, w.why = "stopped", "; ".join(w.stops())[:400]
            elif not press:
                w.outcome = "dry-run"
            else:
                ok, why = w.press_submit(page)
                w.outcome, w.why = ("submitted" if ok else "failed"), why
        except Exception as e:
            w.outcome, w.why = "failed", f"submit error: {type(e).__name__}: {str(e)[:200]}"
        finally:
            shot = os.path.join(DATA, "queue", f"{it['id']}-{tag}.png")
            try:
                page.screenshot(path=shot, full_page=True)
                w.shot = os.path.relpath(shot, TOOL)
            except Exception:
                w.shot = None
            w.record["filed_at"] = dt.datetime.now().isoformat(timespec="seconds")
            w.record["outcome"], w.record["why"] = w.outcome, w.why
            with open(os.path.join(os.path.dirname(R.path_for(w.slug)), "filing.json"), "w", encoding="utf-8") as f:
                json.dump(w.record, f, indent=1, ensure_ascii=False)
    return w


def dry_run(it, answers, learned, log=print, model=None, effort=None):
    """Walk the application to its last page and press nothing there. The card is left as it
    is. Returns the walk (outcome, why, shot)."""
    rec = R.load(it["company_slug"])
    log(f"  [dry run] {it['id']} — {it['company']} / {it['role']}"
        f" ({len(R.unanswered(rec))} question(s) still without your answer)")
    t0, plain = time.time(), log
    w = _walk(it, answers, learned, lambda s, **kw: plain(f"[{time.time() - t0:5.0f}s]{s}", **kw), model, effort, press=False)
    log(f"\n  [dry run] {w.outcome}" + (f": {w.why}" if w.why else "") + f" — screenshot {w.shot}")
    for s in w.stops():
        log(f"    would stop the submit: {s}")
    return w


def submit_one(it, answers, learned, log=print, model=None, effort=None):
    """File one approved item. The card is read again first — an Undo on the phone since the
    list was read wins — and is `submitting` while it is filed, so it cannot be undone half
    way. Updates and saves the item; returns it."""
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
        return _submit_one(it, answers, learned, log, model, effort)
    finally:
        if it.get("status") == "submitting":         # crashed before an outcome: approved again
            it["status"] = "approved"
            _save(it)


def _submit_one(it, answers, learned, log, model, effort):
    log(f"  [submit] {it['id']} — {it['company']} / {it['role']}")
    rec = R.load(it["company_slug"])
    if it.get("reached_end") is False or not rec.get("pages"):
        it["status"], it["fail_reason"] = "failed", "its exploration never reached the last page — use Re-explore"
        _save(it)
        return it
    missing = R.unanswered(rec)
    if missing:
        it.update(questions=questions_from(rec), status="needs_input",
                  fail_reason=f"{len(missing)} question(s) still without your answer — nothing opened, nothing sent")
        _save(it)
        B.notify_outcome(it)
        return it
    w = _walk(it, answers, learned, log, model, effort, press=True)
    now = dt.datetime.now().isoformat(timespec="seconds")
    if w.shot:
        it["submit_screenshot"] = w.shot
    if w.outcome == "submitted":
        it.update(status="submitted", submitted_at=now, fail_reason=None)
    elif w.outcome == "expired":
        it.update(status="expired", fail_reason=w.why)
    elif w.outcome == "stopped":
        # asked something his answers do not cover: back to the phone, nothing sent
        new = {q: p for q, p in w.record["placeholders"].items()}
        doc = R.load(w.slug)
        doc.setdefault("placeholders", {}).update({q: p for q, p in new.items() if q not in doc["placeholders"]})
        R.save(w.slug, doc)
        it.update(status="needs_input", questions=questions_from(doc),
                  fail_reason="the form asked something your answers do not cover — answer it, then Approve. " + (w.why or ""))
    else:
        it["attempts"] = (it.get("attempts") or 0) + 1
        it["last_attempt_at"] = now
        code_wait = any("verification code not received" in x for x in it.get("warnings", []))
        it["status"] = "approved" if code_wait and it["attempts"] < cfg("pipeline.submit_attempts", 3) else "failed"
        it["fail_reason"] = (w.why or "unknown")[:300]
    it.setdefault("warnings", []).extend(w.warnings)
    _save(it)
    R.note_attempt(w.slug, it["status"], why=w.why)
    log(f"    -> {it['status']}" + (f": {w.why}" if w.why and w.outcome != "submitted" else ""))
    B.notify_outcome(it)
    if w.outcome == "submitted":
        # an application in an ATS queue is the weakest form of applying: line up referrals now
        try:
            subprocess.run([sys.executable, "-m", "jobpilot.outreach.referral_tracker", "--for", it["company_slug"]],
                           cwd=TOOL, timeout=300)
        except Exception as e:
            log(f"    [warn] referral targets: {type(e).__name__}")
    return it


def load_item(item_id):
    return json.load(open(os.path.join(B.QUEUE_DIR, item_id + ".json")))


def submit_approved(answers, learned, one=None, limit=None, log=print, model=None, effort=None):
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
        submit_one(it, answers, learned, log, model, effort)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", nargs="?", help="one queue item (default: every approved one)")
    ap.add_argument("--limit", type=int, metavar="N", help="file at most N now (oldest approved first)")
    ap.add_argument("--dry-run", action="store_true", help="walk to the last page, press nothing, change nothing")
    ap.add_argument("--model", help="the map's model for this run")
    ap.add_argument("--effort", help="the map's effort for this run")
    a = ap.parse_args()
    from jobpilot.core.answers import load, load_learned, preflight
    answers = load("answers.yaml")
    preflight(answers)
    if a.dry_run:
        if not a.id:
            sys.exit("--dry-run needs a queue id")
        dry_run(load_item(a.id), answers, load_learned(), model=a.model, effort=a.effort)
        return
    submit_approved(answers, load_learned(), one=a.id, limit=a.limit, model=a.model, effort=a.effort)


if __name__ == "__main__":
    main()
