"""tests/test_walk.py — the generic walker on a three-step fixture: landing page with an Apply button,
page 1 -> Next -> page 2 -> Submit. Explore must reach page 2 and record the route
without pressing Submit (guard + logic); replay must follow the route and submit.
Then: the observed route becomes a platform module the registry recognises."""
import json, os, sys, tempfile, http.server, threading, functools
from playwright.sync_api import sync_playwright

from jobpilot.fill import autofill, browser as B, replay as R, walk as W, platforms as P
from jobpilot.fill import platform_learn as learn

SCRATCH = tempfile.mkdtemp(prefix="walk-test-")
R.APPLICATIONS = SCRATCH; R.TOOL = SCRATCH

HTML = """<!doctype html><html><body>
<h1 id="h">Machine Learning Engineer</h1>
<div id="landing"><p>Great job.</p><button id="apply-btn" onclick="show('p1')">Apply now</button></div>
<div id="p1" style="display:none"><h2>Step 1 of 2</h2>
  <label for="fn">First Name*</label><input id="fn" required>
  <label for="col">Favourite colour*</label><input id="col" required>
  <button type="button" data-testid="next-1" onclick="if(!document.querySelector('#col').value){alert('x');return} show('p2')">Next</button>
  <button type="button" id="sub-hidden" style="display:none">Submit application</button>
</div>
<div id="p2" style="display:none"><h2>Step 2 of 2</h2>
  <fieldset><legend>Do you own a cat?*</legend>
    <label><input type="radio" name="cat" value="yes" required>Yes</label>
    <label><input type="radio" name="cat" value="no" required>No</label></fieldset>
  <button type="button" id="submit-btn" onclick="document.body.innerHTML='<p>Thank you for applying!</p>'">Submit application</button>
</div>
<script>
function show(id){ for (const x of ['landing','p1','p2']) document.getElementById(x).style.display = x===id?'block':'none';
  document.getElementById('h').innerText = id==='p1' ? 'Step 1' : id==='p2' ? 'Step 2' : 'Machine Learning Engineer'; }
</script></body></html>"""

# serve it over http so location.href differs from about:blank and goto works
class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        body = HTML.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{port}/careers/apply/42"

answers = autofill.load("answers.yaml")
ctx = {"market": "usa", "company": "Fixture Co", "company_slug": "fixture-walk", "role": "MLE",
       "portal": P.detect(URL)[0], "location": "Remote", "url": URL}
base_resolve, _ = autofill.build_resolver(answers, ctx)

def with_own(own):
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

fails = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: fails.append(msg)

print("registry:", ctx["portal"], P.detect(URL))
check(ctx["portal"] == "127-0-0-1" and P.detect(URL)[1] is False, f"unknown platform named from the host: {ctx['portal']}")

