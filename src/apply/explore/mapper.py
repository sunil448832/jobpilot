"""mapper.py — the MAP step: one call to Claude for one page, and its reply checked.

    map_page(frame, step, facts, learned, ...) -> {"controls", "fitted", "rows", "problems", ...}

The loop is see -> map -> act -> see (docs/exploration-plan.md):
    see     see.describe: the page read against its stored map; every control the map does
            not fit gets an id, its HTML facts, what it shows, its lists read whole
    map     this module: the page, the applicant's facts, the placeholders on the page and
            what the last act could not finish go to Claude (src/agents/map); it answers
            one row per control, [id, write|select, answer]
    act     act.act_rows: each row on its control
    see     again: what is still not right comes back here, under LAST ACT

Code checks a row only for what it can know for certain — an id that is not on the page,
a kind that is neither write nor select, a fact key that is not in FACTS, two rows for one
control, two page buttons of one kind. Whether an answer is the RIGHT one is Claude's to
judge; the page tells, on the next see. A row that fails a check is not acted on: it goes
back to Claude under LAST ACT in the next round, with why.
"""
import json
import os
import subprocess
import time

from jobpilot.apply.explore import see as S, act as A, facts as F


# ---------------------------------------------------------------- Claude

def ask(prompt, model=None, effort=None, log=print):
    """(reply, seconds): one `claude -p` call of the map agent (src/agents/map), at the model
    and effort of llm.map unless given. A reply without the array (the model reached for a
    tool) is asked once more."""
    from jobpilot.core import agents
    from jobpilot.tailor.autotailor import claude_bin, AUTO_CWD
    cli = claude_bin()
    if not cli:
        raise RuntimeError("claude CLI not found")
    ag = agents.get("map")
    argv = ag.argv(cli, prompt)
    for flag, val in (("--model", model), ("--effort", effort)):
        if val and flag in argv:
            argv[argv.index(flag) + 1] = val
        elif val:
            argv[3:3] = [flag, val]
    os.makedirs(AUTO_CWD, exist_ok=True)
    t0, out = time.time(), ""
    for _ in range(2):
        p = subprocess.run(argv, cwd=AUTO_CWD, capture_output=True, text=True, timeout=ag.timeout(300))
        out = (p.stdout or p.stderr or "").strip()
        if rows_of(out) is not None:
            break
        log(f"    [map] no JSON array in the reply ({out[:80]!r}) — asking again")
    return out, time.time() - t0


def rows_of(reply):
    """The first JSON array in the reply, or None."""
    start = (reply or "").find("[")
    if start < 0:
        return None
    try:
        rows, _ = json.JSONDecoder().raw_decode(reply[start:])
        return rows if isinstance(rows, list) else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- what code can check

def check(rows, controls, facts):
    """(good rows, problems [(row, why)]): only what code knows for certain."""
    ids = {c.id: c for c in controls if getattr(c, "id", "")}
    good, problems, seen, buttons = [], [], set(), {}
    for r in rows:
        if not (isinstance(r, list) and len(r) >= 3):
            problems.append((r, "not a row: [id, write|select, answer]"))
            continue
        cid, kind, answer = r[0], r[1], r[2]
        if cid not in ids:
            problems.append((r, f"{cid!r} is not an id in CONTROLS"))
            continue
        if kind not in ("write", "select"):
            problems.append((r, f"kind {kind!r}: a row is write or select"))
            continue
        if cid in seen:
            problems.append((r, f"a second row for {cid}: one row per control"))
            continue
        how, what = A.read_answer(answer, facts, None)
        if how == "keep":
            how, what = what
        if how == "question" and " " not in str(what) and "." in str(what):
            problems.append((r, f"{what!r} is not a key in FACTS — copy a key exactly, or give the question"))
            continue
        if how == "button":
            buttons.setdefault(what, []).append(r)
        seen.add(cid)
        good.append(r)
    # a control marked required that has no row: told to Claude (not answered by code) —
    # for radios, a required question none of whose radios has a row
    answered = {r[0] for r in good}
    # choices of one question: radios, or checkboxes that share a named group with others —
    # the question takes one row between them, not a row each
    is_choice = lambda c: c.role in ("radio", "checkbox") or (c.facts or {}).get("type") in ("radio", "checkbox")
    shared = {}
    for c in ids.values():
        if is_choice(c) and c.group:
            shared.setdefault(c.group, []).append(c)
    radio_groups = {}
    for c in ids.values():
        if not (c.facts or {}).get("required"):
            continue
        if is_choice(c) and (c.role == "radio" or (c.facts or {}).get("type") == "radio" or len(shared.get(c.group, [])) > 1):
            radio_groups.setdefault(c.group or c.above, []).append(c)
        elif c.id not in answered:
            problems.append((f"(no row) {c.id} {c.role} {c.ref!r}",
                             "it is marked required and has no row — answer it: keep: when it already shows the "
                             "right value, the fact key, or the question itself when no fact answers it"))
    for question, cs in radio_groups.items():
        if not any(c.id in answered for c in cs):
            problems.append((f"(no row) choices {[c.id for c in cs]} of {question!r}",
                             "a required question none of whose choices has a row — a row for the choice whose "
                             "label is the applicant's answer"))
    for word, rs in buttons.items():
        if len(rs) > 1:
            for r in rs:
                good.remove(r)
                problems.append((r, f"{len(rs)} buttons marked {word}: a page has one — the one that "
                                    + {"next": "saves this page and moves on",
                                       "submit": "sends the application",
                                       "start": "opens a blank form"}.get(word, "does that")))
    return good, problems


