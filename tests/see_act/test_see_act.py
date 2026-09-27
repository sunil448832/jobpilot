"""tests/see_act/test_see_act.py — exploration by SEE and ACT, end to end, on two
imitation portals (fixtures.py), for understanding. Uses nothing from src/.

    python tests/see_act/test_see_act.py                # Claude maps each page (real time)
    python tests/see_act/test_see_act.py --headed       # …and watch the browser do it
    python tests/see_act/test_see_act.py --offline      # no Claude: a fixed table maps the pages

For each portal it prints what happens:

  PASS 1 — EXPLORE   (never presses Submit)
      see   the page's accessibility snapshot: role + name of every control
      map   CLAUDE reads the snapshot and returns the page's controls as JSON: question,
            kind, required, and the KEY of the stored fact that answers it (never a value).
            The code checks each entry points at a real control and a real key.
      menus map_menu opens each menu (and each category in it) to see the option tree
      act   fill / choose / pick / check / press / attach — each one checked, each one recorded
      a question nothing answers gets a PLACEHOLDER and becomes a question for the applicant

  APPROVAL           the applicant answers the placeholders on the phone (simulated)

  PASS 2 — SUBMIT    (plain code, no Claude)
      the recipes run again with the true values, then Submit, then the confirmation is checked
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures                                       # noqa: E402
import claude_map                                     # noqa: E402
from see_act import (see, act, alert, current_step, locate, Recorder, replay,   # noqa: E402
                     sign_in_or_create, Refused, map_menu, find_path, leaves)

# ------------------------------------------------------------------ the applicant's stored facts
RESUME = os.path.join(tempfile.mkdtemp(prefix="see-act-"), "sunil_resume.pdf")
open(RESUME, "wb").write(b"%PDF-1.4 demo resume\n")
FACTS = {
    "first_name": "Sunil", "last_name": "Sharma", "email": "sunil@example.com",
    "country": "India", "device": "Mobile", "source": "LinkedIn",
    "previously_employed": "No", "needs_sponsorship": "Yes", "work_authorized": "No",
    "file:resume": RESUME,
}
CREDS = ("sunil@example.com", "demo-password-1")
ARGS = None
# every page's map is saved as JSON: tests/see_act/maps/<run>/<nn>-<portal>-<step>.json
MAPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maps")
RUN = {"dir": None, "n": 0, "portal": ""}


def save_map(step, record):
    """One iteration's map: the snapshot read, the map returned, what the check kept
    and rejected, and the menu trees seen while acting."""
    RUN["n"] += 1
    name = f"{RUN['n']:02d}-{RUN['portal']}-{re.sub(r'[^a-z0-9]+', '-', step.lower()).strip('-') or 'page'}.json"
    path = os.path.join(RUN["dir"], name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    return path


# ------------------------------------------------------------------ MAP, offline: a fixed table
# What --offline uses instead of Claude, so the test runs anywhere and repeatably.
QUESTION_TO_FACT = [
    (r"given name|first name", "first_name"), (r"family name|last name", "last_name"),
    (r"^email", "email"), (r"^country", "country"), (r"device type", "device"),
    (r"hear about", "source"), (r"employed by .* in the past", "previously_employed"),
    (r"sponsorship", "needs_sponsorship"), (r"authori[sz]ed to work", "work_authorized"),
]
NAV = re.compile(r"^(save and continue|next|back|sign in|create account|attach resume|submit.*)$", re.I)


def offline_map(page, root):
    """The same [name, kind, fact] entries Claude returns, from the regex parser + the table above."""
    out, groups = [], set()
    for c in see(page, root):
        if c.role == "option" or (c.role == "button" and NAV.search(c.name)):
            continue
        name, kind = c.name, None
        if c.role == "button" and c.name == "Attach":
            kind = "file"
        elif c.role == "textbox":
            kind = "text"
        elif c.role == "combobox":
            native = locate(page, "combobox", c.name).evaluate("el => el.tagName") == "SELECT"
            kind = "native-select" if native else "search-and-pick"
        elif c.role == "button" and c.name.endswith("Select One"):
            kind = "dropdown"
        elif c.role in ("radio", "button") and c.group and c.group not in groups:
            groups.add(c.group)
            name, kind = c.group, "radio-group" if c.role == "radio" else "yes-no-buttons"
        if not kind:
            continue
        e = claude_map.expand(name, kind, None)
        e["fact"] = "file:resume" if kind == "file" else \
            next((k for rx, k in QUESTION_TO_FACT if re.search(rx, e["question"], re.I)), None)
        out.append(e)
    return out


# ------------------------------------------------------------------ one page, whatever the portal
MAX_ROUNDS = 3                 # map -> check -> act, and back to Claude with the failures, at most this often
LAST_MAP = {}                  # step -> the page's current map (what a later correction builds on)


def sabotage(entries, log):
    """--sabotage: plant the two mistakes Claude made on 2026-09-25 into its FIRST
    answer — radios called yes-no-buttons, and a dropdown taken for a type-in box —
    so the correction loop can be watched on demand."""
    for e in entries:
        if e["kind"] == "radio-group":
            e.update(claude_map.expand(e["name"], "yes-no-buttons", e["fact"]))
            log(f"    sabotage: {e['question']!r} radio-group -> yes-no-buttons")
        elif e["kind"] == "dropdown":
            e.update(claude_map.expand(e["name"], "search-and-pick", e["fact"]))
            log(f"    sabotage: {e['question']!r} dropdown -> search-and-pick")
    return entries


def get_map(page, root, step, snap, record, log, failures=None):
    """The page's map, checked against the live page. The failures — and only
    them — go back to Claude, whose fixes are merged in, until clean or MAX_ROUNDS.
    Returns (good entries, remaining failures)."""
    if ARGS.offline:
        good, bad = claude_map.check_map(page, offline_map(page, root), FACTS, locate)
        record["rounds"].append({"by": "offline table", "failures": [[claude_map.compact(e), err] for e, err in bad]})
        for e, err in bad:
            log(f"      ✗ check: {e['name']!r}: {err}")
        return good, bad
    failed = {e["name"] for e, _ in failures or [] if e}
    good = [e for e in LAST_MAP.get(step, []) if e["name"] not in failed] if failures else None
    for rnd in range(1, MAX_ROUNDS + 1):
        entries = claude_map.ask(step, snap, FACTS, model=ARGS.model, log=log, failures=failures)
        if ARGS.sabotage and not record["rounds"]:
            entries = sabotage(entries, log)
        entries = claude_map.merge(good, entries) if failures else entries
        good, bad = claude_map.check_map(page, entries, FACTS, locate)
        LAST_MAP[step] = good
        record["rounds"].append({"round": len(record["rounds"]) + 1,
                                 "fed_back": [[claude_map.compact(e) if e else None, err] for e, err in failures or []],
                                 "reply": [claude_map.compact(e) for e in entries],
                                 "failures": [[claude_map.compact(e), err] for e, err in bad]})
        for e, err in bad:
            log(f"      ✗ check: {e['name']!r}: {err}")
        if not bad:
            return good, []
        log(f"    -> {len(bad)} failed entr{'y' if len(bad) == 1 else 'ies'} back to Claude (round {rnd + 1} of {MAX_ROUNDS})"
            if rnd < MAX_ROUNDS else f"    -> still {len(bad)} failing after {MAX_ROUNDS} rounds: acting on the rest")
        failures = bad
    return good, bad


def act_on(page, e, rec, log, placeholders, record):
    """One control from the map. Returns None, or the error for Claude."""
    kind, role, name, group, q = e["kind"], e["role"], e["name"], e.get("group") or "", e["question"]
    key = e.get("fact")
    value = FACTS.get(key) if key else None
    source = f"fact:{key}" if key else "placeholder"
    try:
        if kind == "file":
            r = rec.do("button", name, "attach", FACTS["file:resume"], "file:resume")

        elif kind == "text":
            if locate(page, role, name).input_value():
                return None
            r = rec.do(role, name, "fill", value or "To be confirmed", source)
            if not value:
                placeholders[q] = ("To be confirmed", [])

        elif kind in ("search-and-pick", "native-select", "dropdown"):
            tree = map_menu(page, role, name, log=log)              # see the whole menu, then act by PATH
            record["menus"][q] = tree
            action = "pick" if kind == "dropdown" else "choose"
            path = find_path(tree, value) if value else None
            if tree:
                log(f"    menu {q!r}: {tree}")
            if path:
                r = rec.do(role, name, action, value, source, path=path[:-1] if action == "choose" else ())
            elif tree:
                choices = leaves(tree)
                v = next((x for x in choices if x.split(" › ")[-1].lower() == "no"), choices[0])
                log(f"    {value!r} is not offered — placeholder {v!r}, asked with {choices}")
                r = rec.do(role, name, action, v.split(" › ")[-1], "placeholder",
                           path=v.split(" › ")[:-1] if action == "choose" else ())
                placeholders[q] = (v, choices)
            else:
                r = rec.do(role, name, action, value or "No", source)   # search-only menu

        elif kind in ("radio-group", "yes-no-buttons"):             # the check made sure the kind is right
            radios = kind == "radio-group"
            grp = page.get_by_role("group", name=group or name).first
            options = [o.inner_text().strip() or o.get_attribute("value") or ""
                       for o in grp.get_by_role("radio" if radios else "button").all()]
            v = value or next((o for o in options if o.lower() == "no"), options[0] if options else "No")
            r = rec.do("radio" if radios else "button", v, "check" if radios else "press",
                       source=source, group=group or name)
            if not value:
                placeholders[q] = (v, options)

        elif kind == "checkbox" and value and str(value).lower() in ("yes", "true"):
            r = rec.do(role, name, "check", source=source)
        else:
            return None
    except Exception as ex:
        return f"acting on it failed — {type(ex).__name__}: {str(ex).splitlines()[0][:120]}"
    if not r.get("ok"):
        return (f"the value did not take (shows {r.get('shown')!r}"
                + (f", menu offers {r['offered']}" if r.get("offered") else "") + ")")
    return None


def work_page(page, rec, log, placeholders, errors=None):
    """SEE the page, MAP it (Claude, checked), ACT on every control. What fails —
    in the check or while acting — goes back to Claude with its previous map, and
    only the controls that failed are acted on again. `errors`: what the portal
    said when it refused the page (Save and Continue), fed back the same way."""
    root = "main" if page.locator("main").count() else "form"
    step = current_step(page) or "the page"
    snap = claude_map.snapshot(page, root)
    record = {"step": step, "mapped_by": "offline table" if ARGS.offline else f"claude ({ARGS.model})",
              "at": dt.datetime.now().isoformat(timespec="seconds"), "snapshot": snap,
              "refused_with": errors or [], "rounds": [], "menus": {}, "acted": []}
    if not ARGS.offline:
        log("    see (the snapshot Claude reads):\n" + "\n".join("      " + l for l in snap.splitlines()))
    done, first = set(), len(rec.recipes)
    pending = [(None, err) for err in errors] if errors else None
    for attempt in range(1, MAX_ROUNDS + 1):
        controls, check_bad = get_map(page, root, step, snap, record, log, failures=pending)
        log(f"    map ({'offline table' if ARGS.offline else 'Claude'}), attempt {attempt}:")
        for e in controls:
            log(f"      {e['kind']:15} {e['question'][:48]!r:50} "
                f"-> {('fact ' + e['fact']) if e.get('fact') else 'nothing stored: PLACEHOLDER'}"
                + ("   (done)" if e["question"] in done else ""))
        act_bad = []
        for e in controls:
            if e["question"] in done:
                continue
            err = act_on(page, e, rec, log, placeholders, record)
            if err:
                log(f"      ✗ act: {e['name']!r}: {err}")
                act_bad.append((e, err))
            else:
                done.add(e["question"])
        if not act_bad or ARGS.offline:
            break
        log(f"    -> {len(act_bad)} failed action(s) back to Claude")
        pending = act_bad + check_bad
    record["acted"] = [r.__dict__ for r in rec.recipes[first:]]
    log(f"    saved {os.path.relpath(save_map(step, record))}")


# ------------------------------------------------------------------ WORKDAY-STYLE
def explore_workday(page, url, log):
    page.goto(url + "/workday")
    log("  gate (sign in, or create the account):")
    assert sign_in_or_create(page, *CREDS, log=log)
    rec, placeholders = Recorder(page, log), {}
    for _ in range(6):
        step = current_step(page)
        log(f"\n  STEP: {step}")
        if step == "Review":
            break
        work_page(page, rec, log, placeholders)
        rec.do("button", "Save and Continue", "click")
        for _ in range(2):                            # the portal refused the page: back to Claude
            said = alert(page)
            if not said or current_step(page) != step:
                break
            log(f"    the page refused: {said}  -> back to Claude")
            work_page(page, rec, log, placeholders, errors=[f"Save and Continue refused the page: {said}"])
            rec.do("button", "Save and Continue", "click")
        if alert(page) and current_step(page) == step:
            log(f"    still refused: {alert(page)}")
            break
    try:
        act(page, "button", "Submit", "click", mode="explore")
    except Refused as e:
        log(f"\n  guard: {e}")
    return rec.recipes, placeholders, current_step(page) == "Review"


def submit_workday(page, url, recipes, approved, log):
    page.goto(url + "/workday")
    log("  gate:")
    assert sign_in_or_create(page, *CREDS, log=log)             # the account exists now: plain sign-in
    replay(page, recipes, FACTS, approved, log=log)
    act(page, "button", "Submit", "click", mode="replay")
    return "application submitted" in page.inner_text("body").lower()


# ------------------------------------------------------------------ GREENHOUSE-STYLE
def explore_greenhouse(page, url, log):
    page.goto(url + "/greenhouse")
    rec, placeholders = Recorder(page, log), {}
    log("\n  STEP: the one page")
    work_page(page, rec, log, placeholders)
    try:
        act(page, "button", "Submit application", "click", mode="explore")
    except Refused as e:
        log(f"\n  guard: {e}")
    return rec.recipes, placeholders, True


def submit_greenhouse(page, url, recipes, approved, log):
    page.goto(url + "/greenhouse")
    replay(page, recipes, FACTS, approved, log=log)
    act(page, "button", "Submit application", "click", mode="replay")
    return "thank you for applying" in page.inner_text("body").lower()


# ------------------------------------------------------------------ run both
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="map pages with a fixed table instead of Claude")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--slow", type=int, default=0, help="ms pause between browser actions (with --headed)")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--sabotage", action="store_true",
                    help="plant the two known mistakes in Claude's first map, to watch the correction loop")
    ARGS = ap.parse_args()

    # the tree helpers on their own: a menu WITHOUT LinkedIn gives no path, and the
    # phone is offered every real choice, categories included
    no_li = {"Career Site": {"Company Website": None}, "Employee Referral": None, "Recruiter": None}
    check(find_path(no_li, "LinkedIn") is None, "a stored answer the menu does not offer has no path")
    check(leaves(no_li) == ["Career Site › Company Website", "Employee Referral", "Recruiter"],
          f"the phone gets the real choices: {leaves(no_li)}")
    check(find_path({"Social Media": {"LinkedIn": None}}, "linkedin") == ["Social Media", "LinkedIn"],
          "the path to a nested option, case-insensitive")

    RUN["dir"] = os.path.join(MAPS, dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                              + ("-offline" if ARGS.offline else f"-{ARGS.model}"))
    os.makedirs(RUN["dir"], exist_ok=True)
    srv, url = fixtures.serve()
    print(f"\nmaps saved to: {os.path.relpath(RUN['dir'])}")
    print(f"\nmapping pages with: {'the offline table' if ARGS.offline else 'Claude (' + ARGS.model + ')'}")
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome", headless=not ARGS.headed, slow_mo=ARGS.slow)
        for portal, explore, submit in (("WORKDAY-STYLE", explore_workday, submit_workday),
                                        ("GREENHOUSE-STYLE", explore_greenhouse, submit_greenhouse)):
            ctx = br.new_context()                              # one browser profile per portal
            RUN["portal"] = portal.split("-")[0].lower()
            print("\n" + "=" * 78 + f"\n{portal}\n" + "=" * 78)
            print("PASS 1 — EXPLORE")
            page = ctx.new_page()
            recipes, placeholders, reached = explore(page, url, print)
            page.close()

            print("\nRECIPES recorded (this is replay.json):")
            for r in recipes:
                print(f"  {r.step[:22]:22} {r.action:6} {r.role:8} {r.name[:42]!r:44}"
                      + (f" in {r.group[:30]!r}" if r.group else "") + (f" via {r.path}" if r.path else "")
                      + f"  <- {r.source or '(button)'}")

            print("\nAPPROVAL on the phone (simulated):")
            approved = {}
            for q, (ph, choices) in placeholders.items():
                answer = choices[0].split(" › ")[-1] if choices else "I want to build production LLM systems at Acme."
                approved[q] = answer
                print(f"  {q!r}: placeholder {ph!r}" + (f", choices {choices}" if choices else "")
                      + f" -> he answers {answer!r}")
            if not placeholders:
                print("  nothing to answer")

            print("\nPASS 2 — SUBMIT (code only, no Claude)")
            page = ctx.new_page()
            ok = submit(page, url, recipes, approved, print)
            print()
            check(reached, f"{portal}: exploration reached the last page without pressing Submit")
            check(ok, f"{portal}: the replay filled everything and the portal confirmed the submission")
            page.close()
            ctx.close()
        br.close()
    srv.shutdown()
    print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED:\n  " + "\n  ".join(fails))
    sys.exit(1 if fails else 0)
