"""mapper.py — which stored fact answers each control of a page.

    map_page(frame, step, cached, facts, ...) -> (entries, failures)

1. The SAVED map is applied first (reuse.py): each entry is checked against
   the live page and kept only if it still fits.
2. The controls on the page that no kept entry covers — counted by code from the
   snapshot — go to Claude (src/agents/map), with the page's snapshot and the
   job's facts. Claude answers [name, kind, answer] each: the answer is a fact
   KEY (never a value) — or, when no fact answers the control, the question in
   the page's own words, which the applicant is then asked.
3. Every entry is checked against the live page; the failed ones go back to
   Claude with the exact error, only those, until clean or fill.map_rounds.
   Nothing is silently fixed.

An entry: {"name", "kind", "fact", "question"}; kind "skip" marks a control Claude judged not
a question (a search box, an "Add" button) so a cached page needs no Claude.
"""
import json
import re
import os
import subprocess
import time

from jobpilot.core.config import cfg
from jobpilot.apply.explore import act as A, see as S
from jobpilot.apply.explore import reuse as R
from jobpilot.apply.explore.reuse import covered

KINDS = {"text", "number", "search-and-pick", "dropdown", "native-select", "radio-group",
         "yes-no-buttons", "checkbox-group", "checkbox", "file", "skip", "next", "submit", "start"}
BUTTONS = ("next", "submit", "start")          # the page's own buttons: move on, send, open the form
GROUPED = ("radio-group", "yes-no-buttons", "checkbox-group")
ROLE = {"text": "textbox", "number": "spinbutton", "file": "button", "dropdown": "button",
        "search-and-pick": "combobox", "native-select": "combobox", "checkbox": "checkbox",
        "next": "button", "submit": "button", "start": "button"}
def role_of(frame, kind, name):
    """The accessibility role a control of this kind has on this page."""
    return A.search_role(frame, name) if kind == "search-and-pick" else ROLE.get(kind, "button")


def question(entry):
    """The question as the page words it, for a control no fact answers — given by
    the map (Claude reads it off the page), so no code guesses it from the name.
    A control a fact answers is never asked, so its name is enough."""
    return entry.get("question") or entry["name"]


# What the page's HTML says about a control — handed to Claude, which decides the
# kind from it. No decision is made here.
FACTS_JS = r"""el => {
  const a = n => el.getAttribute(n);
  const f = [el.tagName.toLowerCase() + (el.type ? ' type=' + el.type : '')];
  for (const n of ['role', 'aria-haspopup', 'aria-autocomplete', 'aria-expanded', 'aria-required', 'readonly', 'placeholder'])
    if (a(n) !== null && a(n) !== '') f.push(n + '=' + a(n).slice(0, 30));
  if (el.required) f.push('required');
  let n = el;
  for (let i = 0; i < 3 && n.parentElement; i++) {
    n = n.parentElement;
    if (n.querySelector('input[type=file]')) { f.push('a file input next to it'); break; }
  }
  const box = el.closest('[data-automation-id^="formField-"], fieldset, [role="group"]') || el.parentElement;
  if (box && box.querySelector('[role="listbox"], ul li, [data-automation-id*="prompt" i], svg')) f.push('a list / icon in its field');
  return f.join(', ');
}"""


CHROME = ("banner", "navigation", "contentinfo")      # the site's header, menus and footer


