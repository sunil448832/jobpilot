"""tests/test_replay.py — explore -> replay round trip on a local fixture form, headless Chrome, no network."""
import json, os, sys, tempfile
from playwright.sync_api import sync_playwright

from jobpilot.fill import autofill, browser as B, replay as R

SCRATCH = tempfile.mkdtemp(prefix="replay-test-")
R.APPLICATIONS = SCRATCH          # replay.json lands here, not in the real applications/
R.TOOL = SCRATCH

HTML = """
<form>
  <label for="fn">First Name*</label><input id="fn" name="first_name" required>
  <label for="col">Favourite colour*</label><input id="col" name="colour" required>
  <label for="age">Years at current employer*</label><input id="age" name="years" type="number" required>
  <label for="why">Why do you want to work here?*</label><textarea id="why" name="why" required></textarea>
  <label for="dept">Department*</label>
  <select id="dept" name="dept" required><option value="">Select...</option><option>Platform</option><option>Research</option></select>
  <fieldset><legend>Do you own a cat?*</legend>
    <label><input type="radio" name="cat" value="yes" required>Yes</label>
    <label><input type="radio" name="cat" value="no" required>No</label>
  </fieldset>
  <fieldset><legend>Which teams interest you?*</legend>
    <label><input type="checkbox" name="teams" value="infra" required>Infra</label>
    <label><input type="checkbox" name="teams" value="ml">ML</label>
  </fieldset>
  <fieldset><legend>Are you over 18 years of age?*</legend>
    <label><input type="radio" name="adult" value="yes" required>Yes</label>
    <label><input type="radio" name="adult" value="no">No</label>
  </fieldset>
  <label for="cv">Resume*</label><input id="cv" name="resume" type="file" required>
</form>
"""

answers = autofill.load("answers.yaml")
ctx = {"market": "usa", "company": "Fixture Co", "company_slug": "fixture-co", "role": "MLE",
       "portal": "greenhouse", "location": "Remote", "url": "about:blank"}
base_resolve, _ = autofill.build_resolver(answers, ctx)

def with_own(own):
    """The submit-pass resolver: his approved answers first, then the heuristics."""
    o = {B.norm(k): v for k, v in own.items()}
    def resolve(label):
        L = B.norm(label)
        if L in o:
            return {"yes": B.YES, "no": B.NO}.get(o[L].lower(), o[L])
        for k, v in o.items():
            if len(k) > 20 and (k in L or L in k):
                return {"yes": B.YES, "no": B.NO}.get(v.lower(), v)
        return base_resolve(label)
    return resolve

def state(page):
    return page.evaluate("""() => ({
        fn: document.querySelector('#fn').value, col: document.querySelector('#col').value,
        age: document.querySelector('#age').value, why: document.querySelector('#why').value,
        dept: document.querySelector('#dept').value,
        cat: (document.querySelector('input[name=cat]:checked')||{}).value || '',
        teams: [...document.querySelectorAll('input[name=teams]:checked')].map(e => e.value),
        adult: (document.querySelector('input[name=adult]:checked')||{}).value || '',
        cv: document.querySelector('#cv').files.length })""")

fails = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: fails.append(msg)

