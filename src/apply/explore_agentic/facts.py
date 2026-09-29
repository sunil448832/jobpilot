#!/usr/bin/env python3
"""
facts.py — the applicant's facts for ONE job, by key: what the form agent (session.py)
may point a form's question at. A key, never a value, goes through Claude; the
value is looked up here when the code acts.

    answers.yaml, flattened     personal.first_name, work_authorization.outside_india.requires_sponsorship, ...
    job.company / job.location / job.market
                                the job itself, so the agent picks the right row
                                (a Germany job -> the eu row) — no rules in code
    job.today (.year .month .day)   the day the form is filled or filed
    tenant:<stored fact key>    this portal's entry for a stored fact its list does not hold as
                                written (applications/_tenants/<tenant>.yaml entries): his pick,
                                reused on every job here
    employer:<key>              the entry he picked from this portal's list on an earlier job
                                (the same file's answers) — reused here only
    learned:<key>               his answer for this very form, approved on the phone
                                — a <key> comes from the question's words (answers.question_key),
                                so a recorded key keeps meaning the same answer
    file:resume                 the resume file built for this application (the PDF)
    file:resume_docx            the same resume as a .docx, for a second attachment field
    (file:resume_pdf            a record's second attachment field, from before 2026-09-29: it
                                now takes the .docx (act.read_answer); not shown to the agent)

Salary rows of other markets are left out: only this market's asking figure applies.
"""
import datetime as dt
import re

from jobpilot.core.answers import question_key

SKIP = re.compile(r"^(portal_routing\.|files\.)|(password|token|secret|api_?key)", re.I)
# a value that stands for "not written yet" is not a fact: its question gets a
# placeholder and a drafted answer (questions.why_this_company is "PER_COMPANY")
NOT_A_VALUE = re.compile(r"^(PER_COMPANY|PER_ROLE|TODO|TBD|FIXME|N/?A)$", re.I)


def flatten(d, prefix=""):
    """A nested dict as {dotted.key: text}: scalars, and lists of scalars joined."""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(d, list):
        if all(not isinstance(x, (dict, list)) for x in d):
            out[prefix] = ", ".join(str(x) for x in d)
        else:
            for i, x in enumerate(d):
                out.update(flatten(x, f"{prefix}[{i}]"))
    elif isinstance(d, bool):
        out[prefix] = "Yes" if d else "No"
    elif d is not None and str(d).strip():
        out[prefix] = str(d).strip()
    return out


def job_facts(answers, ctx, learned=None, resume=None, tenant=None, resume_pdf=None, resume_docx=None):
    """{key: value} for one job — everything a form on it may be answered from."""
    market = ctx.get("market") or "default"
    by_market = (answers.get("compensation") or {}).get("by_market") or {}
    own = f"compensation.by_market.{market}." if market in by_market else "compensation.default."
    facts = {"job.company": ctx.get("company", ""), "job.location": ctx.get("location", ""),
             "job.market": market}
    today = dt.date.today()                      # "today's date" on a form: the day it is filled or filed
    facts.update({"job.today": today.isoformat(), "job.today.year": str(today.year),
                  "job.today.month": f"{today.month:02d}", "job.today.day": f"{today.day:02d}"})
    for k, v in flatten(answers).items():
        if SKIP.search(k) or NOT_A_VALUE.match(v):
            continue
        if k.startswith(("compensation.by_market.", "compensation.default.")) and not k.startswith(own):
            continue
        facts[k] = v
        # a date is also offered in parts: forms often ask Month and Year separately
        m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$", v)
        if m:
            facts[k + ".year"], facts[k + ".month"] = m.group(1), m.group(2)
            if m.group(3):
                facts[k + ".day"] = m.group(3)
    tenant = tenant or {}
    for k, e in (tenant.get("entries") or {}).items():
        if isinstance(e, dict) and str(e.get("entry") or "").strip():
            facts[f"tenant:{k}"] = str(e["entry"]).strip()
    for kind, rows in (("employer", tenant.get("answers")), ("learned", learned)):
        for e in rows or []:
            if isinstance(e, dict) and str(e.get("answer") or "").strip():
                facts[f"{kind}:{question_key(e.get('match', ''))}"] = str(e["answer"]).strip()
    if resume:
        facts["file:resume"] = resume
    if resume_docx:
        facts["file:resume_docx"] = resume_docx
    if resume_pdf:
        facts["file:resume_pdf"] = resume_pdf
    return facts


def pay_text(answers, market):
    """This market's asking salary, as the review page shows it."""
    comp = answers.get("compensation") or {}
    return ((comp.get("by_market") or {}).get(market) or comp.get("default") or {}).get("expected_text", "")


def questions(*lists):
    """{"learned:<key>" and "employer:<key>": the question it answers}, for the prompt."""
    out = {}
    for kind, rows in lists:
        for e in rows or []:
            if isinstance(e, dict):
                out[f"{kind}:{question_key(e.get('match', ''))}"] = e.get("match", "")
    return out


def for_prompt(facts, asked=None, tenant=""):
    """The facts as Claude sees them, in groups: the stored facts (answers.yaml and the job),
    which always win; this portal's entries for stored facts its lists do not hold; this
    portal's learned answers; his answers for this form — each answer with its question
    (asked: questions()). The resume is named, not its path."""
    asked = asked or {}
    stored, portal, employer, got = [], [], [], []
    for k, v in facts.items():
        if k == "file:resume":
            stored.append("file:resume: (the resume file, PDF)")
        elif k == "file:resume_docx":
            stored.append("file:resume_docx: (the same resume as a .docx)")
        elif k == "file:resume_pdf":
            continue                                     # a key older records name; not offered
        elif k.startswith("tenant:"):
            own = facts.get(k[len("tenant:"):], "")
            portal.append(f"{k}: {v[:120]}" + (f"   (stands for the stored {own[:60]!r})" if own else ""))
        elif k.startswith(("learned:", "employer:")):
            q = asked.get(k, "")
            (employer if k.startswith("employer:") else got).append(
                f"{k}: answer to {q[:80]!r}: {v[:40]}" if q else f"{k}: {v[:40]}")
        else:
            stored.append(f"{k}: {v[:160]}")
    return ("STORED FACTS — his profile: always true, never replaced by anything below\n" + "\n".join(stored)
            + (f"\n\nTHIS PORTAL'S ENTRIES ({tenant}) — the entry this portal's list holds for a stored fact it "
               "does not hold as written; his pick, reused on every job here\n" + "\n".join(portal) if portal else "")
            + (f"\n\nTHIS EMPLOYER'S ANSWERS ({tenant}) — what he answered on earlier jobs of this portal, "
               "to questions the stored facts do not cover\n" + "\n".join(employer) if employer else "")
            + ("\n\nHIS ANSWERS FOR THIS FORM — approved on the phone\n" + "\n".join(got) if got else ""))
