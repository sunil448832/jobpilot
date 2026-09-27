#!/usr/bin/env python3
"""
facts.py — the applicant's facts for ONE job, by key: what the map (mapper.py)
may point a form's question at. A key, never a value, goes through Claude; the
value is looked up here when the code acts.

    answers.yaml, flattened     personal.first_name, work_authorization.eu.requires_sponsorship, ...
    job.company / job.location / job.market
                                the job itself, so the map picks the right row
                                (a Germany job -> the eu row) — no rules in code
    learned:<n>                 an answer he gave on an earlier form (learned.yaml)
    file:resume                 the resume file built for this application

Salary rows of other markets are left out: only this market's asking figure applies.
"""
import re

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


def job_facts(answers, ctx, learned=None, resume=None):
    """{key: value} for one job — everything a form on it may be answered from."""
    market = ctx.get("market") or "default"
    by_market = (answers.get("compensation") or {}).get("by_market") or {}
    own = f"compensation.by_market.{market}." if market in by_market else "compensation.default."
    facts = {"job.company": ctx.get("company", ""), "job.location": ctx.get("location", ""),
             "job.market": market}
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
    for i, e in enumerate(learned or []):
        if isinstance(e, dict) and str(e.get("answer") or "").strip():
            facts[f"learned:{i}"] = str(e["answer"]).strip()
    if resume:
        facts["file:resume"] = resume
    return facts


def pay_text(answers, market):
    """This market's asking salary, as the review page shows it."""
    comp = answers.get("compensation") or {}
    return ((comp.get("by_market") or {}).get(market) or comp.get("default") or {}).get("expected_text", "")


def keys_for_prompt(facts, learned=None):
    """Only the keys of the answers file and the job — for a correction round, where
    each error already quotes the value in question. Learned answers are left out:
    a correction fixes a kind, a name or a choice, not which answer applies."""
    return "\n".join(k for k in facts if not k.startswith("learned:"))


def for_prompt(facts, learned=None):
    """The facts as Claude sees them: one line each; a learned answer shows the
    question it answered; the resume is named, not its path."""
    lines = []
    for k, v in facts.items():
        if k == "file:resume":
            lines.append("file:resume: (the resume file)")
        elif k.startswith("learned:") and learned:
            q = (learned[int(k.split(":")[1])] or {}).get("match", "")
            lines.append(f"{k}: answer to {q[:80]!r}: {v[:40]}")
        else:
            lines.append(f"{k}: {v[:160]}")
    return "\n".join(lines)