def describe(frame, todo, chrome=True):
    """The controls to map, as lines for Claude — each group once, with what the
    page itself says about the control's kind — and the ones that need no map
    (hidden; with chrome=False the site's header / navigation / footer: a form's
    questions never sit there), returned as skips."""
    lines, skips, groups = [], [], {}
    boxes = {}                                    # a group's checkboxes: one alone is a yes/no question
    for c in todo:
        if c.group and c.role == "checkbox":
            boxes[c.group] = boxes.get(c.group, 0) + 1
    for c in todo:
        ref = c.ref
        if not chrome and c.region in CHROME:
            skips.append({"name": ref, "kind": "skip", "fact": None})
            continue
        if c.group and c.role == "button":
            # a button in a question group: a choice (Yes / No) — or the question's own
            # list button, which is named by the group ("<question> › Select One Required")
            try:
                opens = A.locate(frame, "button", f"{c.group} › {c.name}").get_attribute("aria-haspopup") == "listbox"
            except Exception:
                opens = False
            if opens:
                # the question's own list button: named by the question when its own name is
                # nothing but what it shows ("Select One Required" — it changes with a pick);
                # a button with a label of its own ("Degree Select One") keeps "<section> › <label>"
                try:
                    shows = (A.locate(frame, "button", f"{c.group} › {c.name}").inner_text() or "").strip()
                except Exception:
                    shows = None
                bare = shows is not None and re.sub(r"\s*(Required|\*)\s*$", "", c.name).strip() == shows
                ref = c.group if bare else f"{c.group} › {c.name}"
            else:
                try:
                    part = S.is_part(A.locate(frame, "button", f"{c.group} › {c.name}"))
                except Exception:
                    part = False
                if part:                               # a combobox's own toggle / clear, even in a group
                    skips.append({"name": f"{c.group} › {c.name}", "kind": "skip", "fact": None})
                else:
                    groups.setdefault(c.group, (c.role, []))[1].append(c.name)
                continue
        elif c.group and (c.role == "radio" or c.role == "checkbox" and boxes[c.group] > 1):
            groups.setdefault(c.group, (c.role, []))[1].append(c.name)      # a choice inside a question
            continue
        facts = ""
        try:
            loc = A.locate(frame, c.role, ref)
            if not loc.is_visible():
                skips.append({"name": ref, "kind": "skip", "fact": None})     # nothing a person can reach
                continue
            facts = loc.evaluate(FACTS_JS) or ""
        except Exception:
            pass
        if c.role == "button" and S.is_part(frame.get_by_role("button", name=c.name, exact=True).nth(c.nth - 1)):
            skips.append({"name": ref, "kind": "skip", "fact": None})         # a combobox's own toggle
            continue
        if "type=password" in facts:
            skips.append({"name": ref, "kind": "skip", "fact": None})         # credentials: the platform's, never mapped
            continue
        offers = ""
        if (c.role == "combobox" or "aria-haspopup=listbox" in facts or "aria-haspopup=true" in facts
                or (c.role == "textbox" and "a list / icon in its field" in facts)):
            try:                                        # a list: its choices, read before Claude maps it
                few, n = A.peek_choices(frame, "button" if c.role == "button" else c.role, ref,
                                        small=int(cfg("fill.choices_shown", 30)))
            except Exception:
                few, n = None, 0
            if few:
                offers = f"   choices: {few}"
            elif n:
                offers = f"   a long list ({n}+ entries): search it"
            elif c.role == "combobox":
                offers = "   a list that fills in as you type: search it"
        lines.append(f"- {c.role} {ref!r}" + (f"   [{facts}]" if facts else "") + offers
                     + (f"   (in {c.group!r})" if c.group and " › " not in ref and ref != c.group else "")
                     + (f"   (in page {c.region})" if c.region and c.region != "main" else "")
                     + (f"   (text above: {c.above[:80]!r})" if c.above else ""))
    for g, (role, opts) in groups.items():
        lines.append(f"- group {g!r} holding {role}s: {', '.join(opts)}")
    return lines, skips


KIND_OF = {"textbox": "text", "spinbutton": "number",
           "combobox": "search-and-pick (native-select only for a <select>)",
           "button": "dropdown if it opens a list, file if it picks a file",
           "checkbox": "checkbox", "radio": "radio-group, named by its question"}


def what_is(frame, name):
    """What the page does have under this name — so a wrong kind is corrected in one
    round, not guessed at: ' — the page has a combobox named that: kind search-and-pick'."""
    base = A._split(name)[1]
    # one of a question's choices, sent as a control of its own
    choice_of = [c for c in S.form_controls(S.snapshot(frame), frame) if c.group and c.name == base]
    if choice_of:
        return (f" — {base!r} is one of the choices of the question {choice_of[0].group!r}, not a control: "
                "leave it out (that question's own entry carries the answer)")
    for role, kind in KIND_OF.items():
        try:
            if frame.get_by_role(role, name=base, exact=True).count():
                return f" — the page has a {role} named that: kind {kind}"
        except Exception:
            pass
    # the question's text, not the control's name: the control under it
    q = base.rstrip(" *✱")
    under = [c for c in S.form_controls(S.snapshot(frame), frame) if q and c.above.rstrip(" *✱") == q and not c.group]
    if under:
        return f" — that is a question's text; the control under it is the {under[0].role} named {under[0].ref!r}"
    return ""


