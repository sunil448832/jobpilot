"""tests/test_mismatch.py — a required dropdown whose options do not include the
stored answer (NVIDIA's veteran question). Exploration puts ANY option in as a
placeholder so the walk goes on, and asks with the real choices; the submit gate
refuses the stored answer and waits for his pick; his pick, backfilled into
replay.json, settles it. Also: a label longer than 160 characters keeps its '*'."""
import json, os, sys, tempfile
from playwright.sync_api import sync_playwright
from jobpilot.fill import autofill, browser as B, replay as R

SCRATCH = tempfile.mkdtemp(prefix="mismatch-test-")
R.APPLICATIONS = SCRATCH; R.TOOL = SCRATCH

LONG = ("Do you identify as one of the following protected veterans (Disabled Veteran, Recently Separated "
        "Veteran, Active Duty Wartime or Campaign Badge Veteran, Armed Forces Service Medal Veteran)?")
HTML = f"""<form>
  <label for="v">{LONG}*</label>
  <select id="v" required><option value="">Select One</option>
    <option>I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF PROTECTED VETERANS LISTED ABOVE</option>
    <option>I IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN</option>
    <option>I AM NOT A VETERAN</option><option>I DO NOT WISH TO SELF-IDENTIFY</option></select>
  <div data-automation-id="formField-x"><label>{LONG} (listbox)<abbr aria-hidden="true">*</abbr></label>
    <button type="button" aria-haspopup="listbox">Select One</button></div>
</form>"""

fails = []
def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)

answers = autofill.load("answers.yaml")
ctx = {"market": "usa", "company": "Fixture Co", "company_slug": "fixture-mm", "portal": "x", "url": "about:blank"}
resolve, _ = autofill.build_resolver(answers, ctx, learned=[])     # the stored answers.yaml only, not his live learned answers
check(resolve(LONG) == answers["eeo"]["veteran_status"], f"stored answer for the veteran question: {resolve(LONG)!r}")

with sync_playwright() as pw:
    br = pw.chromium.launch(channel="chrome", headless=True); page = br.new_page(); page.set_content(HTML)
    ctx["replay"] = rep = R.Replay(explore=True)
    filled, warnings, missing = B.fill_fields(page, resolve, answers, ctx)
    lb = [f for f in page.evaluate(B.EXTRACT_JS) if f["type"] == "listbox"]
    check(lb and lb[0]["required"], "a >160-char label with an abbr '*' counts as required")
    chosen = page.evaluate("() => document.querySelector('#v').value")
    check(chosen.startswith("I IDENTIFY AS ONE"), f"placeholder = an option, so the walk goes on: {chosen[:40]!r}")
    rec = next(r for r in rep.record if r["type"] == "select-one")
    check(rec.get("dummy") and rec.get("mismatch") and len(rec["options"]) == 4, f"recorded as a mismatch placeholder with 4 real choices")
    m = next(x for x in missing if x["label"].startswith("Do you identify"))
    check("not one of the choices" in m["reason"] and "I AM NOT A VETERAN" in m["options"], "asked, with the real choices")
    qs, _ = B.questions_from_missing(missing, [], resolve=resolve, warnings=[])
    check(any(q["label"].startswith("Do you identify") and "I AM NOT A VETERAN" in q["options"] for q in qs),
          "the question survives the answer-on-file filter (the stored answer does not fit)")
    rel = rep.save(ctx); item = {"company_slug": "fixture-mm", "replay": rel}
    gate = R.Replay(recipes=R.load(item)["recipes"])
    check(any(g["label"].startswith("Do you identify") for g in gate.gaps(resolve)), "submit gate: a stored answer does not settle it")
    R.apply_answers(item, {LONG + "*": "I AM NOT A VETERAN"})
    doc = json.load(open(os.path.join(SCRATCH, rel)))
    r2 = next(r for r in doc["recipes"] if r["type"] == "select-one")
    check(r2["value"] == "I AM NOT A VETERAN" and not r2.get("dummy") and not r2.get("mismatch"), "his pick backfilled into replay.json")
    check(not any(g["label"].startswith("Do you identify") for g in R.Replay(recipes=doc["recipes"]).gaps(resolve)), "gate clear after his pick")
    br.close()

print(); print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