with sync_playwright() as pw:
    br = pw.chromium.launch(channel="chrome", headless=True)

    # ---------------- pass 1: explore
    print("pass 1 — explore")
    page = br.new_page(); page.set_content(HTML)
    ctx["replay"] = rep = R.Replay(explore=True)
    filled, warnings, missing = B.fill_fields(page, base_resolve, answers, ctx)
    st = state(page)
    check(st["fn"] == answers["personal"]["first_name"], f"mapped text filled: {st['fn']!r}")
    check(st["col"] == R.DUMMY_TEXT, f"unmapped text got placeholder: {st['col']!r}")
    check(st["age"] == "1", f"number got placeholder: {st['age']!r}")
    check(st["why"] == R.DUMMY_TEXT, f"textarea got placeholder")
    check(st["dept"] == "Platform", f"select got first real option: {st['dept']!r}")
    check(st["cat"] == "no", f"unmapped radio got the No placeholder (opens no follow-ups): {st['cat']!r}")
    check(st["teams"] == ["infra"], f"unmapped checkbox group got first option: {st['teams']}")
    check(st["adult"] == "yes", f"mapped radio answered from rules: {st['adult']!r}")
    check(st["cv"] == 1, "resume uploaded")
    ph = rep.placeholders
    check({B.norm(k) for k in ph} == {"favourite colour", "years at current employer", "why do you want to work here?",
                                      "department", "do you own a cat?", "which teams interest you?"},
          f"placeholders recorded: {sorted(ph)}")
    check(not any(k in filled for k in ph), "placeholders kept out of `filled`")
    check({m["label"] for m in missing} == set(ph), "every placeholder is a `missing` record -> question")
    dummies = [r for r in rep.record if r.get("dummy")]
    check(len(dummies) == 6 and all(r.get("required") for r in dummies), f"{len(dummies)} dummy recipes, all required")
    strategies = {B.norm(r["label"]): r["strategy"] for r in rep.record}
    check(strategies.get("first name") == "fill" and strategies.get("department") == "select"
          and strategies.get("do you own a cat?") in ("check", "label-click", "force-click", "js-set")
          and strategies.get("resume") == "set_input_files", f"strategies recorded: {strategies}")
    rel = rep.save(ctx)
    doc = json.load(open(os.path.join(SCRATCH, rel)))
    check(len(doc["recipes"]) == len(rep.record) and doc["placeholders"] == ph, f"replay.json written: {rel}")
    qs = R.annotate_questions([{"label": "Favourite colour"}, {"label": "First Name"}], ph)
    check("placeholder" in qs[0].get("note", "") and "note" not in qs[1], "questions annotated with their placeholder")
    page.close()

    # ---------------- the gate
    print("gate")
    item = {"company_slug": "fixture-co", "replay": rel}
    rep2 = R.Replay(recipes=R.load(item)["recipes"])
    gaps = rep2.gaps(base_resolve)
    check({B.norm(g["label"]) for g in gaps} == {B.norm(k) for k in ph}, f"without answers every placeholder is a gap ({len(gaps)})")
    check(all("placeholder" in g["reason"] for g in gaps), "gap reasons name the placeholder")
    own = {"Favourite colour": "Blue", "Years at current employer": "4",
           "Why do you want to work here?": "Because of the agent platform work.",
           "Department": "Research", "Do you own a cat?": "No", "Which teams interest you?": "ML"}
    check(rep2.gaps(with_own(own)) == [], "with approved answers there are no gaps")

    # ---------------- approval backfills replay.json; submit reads it
    print("approval backfill")
    n = R.apply_answers(item, own)
    doc = json.load(open(os.path.join(SCRATCH, rel)))
    left = [r["label"] for r in doc["recipes"] if r.get("dummy")]
    check(n == 6 and not left and not doc["placeholders"], f"6 placeholder recipes now carry his answers ({n}); none left: {left}")
    col = next(r for r in doc["recipes"] if B.norm(r["label"]) == "favourite colour")
    check(col["value"] == "Blue" and col["answered_by"] == "applicant", f"recipe value is the answer: {col['value']!r}")
    long = next(r for r in doc["recipes"] if B.norm(r["label"]).startswith("why do you want"))
    check(long["value"] == own["Why do you want to work here?"], "a long answer is kept whole, not truncated")
    check(doc["answers"]["Department"] == "Research", "answers map kept in replay.json")
    full = dict(item, market="usa", company="Fixture Co", role="MLE", portal="greenhouse", location="Remote",
                url="about:blank", fields={}, questions=[])        # nothing on the item itself
    _, rv = B.resolver_for_item(full, answers)
    check(rv("Favourite colour*") == "Blue" and rv("Department") == "Research",
          "submit resolver answers from replay.json alone")
    check(rep2.gaps(with_own({k: v for k, v in own.items() if k != "Department"})) and
          [B.norm(g["label"]) for g in rep2.gaps(with_own({k: v for k, v in own.items() if k != "Department"}))] == ["department"],
          "one missing answer -> exactly that gap")

    # ---------------- pass 2a: replay on a fresh page (Greenhouse-like)
    print("pass 2a — replay, fresh page")
    page = br.new_page(); page.set_content(HTML)
    ctx["replay"] = rep3 = R.Replay(recipes=R.load(item)["recipes"])
    filled, warnings, missing = B.fill_fields(page, with_own(own), answers, ctx)
    st = state(page)
    check(st["col"] == "Blue" and st["age"] == "4" and st["why"].startswith("Because"), f"true text values in: {st['col']!r}, {st['age']!r}")
    check(st["dept"] == "Research", f"true select value: {st['dept']!r}")
    check(st["cat"] == "no", f"true radio: {st['cat']!r}")
    check(st["teams"] == ["ml"], f"true checkbox set, placeholder box not ticked: {st['teams']}")
    check(not rep3.placeholders and not [r for r in rep3.record if r.get("dummy")], "replay wrote no placeholders")
    check(rep3.replayed == 8 and rep3.discovered == 0, f"recipes covered every non-file control: replayed {rep3.replayed}, discovered {rep3.discovered}")
    check(not [m for m in missing if m["required"]], f"nothing required left missing: {missing}")
    page.close()

    # ---------------- pass 2b: replay over a persisted draft (Workday-like) with placeholders still in it
    print("pass 2b — replay over a draft that still holds the placeholders")
    page = br.new_page(); page.set_content(HTML)
    page.evaluate("""() => {
        document.querySelector('#col').value = 'To be confirmed';
        document.querySelector('#dept').value = 'Platform';
        document.querySelector('input[name=cat][value=yes]').checked = true;
        document.querySelector('input[name=teams][value=infra]').checked = true; }""")
    ctx["replay"] = rep4 = R.Replay(recipes=R.load(item)["recipes"])
    B.fill_fields(page, with_own(own), answers, ctx)
    st = state(page)
    check(st["col"] == "Blue", f"placeholder text overwritten: {st['col']!r}")
    check(st["dept"] == "Research", f"placeholder select replaced: {st['dept']!r}")
    check(st["cat"] == "no", f"placeholder radio replaced: {st['cat']!r}")
    check(st["teams"] == ["ml"], f"placeholder checkbox UNTICKED, answer ticked: {st['teams']}")
    page.close()

    # ---------------- pass 2c: a new control the recipes never saw is still discovered
    print("pass 2c — unknown control falls back to discovery")
    page = br.new_page(); page.set_content(HTML.replace("</form>", '<label for="li">LinkedIn Profile</label><input id="li" name="linkedin"></form>'))
    ctx["replay"] = rep5 = R.Replay(recipes=R.load(item)["recipes"])
    B.fill_fields(page, with_own(own), answers, ctx)
    check(page.evaluate("() => document.querySelector('#li').value") == answers["links"]["linkedin"], "new field filled by discovery")
    check(rep5.discovered == 1, f"counted as discovered: {rep5.discovered}")
    page.close()

    # ---------------- attempts log
    ctx["replay"] = rep3
    item["status"] = "submitted"
    rep3.note_attempt(item, "submitted")
    doc = json.load(open(os.path.join(SCRATCH, rel)))
    check(len(doc["attempts"]) == 1 and doc["attempts"][0]["replayed"] == rep3.replayed, f"attempt logged in replay.json: {doc['attempts'][0]}")
    br.close()

# ---------------- robust_check honours `first`
print("robust_check ordering")
with sync_playwright() as pw:
    br = pw.chromium.launch(channel="chrome", headless=True)
    page = br.new_page(); page.set_content('<label><input type="checkbox" data-jobbot-idx="0">x</label>')
    ok, how = B.robust_check(page, 0, first="js-set")
    check(ok and how == "js-set", f"recorded rung tried first: {how}")
    check(B.robust_uncheck(page, 0) and not page.evaluate("() => document.querySelector('input').checked"), "robust_uncheck clears it")
    br.close()

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
