"""explore_agentic/replay.py — file an application from its record (calls.json): redo it with
no Claude while the pages are as they were, and let the agent take over only where they are not.

    1 redo      calls.Redo.forward, by code: page after page from the posting, each page's
                recorded steps (a placeholder takes the applicant's approved answer), a check
                that every field holds what it held, the page's Next — to the record's last page
    2 resume    where the redo stops (a page differs, a page the record does not know, a Next
                that does not move on): the agent, with every tool and the replay tool
                (resume.md) — it fixes the page, calls replay to go on, and finishes on the
                last page
    3 submit    only with --submit, a queue item that is approved and never pressed before,
                every placeholder answered, none set while filing: code presses the button the
                record names (form.submit — its checks, and the watch of what the portal does)
    4 fix       NOT ACCEPTED (errors on the form, or sent back to an earlier page): the agent,
                with the submit tool (filing.md), fixes what the page says and submits again
                itself — at most 3 presses in all; SUBMITTED only on the portal's confirmation;
                REFUSED or UNCLEAR are never pressed again

    python -m jobpilot.apply.explore_agentic.replay <slug> [--submit [--item=<queue id>]] [--model= --effort=]

Without --submit it is a dry run: it goes to the last page and presses nothing there. With it,
the item is the slug's newest approved queue item unless --item names one.
Written: calls.json (saved after every call: a filing stopped anywhere resumes from it),
applications/<slug>/filing_agentic.json (what happened, each press), tests/maps/<slug>/replay-<time>.log.
"""
import asyncio
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time

from jobpilot.core.answers import load, load_learned
from jobpilot.core.paths import TOOL
from jobpilot.apply.explore import browser as B, record as R
from jobpilot.apply.explore_agentic.form import Form
from jobpilot.apply.explore_agentic import calls as C, session as SS


def queue_item(slug, item_id=None):
    """The queue item to file: the one named, else the slug's newest approved one."""
    if item_id:
        return json.load(open(os.path.join(B.QUEUE_DIR, item_id + ".json")))
    items = []
    for name in sorted(os.listdir(B.QUEUE_DIR)):
        if name.endswith(".json"):
            it = json.load(open(os.path.join(B.QUEUE_DIR, name)))
            if it.get("company_slug") == slug and it.get("status") == "approved":
                items.append(it)
    if not items:
        sys.exit(f"{slug}: no approved queue item — nothing opened, nothing sent")
    return max(items, key=lambda it: it.get("created") or it["id"])


def first_press(form, rec):
    """The first press, by code: the button the record names (form.submit checks and watches).
    What it reported."""
    form.read()
    c = form.find(rec["submit_control"])
    if c is None:
        return f"NOT ACCEPTED: the submit button the record names ({rec['submit_control']['name']!r}) is not on " \
               "the page. see the page and find it."
    return form.submit(c.id)


def resume_task(report, submit):
    return ("You are FILING an application the applicant approved, from the record of its exploration. The "
            f"record was redone by code and stopped:\n{report}\n\nCarry on from here (see RESUMING): fix the page, "
            "call replay to go on with the record, and on the last page call finish('last-page', note, submit_id)"
            + (" — do not submit: Submit is pressed next, by code." if submit else "."))


def fix_task(said):
    return ("You are FILING the application the applicant approved (see FILING). Its record was redone and "
            f"Submit was pressed; the portal did not accept it:\n{said}\n\nsee the page, fix what it says, and "
            "submit again. End with finish.")