def check(frame, entries, facts):
    """(good, [(entry, error)]) — each entry against the live page, each error worded
    so Claude can correct it."""
    good, bad = [], []
    for e in entries:
        kind, name = e.get("kind"), e.get("name") or ""
        if kind not in KINDS:
            if frame.get_by_role("button", name=A._split(name)[1]).count():
                good.append({**e, "kind": "skip"})           # a button it has no kind for: not a question
                continue
            bad.append((e, f"unknown kind {kind!r}"))
            continue
        if kind == "skip":
            good.append(e)
            continue
        if kind in BUTTONS:
            try:
                A.locate(frame, "button", name)
                good.append(e)
            except A.NotFound:
                bad.append((e, f"no button or link named {name!r} on the page"))
            continue
        if e.get("fact") and e["fact"] not in facts:
            bad.append((e, f"fact {e['fact']!r} is not a listed key — use a listed key or the question"))
            continue
        try:
            if kind in GROUPED:
                w = "" if frame.get_by_role("group", name=name).count() else what_is(frame, name)
                if w and " is one of the choices of " in w:
                    bad.append((e, w.lstrip(" —")))
                    continue
                if w:                                # not a group of choices: one control of that name
                    bad.append((e, f"{name!r} is not a group of choices{w}"))
                    continue
                grp = S.group_scope(frame, name)
                if grp is None:
                    bad.append((e, f"no question {name!r} with choices under it" + what_is(frame, name)))
                    continue
                radios, buttons = grp.get_by_role("radio").count(), grp.get_by_role("button").count()
                if kind == "checkbox-group" and not grp.get_by_role("checkbox").count():
                    bad.append((e, "that group holds no checkboxes"))
                    continue
                if kind == "radio-group" and not radios:
                    bad.append((e, "that group holds buttons, not radios — kind yes-no-buttons"))
                    continue
                if kind == "yes-no-buttons" and not buttons:
                    bad.append((e, "that group holds radios, not buttons — kind radio-group"))
                    continue
            elif kind == "file":                     # its button, or the file input under its question
                try:
                    A.locate(frame, "button", name)
                except A.NotFound:
                    if S.under_question(frame, name, "file") is None:
                        raise
            else:
                A.locate(frame, role_of(frame, kind, name), name)
                if kind == "native-select" and A.locate(frame, "combobox", name).evaluate("el => el.tagName") != "SELECT":
                    bad.append((e, "that combobox is not a native <select> — kind search-and-pick"))
                    continue
                if kind == "text" and A.locate(frame, "textbox", name).evaluate("el => el.type === 'password'"):
                    bad.append((e, "a password box: signing in is the platform's own step — leave it out"))
                    continue
                v = facts.get(e.get("fact") or "", "")
                if kind == "checkbox" and v and v.strip().lower() not in ("yes", "no", "true", "false"):
                    bad.append((e, f"a checkbox is set by a Yes / No fact; {e['fact']!r} holds {v[:60]!r} — "
                                   "use a Yes / No fact, or the question"))
                    continue
        except A.NotFound:
            bad.append((e, f"no {ROLE.get(kind, kind)} named exactly {name!r}" + (what_is(frame, name)
                                                                               or " — copy a name from the list you were "
                                                                                  "given; one that is not there: leave it out")))
            continue
        except Exception as ex:
            bad.append((e, f"{type(ex).__name__}: {str(ex)[:100]}"))
            continue
        good.append(e)
    # one button of each kind per page: which one moves on, sends, opens the form
    for k in BUTTONS:
        same = [e for e in good if e["kind"] == k]
        if len(same) > 1:
            names = [e["name"] for e in same]
            for e in same:
                good.remove(e)
                rule = {"start": "the one that opens a blank form to fill in by hand — not autofill "
                                 "from a resume, not reuse of a past application, not Close",
                        "next": "the one that saves this page and moves on",
                        "submit": "the one that sends the application"}[k]
                bad.append((e, f"{len(same)} buttons are marked {k} ({names}) — a page has one {k} button, "
                               f"{rule}; mark only that one and leave the others out"))
    # a page that sends the application is the last one: nothing moves on from it
    sub = [e for e in good if e["kind"] == "submit"]
    for e in [e for e in good if e["kind"] == "next"] if sub else []:
        good.remove(e)
        bad.append((e, f"this page has a submit button ({sub[0]['name']!r}): it is the form's last page and has "
                       f"no next — {e['name']!r} is not one; leave it out"))
    return good, bad


