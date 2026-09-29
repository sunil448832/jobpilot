#!/usr/bin/env python3
"""
learn.py — keep the answers he approved on a review card for the employer portal he gave
them on, and nowhere else: applications/_tenants/<tenant>.yaml (workday:crowdstrike;
core/answers.py reads it for that portal's next jobs).

      answers:  a pick from the portal's own list, for a question the stored facts do not
                cover ("How did you hear about us?" -> Job Board › LinkedIn). Only picks: a
                written answer is never refused by a form, a pick the list does not hold is
      entries:  a pick that stood in for a stored fact its list cannot hold (no IIT Jodhpur;
                "stand_in_for", set by review/serve.py); the stored fact never changes

Never kept: a written answer (a text box: drafted again, or asked, when needed), an essay,
a question about his experience,
projects or motivation (drafted from his resume each time), and a question the stored facts
answer — relatives, affiliations and conflicts, sponsorship and work authorization,
citizenship (config/answers.yaml). There is no general store: what should hold for every
employer is moved into answers.yaml by hand, in the weekly review of these files.

Nothing stored is ever overwritten. A different answer for a stored question (or a different
pick for a stored entry) is a conflict: the stored one stays in use, and he is asked on the
questions page (review/ask.py pose; Telegram is told) — keep, new, or the answer to store.
resolve() applies his reply (review/serve.py).

Usage:
    python -m jobpilot.apply.draft.learn <submission.json> ...
"""
import argparse
import json
import os
import re
import sys

import yaml

from jobpilot.core.answers import load_tenant, tenant_path

ESSAY = 25                         # words: a longer answer was written for that one job
PROJECT = re.compile(r"experience|project|describe|walk (us|me) through|example of|tell (us|me) about|"
                     r"accomplish|proud|worked on|built|hands-on|familiar|proficien|comfortable with|"
                     r"techniques|skills?\b|why .*(interested|join|want|apply)|^why |excited|motivat|"
                     r"what makes you", re.I)
COVERED = re.compile(r"relative|related to|relationship with|family|spouse|partner of|affiliat|government|"
                     r"official|conflict of interest|outside (employment|business)|second job|donat|volunteer|"
                     r"sponsor|visa|work authori|authori[sz]ed to work|right to work|eligible to work|"
                     r"work permit|citizen", re.I)
HEAD = ("# ============================================================================\n"
        "# {title}\n"
        "# Grown by learn.py from his approvals. Nothing here is overwritten: a different\n"
        "# answer is put to him on the questions page first. The stored facts\n"
        "# (config/answers.yaml) always win over it. Edit freely — his own words, must stay true.\n"
        "# ============================================================================\n\n")


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[*∗]", "", s or "")).strip().lower()


# ------------------------------------------------------------------ the portal files

def kept(label, answer, choice=True):
    """None when this answer is kept, else why it is not."""
    if not choice:
        return "written, not a pick"
    if len(str(answer).split()) > ESSAY:
        return "essay"
    if PROJECT.search(label or ""):
        return "experience / projects / motivation"
    if COVERED.search(label or ""):
        return "a stored fact answers it"
    return None


def save_tenant(t):
    """applications/_tenants/<tenant>.yaml; a tenant left with nothing loses its file."""
    p = tenant_path(t["tenant"])
    if not t["answers"] and not t["entries"]:
        if os.path.isfile(p):
            os.remove(p)
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(HEAD.format(title=f"{t['tenant']} — his answers for this employer's portal only"))
        yaml.safe_dump({"tenant": t["tenant"], "company": t.get("company"), "answers": t["answers"],
                        "entries": t["entries"]}, f, sort_keys=False, allow_unicode=True, width=88)


# ------------------------------------------------------------------ merging

def merge(rows, label, answer):
    """added / unchanged / conflict (a different answer is stored: it stays)."""
    label_n = norm(label)
    for e in rows:
        if norm(e.get("match", "")) == label_n:
            return "unchanged" if e.get("answer") == answer else "conflict"
    rows.append({"match": label_n[:200], "answer": answer})
    return "added"


def merge_entry(t, fact, entry):
    """added / unchanged / conflict, for this portal's entry standing for a stored fact."""
    from jobpilot.apply.explore_agentic import facts as F
    from jobpilot.core.answers import load as load_answers
    if fact in t["entries"]:
        return "unchanged" if t["entries"][fact].get("entry") == entry else "conflict"
    t["entries"][fact] = {"entry": entry, "stored": F.flatten(load_answers("answers.yaml")).get(fact, "")}
    return "added"