async def file(slug, submit=False, item_id=None, model="opus", effort="low"):
    stamp = dt.datetime.now().strftime("%m%d-%H%M")
    base = os.path.join(TOOL, "tests", "maps", slug, f"replay-{stamp}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    logf = open(base + ".log", "w", encoding="utf-8")
    t0 = time.time()

    def log(s):
        line = f"[{time.time() - t0:5.0f}s] {s}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    rec, calls = C.load(slug)
    if not calls:
        sys.exit(f"{slug}: no record (calls.json) — explore it first")
    item = None
    if submit:
        item = queue_item(slug, item_id)
        if item.get("status") != "approved" or item.get("submitted_at"):
            sys.exit(f"{item['id']} is not approved ({item.get('status')}) — nothing opened, nothing sent")
        if item.get("submit_presses"):
            sys.exit(f"{item['id']}: Submit was pressed before ({item['submit_presses']}) — check the email for a "
                     "confirmation; nothing opened, nothing sent")
    approved = C.approved_answers(slug, rec)
    open_qs = [q for q in (rec.get("placeholders") or {}) if R.norm(q) not in approved]
    if submit and open_qs:
        sys.exit(f"{len(open_qs)} question(s) still without his answer: {open_qs[:4]} — nothing opened, nothing sent")
    if submit and not rec.get("submit_control"):
        sys.exit("the record names no submit button — explore it again")

    form = Form(slug, load("answers.yaml"), load_learned(), log, mode="submit" if submit else "dry-run")
    form.item, form.submit_control = item, rec.get("submit_control")
    form.redo = redo = C.Redo(form, rec, calls, approved, not submit, log)
    form.save_calls = lambda cs: C.save(slug, cs, calls, finished=False)
    pool = cf.ThreadPoolExecutor(1)
    on_browser = lambda fn, *a: asyncio.get_running_loop().run_in_executor(pool, fn, *a)
    out = {"slug": slug, "mode": "submit" if submit else "dry-run", "outcome": None, "why": None}
    closed = await on_browser(form.open)
    try:
        if closed:
            out["outcome"], out["why"] = "expired", closed
            return out
        log(f"filing {slug}: {sum(map(len, redo.pages.values()))} recorded calls over {len(redo.pages)} pages; "
            f"{'SUBMIT' if submit else 'dry run'}; answers {len(approved)}")
        r = await on_browser(redo.forward)
        form.save_calls(form.calls)
        out["redo"] = r
        log(f"  redo: {r['redone']} -> '{r['at']}': {r['why']}" + (f" {r['differs'][:6]}" if r["differs"] else ""))
        reached = r["end"]
        if not reached:                                   # the agent resumes from where the redo stopped
            out["agent"] = "resume"
            await SS.agent(form, on_browser, resume_task(redo.text(r), submit), model, effort, 120, log, logf)
            reached = (form.done or ("",))[0] == "last-page"
            if not reached and not form.sent:
                out["outcome"], out["why"] = "stuck", (form.done or ("", "the agent did not reach the last page"))[1]
        stops = list(form.placeholders) + (redo.unanswered if submit else [])
        if reached and stops:
            out["outcome"], out["why"] = "needs-answers", f"asked something his answers do not cover: {stops[:4]}"
        elif reached and not submit:
            out["outcome"] = "last-page"
        elif reached and not form.sent:
            said = await on_browser(first_press, form, rec)
            log(f"  submit: {said[:300]}")
            if not form.sent and not said.startswith("refused"):
                form.done = None
                out["agent"] = "fix"
                await SS.agent(form, on_browser, fix_task(said), model, effort, 80, log, logf)
            if not form.sent:
                done = form.done or ("stuck", said)
                out["outcome"] = "needs-answers" if done[0] == "needs-answer" else "not-sent"
                out["why"] = done[1]
        if form.sent:
            out["outcome"], out["why"] = form.sent
        shot = os.path.join(TOOL, "data", "queue", f"{slug}-{'submitted' if submit else 'replay'}-{stamp}.png")
        try:
            await on_browser(lambda: form.page.screenshot(path=shot, full_page=True))
            out["screenshot"] = os.path.relpath(shot, TOOL)
        except Exception:
            pass
    finally:
        await on_browser(form.close)
        pool.shutdown()
        out["seconds"] = round(time.time() - t0)
        out["new_placeholders"] = form.placeholders
        out["presses"] = form.presses
        C.save(slug, form.calls, calls, finished=out["outcome"] == "submitted")
        with open(os.path.join(os.path.dirname(R.path_for(slug)), "filing_agentic.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
        if item is not None:
            now = dt.datetime.now().isoformat(timespec="seconds")
            if out["outcome"] == "submitted":
                item.update(status="submitted", submitted_at=now, fail_reason=None, submit_screenshot=out.get("screenshot"))
            elif out["outcome"] == "unclear":             # maybe sent: never pressed again by a rerun
                item.update(status="unconfirmed", fail_reason=out["why"], submit_screenshot=out.get("screenshot"))
            elif out["outcome"] == "needs-answers":
                item.update(status="needs_input", fail_reason=out["why"])
            else:
                item.update(status="failed", fail_reason=(out["why"] or out["outcome"] or "unknown")[:300])
            with open(os.path.join(B.QUEUE_DIR, item["id"] + ".json"), "w") as f:
                json.dump(item, f, indent=2)
        log(f"\nFILING: {out['outcome']}" + (f" — {out['why']}" if out.get("why") else "")
            + f" | {out['seconds']}s | agent: {out.get('agent') or 'not needed'} | presses {len(form.presses)}")
        logf.close()
    return out


def main():
    slug = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    if not slug:
        sys.exit(__doc__)
    flag = lambda k, d=None: next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{k}=")), d)
    asyncio.run(file(slug, "--submit" in sys.argv, flag("item"), flag("model", "opus"), flag("effort", "low")))


if __name__ == "__main__":
    main()