with sync_playwright() as pw:
    br = pw.chromium.launch(channel="chrome", headless=True)

    # ---------------- guard alone: a submit-looking button is inert on pass 1
    print("guard")
    page = br.new_page(); W.guard_exploration(page); page.goto(URL)
    page.evaluate("() => show('p2')")
    page.click("#submit-btn")
    check("Thank you" not in page.inner_text("body") and W.blocked_clicks(page) == ["Submit application"],
          f"click on Submit blocked by the guard: {W.blocked_clicks(page)}")
    page.evaluate("() => show('p1')"); page.click('[data-testid="next-1"]')  # next is not blocked (alert since col empty)
    page.close()

    # ---------------- explore
    print("pass 1 — explore")
    page = br.new_page(); W.guard_exploration(page); page.goto(URL)
    ctx["replay"] = rep = R.Replay(explore=True); rep.platform = ctx["portal"]
    res = W.explore(page, ctx, answers, base_resolve, rep)
    check(rep.start and rep.start["text"] == "Apply now", f"start button recorded: {rep.start}")
    check(res["reached_end"] and res["pages_done"] == 2, f"walked to the last page: {res['pages_done']} pages, reached_end={res['reached_end']}")
    check([p.get("heading") for p in rep.pages] == ["Step 1", "Step 2"], f"pages recorded: {[p.get('heading') for p in rep.pages]}")
    check(rep.pages[0]["next"] == {"text": "Next", "testid": "next-1"}, f"next descriptor: {rep.pages[0]['next']}")
    check(rep.submit == {"text": "Submit application", "id": "submit-btn"}, f"submit descriptor (seen, not pressed): {rep.submit}")
    check("Thank you" not in page.inner_text("body") and not W.blocked_clicks(page), "submit never pressed, guard never needed")
    check(B.norm(list(rep.placeholders)[0]) == "favourite colour" and any(B.norm(k) == "do you own a cat?" for k in rep.placeholders),
          f"placeholders on both pages: {sorted(rep.placeholders)}")
    steps = {B.norm(r["label"]): r.get("step") for r in rep.record}
    check(steps.get("favourite colour", "").startswith("page 1") and steps.get("do you own a cat?", "").startswith("page 2"),
          f"recipes tagged with their page: {steps}")
    check({B.norm(q['label']) for q in res['questions']} == {"favourite colour", "do you own a cat?"}, f"questions from both pages: {[q['label'] for q in res['questions']]}")
    rep.reached_end = res["reached_end"]
    rel = rep.save(ctx); doc = json.load(open(os.path.join(SCRATCH, rel)))
    check(doc["pages"] and doc["submit"] and doc["start"] and doc["reached_end"], "route saved in replay.json")
    page.close()

    # ---------------- replay: follow the route, true answers, press submit
    print("pass 2 — replay")
    page = br.new_page(); page.goto(URL)
    item = {"company_slug": "fixture-walk", "replay": rel, "id": "fixture-walk-0", "company": "Fixture Co", "role": "MLE"}
    ctx["replay"] = rep2 = R.Replay.from_doc(R.load(item))
    own = {"Favourite colour": "Blue", "Do you own a cat?": "No"}
    check(rep2.gaps(base_resolve) and not rep2.gaps(with_own(own)), "gate: placeholders need answers, then none")
    res2 = W.replay(page, ctx, answers, with_own(own), rep2, item)
    check(res2["pages_done"] == 2 and res2["clicked"] and res2["submit_ok"], f"route followed and submitted: {res2['pages_done']} pages, clicked={res2['clicked']}, ok={res2['submit_ok']}")
    check("Thank you for applying" in page.inner_text("body"), "confirmation page reached")
    check(rep2.replayed >= 3 and rep2.discovered == 0, f"recipes covered the controls: replayed {rep2.replayed}, discovered {rep2.discovered}")
    page.close()

    # ---------------- the observed route becomes a platform module
    print("learn from observation")
    learn.DIR = os.path.join(SCRATCH, "platforms"); os.makedirs(learn.DIR)
    p = learn.write_from_observation(rep, ctx)
    src = open(p).read()
    check("ID = '127-0-0-1'" in src and "HOSTS = ('127.0.0.1',)" in src and "'text': 'Apply now'" in src
          and "'testid': 'next-1'" in src and "'id': 'submit-btn'" in src, f"module written: {os.path.basename(p)}")
    ns = {}; exec(src, ns)
    check(ns["NEXT"] == [rep.pages[0]["next"]] and ns["SUBMIT"] == rep.submit and ns["ROUTE"] == "mobile", "module content is the route")

    # ---------------- and hints from a module steer the next walk
    print("walk with module hints")
    import types
    mod = types.ModuleType("fixturemod"); mod.ID = "127-0-0-1"; mod.START = ns["START"]; mod.NEXT = ns["NEXT"]; mod.SUBMIT = ns["SUBMIT"]
    P._load()[mod.ID] = mod
    page = br.new_page(); W.guard_exploration(page); page.goto(URL)
    ctx["replay"] = rep3 = R.Replay(explore=True); rep3.platform = mod.ID
    res3 = W.explore(page, ctx, answers, base_resolve, rep3)
    check(res3["reached_end"] and rep3.start == mod.START and rep3.submit == mod.SUBMIT, "hinted walk reaches the end using the module's buttons")
    page.close()

    # ---------------- application hooks sit above the platform module
    print("application hooks")
    from jobpilot.fill import hooks as HK
    HK.APPLICATIONS = SCRATCH
    os.makedirs(os.path.join(SCRATCH, "fixture-walk"), exist_ok=True)
    open(HK.path_for("fixture-walk"), "w").write(
        'SUBMIT = {"text": "Submit application", "id": "submit-btn", "via": "hooks"}\n'
        'NEXT = [{"text": "Next"}]\n'
        'def submit(page, ctx, answers, resolve, rep, it):\n    return None\n')
    know = HK.knowledge(mod.ID, "fixture-walk")
    check(know.get("SUBMIT", {}).get("via") == "hooks" and know.get("START") == mod.START
          and know.custom("submit") is not None and know.custom("explore") is None,
          f"lookup order: {know.describe()}")
    page = br.new_page(); W.guard_exploration(page); page.goto(URL)
    ctx["replay"] = rep4 = R.Replay(explore=True); rep4.platform = mod.ID
    res4 = W.explore(page, ctx, answers, base_resolve, rep4)
    check(res4["reached_end"] and rep4.pages[0]["next"] == {"text": "Next", "testid": "next-1"}
          and rep4.submit == {"text": "Submit application", "id": "submit-btn"},
          "walk with the hooks' NEXT/SUBMIT reaches the end")
    open(HK.path_for("fixture-walk"), "w").write("this is not python\n")
    check(HK.load("fixture-walk") is None and HK.knowledge(mod.ID, "fixture-walk").get("START") == mod.START,
          "a broken hooks file is ignored, the platform module still answers")
    page.close()
    br.close()

srv.shutdown()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
