#!/usr/bin/env python3
"""
draft.py — the answers he taps on the phone: 2-3 complete, true answers for every
question an exploration could not answer from his stored facts (the
draft_answers agent, src/agents/draft_answers). Runs after exploration, reading
the resume as tailored for this job.

    draft_for(slug, cli)            the newest queue item of an application: draft
                                    its open questions; the item is `preparing`
                                    meanwhile, so the review page never shows it half-ready
    draft_questions(slug, qfile)    one drafting session over one questions file
"""
import glob
import json
import os
import subprocess

from jobpilot.core.paths import APPLICATIONS, CONFIG, DATA, TRACKING


def newest_queue_file(slug):
    fs = sorted(glob.glob(os.path.join(DATA, "queue", f"{slug}-*.json")))
    return fs[-1] if fs else None


def set_status(qfile, status):
    try:
        d = json.load(open(qfile))
        d["status"] = status
        json.dump(d, open(qfile, "w"), indent=2)
    except (OSError, ValueError):
        pass


def open_question_count(qfile):
    try:
        d = json.load(open(qfile))
    except (OSError, ValueError):
        return 0
    return len([q for q in d.get("questions", []) if not q.get("options")])


def draft_questions(slug, qfile, cli):
    """One `claude -p` session: tappable answers for every question without options."""
    from jobpilot.core import agents
    from jobpilot.tailor.autotailor import policy_sections, jd_text, run, AUTO_CWD
    ag = agents.get("draft_answers")
    appdir = os.path.join(APPLICATIONS, slug)
    # the resume as tailored for this job (ats.md is built from the .tex), else the .tex itself
    resume = next((p for p in (os.path.join(appdir, "ats.md"), os.path.join(appdir, "resume.tex"))
                   if os.path.isfile(p)), os.path.join(appdir, "sections"))
    kw = dict(appdir=appdir, qfile=os.path.abspath(qfile), resume=resume,
              answers_file=os.path.join(CONFIG, "answers.yaml"),
              policy=policy_sections([1, 5, 6, 8]), jd=jd_text(slug), tracking=TRACKING)
    try:
        ok, out = run(ag.argv(cli, ag.render(**kw), **kw), timeout=ag.timeout(600), cwd=AUTO_CWD)
        return ok, out[-300:]
    except subprocess.TimeoutExpired:
        return False, "question drafting timed out"


def draft_for(slug, cli, log=print):
    """Draft the open questions of the application's newest queue item, if any.
    A question with no drafted answers makes him type on a phone, which is what
    this system exists to avoid (POLICY.md section 5)."""
    qfile = newest_queue_file(slug)
    if not (qfile and open_question_count(qfile)):
        return None
    try:
        before = json.load(open(qfile)).get("status") or "needs_input"
    except (OSError, ValueError):
        before = "needs_input"
    set_status(qfile, "preparing")
    try:
        ok, out = draft_questions(slug, qfile, cli)
        log(f"questions: {'drafted' if ok else 'FAILED — ' + out[-100:]}")
        return ok
    finally:
        set_status(qfile, before)           # a failed exploration stays failed