def ask_conflict(label, stored, new, who, tenant=None, fact=None):
    """Put a conflict to him: what is stored stays until he replies. With `fact`: a portal
    entry; without: an answer kept for this portal."""
    from jobpilot.review import ask as ask_mod
    match = norm(label)
    if fact:
        key = "learned-" + re.sub(r"[^a-z0-9]+", "-", f"{tenant}-{fact}".lower())[:80].strip("-")
        q = (f"On the {who} form you picked “{new}” for your {fact}, but this portal ({tenant}) already has "
             f"“{stored}” for it. Which should its next forms use?")
    else:
        where = f"every {tenant} form"
        key = "learned-" + re.sub(r"[^a-z0-9]+", "-", f"{tenant or ''} {match}".strip())[:80].strip("-")
        q = (f"On the {who} form you answered “{new}” for “{label}”, but your stored answer is "
             f"“{stored}”. Which should {where} use from now on?")
    ask_mod.pose(key, q, hint="reply keep (the stored one), new (this form's), or type the one to store",
                 about=f"Stored answer — {label[:60]}",
                 extra={"learned": {"match": match, "new": new, "tenant": tenant, "fact": fact}})
    return key


def resolve(q):
    """His reply to a conflict question: keep / new / the answer itself. What was done."""
    info = q.get("learned") or {}
    reply = (q.get("answer") or "").strip()
    if not info or not reply or reply.lower() == "keep":
        return "kept"
    new = info["new"] if reply.lower() == "new" else reply
    t = load_tenant(info.get("tenant"))
    if info.get("fact"):
        row = t["entries"].get(info["fact"])
        if not row:
            return "kept (the portal entry is no longer stored)"
        row["entry"] = new
    else:
        row = next((e for e in t["answers"] if norm(e.get("match", "")) == info["match"]), None)
        if not row:
            return "kept (the answer is no longer stored)"
        row["answer"] = new
    save_tenant(t)
    return f"stored for {info.get('tenant')}: {new}"


# ------------------------------------------------------------------ undo

def snapshot(tenant=None):
    """{key: entry} — what a decision's answers may change, for its undo:
    "employer::<tenant>::<match>", "entry::<tenant>::<fact key>"."""
    if not tenant:
        return {}
    t = load_tenant(tenant)
    out = {f"employer::{tenant}::{norm(e.get('match', ''))}": dict(e) for e in t["answers"]}
    out.update({f"entry::{tenant}::{k}": dict(v) for k, v in t["entries"].items()})
    return out


def restore(changes):
    """Undo one decision's learning: [(key, the entry before or None)]. Keys of the retired
    general store (no "::", or "general::") are left alone."""
    tenants = {}
    for m, before in changes:
        kind, _, rest = m.partition("::")
        if kind not in ("employer", "entry"):
            continue
        tenant, _, k = rest.rpartition("::")
        t = tenants.setdefault(tenant, load_tenant(tenant))
        if kind == "entry":
            t["entries"].pop(k, None)
            if before:
                t["entries"][k] = before
        else:
            t["answers"] = [e for e in t["answers"] if norm(e.get("match", "")) != k]
            if before:
                t["answers"].append(before)
    for t in tenants.values():
        save_tenant(t)


# ------------------------------------------------------------------ one submission

def learn(sub):
    """Keep one review decision's answers for the portal they were given on. {what: how many}."""
    who = sub.get("company") or "?"
    tenant = sub.get("tenant")
    counts = {}

    def count(k):
        counts[k] = counts.get(k, 0) + 1

    if not tenant:
        return {"not kept (no portal known)": len(sub.get("answers", []))}
    t = load_tenant(tenant)
    t["company"] = t.get("company") or (who if who != "?" else None)
    for ans in sub.get("answers", []):
        text, label = (ans.get("text") or "").strip(), ans.get("label", "")
        if not text or ans.get("kind") == "unanswered":
            count("skipped")
            continue
        if ans.get("stand_in_for"):                       # a stored fact's stand-in: this portal's entry
            got = merge_entry(t, ans["stand_in_for"], text)
            count(f"portal entry {got}")
            if got == "conflict":
                ask_conflict(label, t["entries"][ans["stand_in_for"]]["entry"], text, who, tenant, ans["stand_in_for"])
            continue
        why = kept(label, text, bool(ans.get("choice")))
        if why:
            count(f"not kept ({why})")
            continue
        got = merge(t["answers"], label, text)
        count(f"answer {got}")
        if got == "conflict":
            old = next(e["answer"] for e in t["answers"] if norm(e.get("match", "")) == norm(label))
            ask_conflict(label, old, text, who, tenant)
    save_tenant(t)
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    for p in a.files:
        counts = learn(json.load(open(p)))
        print("  learned: " + (", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "nothing"))


if __name__ == "__main__":
    sys.exit(main())
