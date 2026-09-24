"""tests/test_submit.py — submit in two parts on a fixture form whose first Submit
is rejected (the portal clears a field and shows an error), no LLM:
  part 1  the code replay presses Submit once and it does not go through;
  part 2  the live submit session (the one a Claude run drives) refills and files it.
Also every limit the session enforces, the handoff inside submit_approved, and the
submit procedure written into replay.json. Nothing leaves localhost."""
import http.server, json, os, sys, tempfile, threading, time
from playwright.sync_api import sync_playwright

from jobpilot.fill import autofill, browser as B, replay as R, session as S, hooks as HK, walk as W
from jobpilot.fill import submit_flow as SF, submit_resolve as SR, platforms as P

SCRATCH = tempfile.mkdtemp(prefix="submit-test-")
R.APPLICATIONS = SCRATCH; R.TOOL = SCRATCH; HK.APPLICATIONS = SCRATCH
B.QUEUE_DIR = os.path.join(SCRATCH, "queue"); B.TOOL = SCRATCH; B.DATA = SCRATCH
SF.DATA = SCRATCH; SF.TOOL = SCRATCH; S.SOCK_DIR = os.path.join(SCRATCH, "auto")
for d in ("queue", "auto", "fixture-sub"):
    os.makedirs(os.path.join(SCRATCH, d), exist_ok=True)

HTML = """<!doctype html><html><body><h1>Apply</h1>
<form onsubmit="return false">
  <label for="fn">First Name*</label><input id="fn" required>
  <label for="col">Favourite colour*</label><input id="col" required>
  <div id="err"></div>
  <button type="button" id="submit-btn" onclick="go()">Submit application</button>
  <button type="button" id="dismiss">Dismiss</button>
</form>
<script>
let first = true;
function go(){
  const col = document.querySelector('#col');
  if (first) {                 // the portal rejects the first attempt and clears a field
    first = false; col.value = ''; col.setAttribute('aria-invalid', 'true');
    document.querySelector('#err').innerHTML = '<div role="alert">Favourite colour is required</div>';
    return;
  }
  if (!col.value) return;
  document.body.innerHTML = '<p>Thank you for applying!</p>';
}
</script></body></html>"""

class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        b = HTML.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/apply"

answers = autofill.load("answers.yaml")
fails = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: fails.append(msg)

# A rejected press waits browser.submit_poll_s for a confirmation that never
# comes; the fixture answers instantly, so 3s is plenty (30s x 5 presses was
# most of this test's 3.5 minutes).
_cfg = W.cfg
W.cfg = lambda k, d=None: 3 if k == "browser.submit_poll_s" else _cfg(k, d)

def headless(pw):
    return pw.chromium.launch(channel="chrome", headless=True)

# ---- exploration (pass 1, code) gives the route and recipes
ctx = {"market": "usa", "company": "Fixture Co", "company_slug": "fixture-sub", "role": "MLE",
       "portal": P.detect(URL)[0], "location": "Remote", "url": URL}
resolve0, pay = autofill.build_resolver(answers, ctx)
with sync_playwright() as pw:
    br = headless(pw); page = br.new_page(); W.guard_exploration(page); page.goto(URL)
    rep = ctx["replay"] = R.Replay(explore=True); rep.platform = ctx["portal"]
    res = W.explore(page, ctx, answers, resolve0, rep); rep.reached_end = res["reached_end"]
    rel = rep.save(ctx); br.close()
check(rep.submit == {"text": "Submit application", "id": "submit-btn"}, f"exploration recorded the submit button: {rep.submit}")

def new_item(i):
    it = {"id": f"fixture-sub-{i}", "company": "Fixture Co", "company_slug": "fixture-sub", "role": "MLE",
          "location": "Remote", "url": URL, "portal": ctx["portal"], "market": "usa", "replay": rel,
          "fields": {"First Name*": "Sunil", "Favourite colour*": "Blue"}, "questions": [],
          "warnings": [], "status": "approved"}
    json.dump(it, open(os.path.join(SCRATCH, "queue", f"{it['id']}.json"), "w"), indent=2)
    return it

# ---- part 1 alone: the code replay presses once and does not get through
it = new_item(1)
c1, resolve = B.resolver_for_item(it, answers)
with sync_playwright() as pw:
    br = headless(pw); page = br.new_page(); page.goto(URL)
    c1["replay"] = rp = R.Replay.from_doc(R.load(it))
    out = W.replay(page, c1, answers, resolve, rp, it)
    br.close()
check(out["reached_end"] and out["clicked"] and not out["submit_ok"], f"part 1: pressed, not through (clicked={out['clicked']}, ok={out['submit_ok']})")