def answer_text(e):
    """An entry's answer as the map writes it."""
    if e.get("fact"):
        return e["fact"]
    if e.get("search"):
        return "search:" + "; ".join(e["search"])
    if e.get("option"):
        return ("guess:" if e.get("guess") else "option:") + e["option"]
    return e.get("question")


def ask(step, snap, lines, facts, learned, failures=None, log=print):
    """Claude's entries for the controls described in `lines` (or the corrections of `failures`)."""
    from jobpilot.core import agents
    from jobpilot.tailor.autotailor import claude_bin, AUTO_CWD
    from jobpilot.apply.explore import facts as F
    cli = claude_bin()
    if not cli:
        raise RuntimeError("claude CLI not found")
    ag = agents.get("map")
    prompt = ag.render(step=step, snapshot=snap, todo="\n".join(lines) or "(none — corrections only)",
                       # a correction round with nothing new to map needs the keys, not the
                       # values: each error already quotes the value in question
                       facts=F.for_prompt(facts, learned) if lines else F.keys_for_prompt(facts, learned))
    if failures:
        prompt += agents.fill(ag.part("correction"), {"failures": "\n".join(
            (f"  {json.dumps([e.get('name'), e.get('kind'), answer_text(e)])}  -> {err}"
             if e else f"  (page) -> {err}")
            for e, err in failures)})
    os.makedirs(AUTO_CWD, exist_ok=True)
    t0 = time.time()
    argv = ag.argv(cli, prompt)
    if failures:                              # a correction: its own model / effort (llm.map_correct)
        from jobpilot.tailor.autotailor import llm_flags
        drop = {i + d for i, a in enumerate(argv) if a in ("--model", "--effort") for d in (0, 1)}
        argv = [a for i, a in enumerate(argv) if i not in drop]
        argv[3:3] = llm_flags("map_correct")
    for attempt in (1, 2):                    # a reply without the array (the model reached for a tool): once more
        p = subprocess.run(argv, cwd=AUTO_CWD, capture_output=True, text=True, timeout=ag.timeout(300))
        out = (p.stdout or "").strip()
        if "[" in out:
            break
        log(f"    [map] no JSON in the reply ({(out or p.stderr)[:80]!r}) — asking again")
    start = out.find("[")
    if start < 0:
        raise RuntimeError(f"no JSON from the map agent: {(out or p.stderr)[:200]}")
    try:
        rows, end = json.JSONDecoder().raw_decode(out[start:])   # the first array; anything after it is ignored
    except json.JSONDecodeError as e:
        raise RuntimeError(f"map agent JSON unreadable: {e}")
    reply_len = end
    entries = []
    for r in rows:
        if not (isinstance(r, list) and len(r) >= 2 and r[0]):
            continue
        ans = str(r[2]).strip() if len(r) > 2 and r[2] else ""
        e = {"name": str(r[0]), "kind": str(r[1]), "fact": None, "question": None}
        if e["kind"] == "button":                   # "just a button": not a question — left out
            e["kind"] = "skip"
        # the third field: option:<a choice the page offers>, a fact key, or the question
        if ans.lower().startswith("option:"):
            e["option"] = ans[7:].strip()
        elif ans.lower().startswith("search:"):
            # a long list: terms to search it with; what it holds comes back to pick from
            e["search"] = [x.strip() for x in ans[7:].split(";") if x.strip()][:3]
        elif ans.lower().startswith("guess:"):
            # the nearest choice, not sure: picked as a placeholder, asked on the phone
            choice, q, near = (ans[6:].split(" | ") + ["", ""])[:3]
            e["option"], e["guess"], e["question"] = choice.strip(), True, (q.strip() or None)
            e["near"] = [x.strip() for x in near.split(";") if x.strip()]    # other entries he may mean
        elif ans in facts or (" " not in ans and ("." in ans or ":" in ans)):
            e["fact"] = ans
        elif ans:
            e["question"] = ans
        entries.append(e)
    log(f"    [map] claude {'corrected' if failures else 'mapped'} {len(entries)} control(s) on '{step}' "
        f"in {time.time() - t0:.0f}s (~{len(prompt) // 4} tokens in, ~{reply_len // 4} out)")
    return entries