# ---------------------------------------------------------------- what the next round is told

def placeholders_text(outcomes):
    """The placeholders an act set on this page, as the map reads them (by control name):
    they stand in the page, they are not the applicant's answers."""
    out = []
    for o in outcomes or []:
        p = o.get("placeholder")
        if p:
            out.append(f"- {o.get('control', '')} holds {p['used']!r} — a placeholder, not an answer; "
                       f"asked as {p['question']!r}"
                       + (f"; candidates {S.compact(p['candidates'])}" if p.get("candidates") else ""))
    return "\n".join(out) or "(none)"


def last_act_text(outcomes, problems=()):
    """What the last round could not finish, as the map reads it: a search's matches, a
    choice its list did not hold, an error — and rows the check turned back."""
    out = []
    for o in outcomes or []:
        if o.get("ok"):
            continue
        name, offered = o.get("control", ""), o.get("offered")
        if isinstance(offered, dict):
            out.append(f"- {name} — searched: " + "; ".join(f"{t!r} -> {S.compact(h)}" for t, h in offered.items()))
        elif offered:
            out.append(f"- {name} — {o.get('error')}; the list holds {S.compact(offered)}")
        else:
            out.append(f"- {name} — {o.get('error') or 'did not take (shows ' + repr(o.get('shown')) + ')'}")
    for r, why in problems or ():
        out.append(f"- {r} — {why}" if isinstance(r, str) else f"- your row {json.dumps(r, ensure_ascii=False)} — {why}")
    return "\n".join(out) or "(none)"


# ---------------------------------------------------------------- one round

def map_page(frame, step, facts, learned, entries=None, placeholders="(none)", last_act="(none)",
             open_lists=True, chrome=False, model=None, effort=None, log=print):
    """One map round on the page showing in `frame`:
        controls   see's controls (the ones Claude may answer carry an id)
        fitted     the stored map's entries that fit the page — no Claude needed for them
        rows       Claude's rows that pass the check, to act on
        problems   rows turned back, with why — told to the next round under LAST ACT
        prompt, reply, secs   what went to Claude and back (for the record)
    No Claude call when nothing is left to map."""
    from jobpilot.core import agents
    controls, fitted, text, snap = S.describe(frame, entries=entries, open_lists=open_lists, chrome=chrome)
    if not any(getattr(c, "id", "") for c in controls):
        return {"controls": controls, "fitted": fitted, "rows": [], "problems": [], "prompt": "", "reply": "", "secs": 0}
    prompt = agents.get("map").render(step=step, snapshot=snap, todo=text, facts=F.for_prompt(facts, learned),
                                      placeholders=placeholders, last_act=last_act)
    reply, secs = ask(prompt, model, effort, log)
    rows = rows_of(reply) or []
    good, problems = check(rows, controls, facts)
    log(f"    [map] '{step}': {sum(1 for c in controls if c.id)} controls -> {len(good)} rows"
        + (f", {len(problems)} turned back" if problems else "") + f" in {secs:.0f}s")
    return {"controls": controls, "fitted": fitted, "rows": good, "problems": problems,
            "prompt": prompt, "reply": reply, "secs": secs}