# ---- part 2 in-process: every command and every limit
with sync_playwright() as pw:
    br = headless(pw); page = br.new_page(); page.goto(URL)
    c2, resolve = B.resolver_for_item(it, answers)
    c2["replay"] = rp = R.Replay.from_doc(R.load(it))
    fl = SF.SubmitFlow(page, c2, answers, resolve, rp, it, max_presses=2)
    st = fl.open()
    check(st["reached_end"] and st["filled"] >= 2 and not st["confirmed"], f"open: replayed to the last page, {st['filled']} filled")
    st = fl.press()
    check(not st["last_press"]["submitted"] and any("colour" in (e.get("text") or e.get("label") or "").lower() for e in st["errors"]),
          f"first press rejected, error visible: {[e.get('text') or e.get('label') for e in st['errors']]}")
    check(not fl.set("Favourite colour", "Green").get("ok"), "set refuses a value that is not approved")
    check(not fl.set("Favourite colour", "Yes").get("ok"), "set refuses a bare Yes that is not approved")
    check(not fl.click("Submit application").get("ok"), "click refuses the Submit button (press is capped)")
    check(fl.click("Dismiss").get("ok"), "click presses a harmless button")
    st = fl.set("Favourite colour", "Blue")
    check(st.get("ok") and page.evaluate("() => document.querySelector('#col').value") == "Blue", "set puts an approved value back")
    st = fl.press()
    check(st["last_press"]["submitted"] and st["confirmed"] and fl.outcome(), "second press files it; the page confirms")
    r = fl.press()
    check(not r.get("ok") and "confirmation" in r["error"], "no press after a confirmation (no duplicate filing)")
    check(len(fl.log) >= 4, f"steps logged for the procedure: {fl.log}")
    br.close()

# cap: one press allowed, a second is refused
with sync_playwright() as pw:
    br = headless(pw); page = br.new_page(); page.goto(URL)
    c3, resolve = B.resolver_for_item(it, answers); c3["replay"] = rp = R.Replay.from_doc(R.load(it))
    fl = SF.SubmitFlow(page, c3, answers, resolve, rp, it, max_presses=1); fl.open(); fl.press()
    r = fl.press()
    check(not r.get("ok") and "cap" in r["error"], "press is capped")
    br.close()

# ---- part 2 over the socket, as a Claude run drives it: finish writes the item + procedure
it = new_item(2)
c4, resolve = B.resolver_for_item(it, answers)
th = threading.Thread(target=lambda: SF.serve_submit(it, c4, answers, resolve, browser_factory=headless, idle_s=120), daemon=True)
th.start()
for _ in range(120):
    if os.path.exists(S.sock_path(it["id"], "submit")): break
    time.sleep(0.5)
st = S.send(it["id"], "status", kind="submit")
check(st.get("ok") and st["reached_end"] and st["presses_left"] == 2, "socket: status")
S.send(it["id"], "press", kind="submit")
S.send(it["id"], "refill", kind="submit")
st = S.send(it["id"], "press", kind="submit")
check(st.get("confirmed"), "socket: refill then press files it")
fin = S.send(it["id"], "finish", kind="submit", note="first press cleared the colour field; refilled and pressed again")
th.join(30)
saved = json.load(open(os.path.join(SCRATCH, "queue", f"{it['id']}.json")))
check(fin.get("submitted") and saved["status"] == "submitted" and saved["submitted_via"] == "claude-resolve", "finish wrote the item as submitted")
doc = json.load(open(os.path.join(SCRATCH, "fixture-sub", "replay.json")))
proc = doc.get("submit_procedure") or {}
check(proc.get("resolved_by") == "claude" and proc.get("pressed", {}).get("text") == "Submit application"
      and any("refill" in x for x in proc.get("steps", [])), f"replay.json records the submit procedure: {proc.get('steps')}")

# ---- the handoff inside submit_approved: part 1 fails -> part 2 is called; success by code is recorded
calls = []
def fake_resolve(item):
    calls.append(item["id"])
    item["status"] = "submitted"; item["submitted_at"] = "now"; item["submitted_via"] = "claude-resolve"
    return item
SR.resolve = fake_resolve
B._browser = lambda pw: headless(pw)
B.notify_outcome = lambda it: None
import subprocess as _subprocess
_real_run = _subprocess.run
_subprocess.run = lambda argv, *a, **k: None if "referral_tracker" in " ".join(map(str, argv)) else _real_run(argv, *a, **k)
it = new_item(3)
B.submit_approved(answers, one=it["id"])
saved = json.load(open(os.path.join(SCRATCH, "queue", f"{it['id']}.json")))
check(calls == [it["id"]] and saved["status"] == "submitted", f"submit_approved handed the failure to part 2: calls={calls}")

HTML = HTML.replace("let first = true;", "let first = false;")   # a portal that takes the first press
it = new_item(4); calls.clear()
B.submit_approved(answers, one=it["id"])
saved = json.load(open(os.path.join(SCRATCH, "queue", f"{it['id']}.json")))
doc = json.load(open(os.path.join(SCRATCH, "fixture-sub", "replay.json")))
check(not calls and saved["status"] == "submitted" and doc["submit_procedure"]["resolved_by"] == "code",
      "part 1 success: no Claude, procedure recorded as code")

srv.shutdown()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