def map_page(frame, step, cached, facts, learned, log=print, failures=None, rounds=None, budget=None, chrome=True):
    """(entries, failures) for the page showing in `frame`. `failures`: errors from
    acting, or the portal's refusal, to go back to Claude. `rounds`: a list the
    caller keeps, each round's reply and errors appended (for the record)."""
    rounds = rounds if rounds is not None else []
    snap = S.snapshot(frame)
    good = R.apply(frame, cached, facts, log)            # the saved map, what still fits
    pending = list(failures or [])
    failed_names = {e["name"] for e, _ in pending if e}
    good = [e for e in good if e["name"] not in failed_names]
    for rnd in range(1, int(cfg("fill.map_rounds", 3)) + 1):
        coming_back = [e for e, _ in pending if e]           # failed entries: corrected, not re-asked
        todo = [c for c in S.form_controls(snap, frame) if not covered(c, good + coming_back)]
        lines, skips = describe(frame, todo, chrome)
        good += skips
        todo = [c for c in todo if not covered(c, skips)]
        if not todo and not pending:
            break
        if not cfg("fill.use_claude", True):
            log(f"    [map] {len(todo)} unmapped control(s), Claude is off (fill.use_claude)")
            break
        if budget is not None:
            if budget.get("calls", 0) <= 0:
                log("    [map] Claude-call budget for this page used up — what is left is asked on the phone")
                break
            budget["calls"] -= 1
        new = ask(step, snap, lines, facts, learned, failures=pending, log=log)
        ok, bad = check(frame, new, facts)
        # a control Claude was shown and did not return is not a question: remember that
        answered = ok + [e for e, _ in bad]
        skips = [{"name": c.group if c.role == "radio" and c.group else c.ref, "kind": "skip", "fact": None}
                 for c in todo if not covered(c, answered)]
        # a correction replaces: an entry of the same name, and a button of the same kind
        # (a new `start` is THE start button — the old one does not stay beside it)
        # a guess keeps the fact it stands in for (the note on the phone names the stored answer)
        before = {re.sub(r"[\s*✱]+$", "", x["name"]): x for x in good + [x for x, _ in pending if x]}
        for n in ok:
            old = before.get(re.sub(r"[\s*✱]+$", "", n["name"]), {})
            if n.get("guess") and not n.get("fact") and old.get("fact"):
                n["fact"] = old["fact"]
        same = lambda s: re.sub(r"[\s*✱]+$", "", s or "")       # "Question*" and "Question" are one entry
        new_names = {same(n["name"]) for n in ok}
        new_buttons = {n["kind"] for n in ok if n["kind"] in BUTTONS}
        good = [e for e in good if same(e["name"]) not in new_names and e["kind"] not in new_buttons] + ok + skips
        rounds.append({"round": len(rounds) + 1, "todo": [c.name for c in todo],
                       "fed_back": [[e.get("name") if e else None, err] for e, err in pending],
                       "reply": [[e["name"], e["kind"], answer_text(e)] for e in new],
                       "errors": [[e["name"], err] for e, err in bad]})
        for e, err in bad:
            log(f"      ✗ {e['name'][:50]!r}: {err}")
        if not bad:
            pending = []
            break
        pending = bad
        snap = S.snapshot(frame)
    return good, pending
