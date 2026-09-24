"""tests/test_session.py — a live exploration driven by command on the fixture form.
The browser stays open; status lists only what remains on the current page; a hooks
file written mid-session is picked up by `retry`; `next` moves on; `finish` writes
the queue item and replay.json. Nothing is submitted."""
import json, os, sys, tempfile, threading, time
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
import fixture_form
from jobpilot.fill import autofill, browser as B, replay as R, session as S, hooks as HK, platforms as P

SCRATCH = tempfile.mkdtemp(prefix="session-test-")
R.APPLICATIONS = SCRATCH; R.TOOL = SCRATCH; HK.APPLICATIONS = SCRATCH
B.QUEUE_DIR = os.path.join(SCRATCH, "queue"); S.SOCK_DIR = os.path.join(SCRATCH, "auto")
B.TOOL = SCRATCH
os.makedirs(os.path.join(SCRATCH, "fixture-sess"))

srv, URL = fixture_form.serve()
answers = autofill.load("answers.yaml")
ctx = {"market": "usa", "company": "Fixture Co", "company_slug": "fixture-sess", "role": "MLE",
       "portal": P.detect(URL)[0], "location": "Remote", "url": URL}
resolve, pay = autofill.build_resolver(answers, ctx)

fails = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: fails.append(msg)

def headless(pw):
    return pw.chromium.launch(channel="chrome", headless=True)

result = {}
def run_server():
    result["item"] = S.serve(ctx, answers, resolve, pay, browser_factory=headless, idle_s=120)
t = threading.Thread(target=run_server, daemon=True); t.start()
for _ in range(120):
    if os.path.exists(S.sock_path("fixture-sess")): break
    time.sleep(0.5)
check(os.path.exists(S.sock_path("fixture-sess")), "session came up on its socket")

st = S.send("fixture-sess", "status")
check(st.get("ok") and st["page"] == 1 and st["heading"] == "Step 1", f"page 1 filled and waiting: {st.get('page')} {st.get('heading')!r}")
check("First Name*" in st["filled_on_page"] and [m["label"] for m in st["remaining"]] == ["Favourite colour*"],
      f"status lists only what remains: filled={st['filled_on_page']} remaining={[m['label'] for m in st['remaining']]}")
check(st["next"] == {"text": "Next", "testid": "next-1"} and not st["reached_end"], "next button seen, not the end")

# retry picks up code written mid-session: a hooks file that names the Next button by text only
open(HK.path_for("fixture-sess"), "w").write('NEXT = [{"text": "Next"}]\n')
st = S.send("fixture-sess", "retry")
check(st.get("ok") and "application hooks" in st["knowledge"] and st["page"] == 1, f"retry reloaded knowledge: {st.get('knowledge')}")
check([m["label"] for m in st["remaining"]] == ["Favourite colour*"], "retry kept the page, remaining unchanged (no answer yet)")

st = S.send("fixture-sess", "next")
check(st.get("ok") and st["page"] == 2 and st["heading"] == "Step 2", f"next advanced to page 2: {st.get('message')}")
check(st["reached_end"] and st["submit"] == {"text": "Submit application", "id": "submit-btn"}, "page 2 is the last page, submit seen")
check([m["label"] for m in st["remaining"]] == ["Do you own a cat?*"], f"page 2 remaining: {[m['label'] for m in st['remaining']]}")
check([p["n"] for p in st["pages"]] == [1, 2], "both pages recorded once each")

st2 = S.send("fixture-sess", "next")
check(not st2.get("ok") and "last page" in st2.get("message", ""), "next on the last page is refused")

fin = S.send("fixture-sess", "finish")
check(fin.get("ok") and fin["reached_end"] and fin["questions"] == 2, f"finish wrote the item: {fin}")
t.join(30)
check(not t.is_alive() and not os.path.exists(S.sock_path("fixture-sess")), "server closed and socket removed")
item = result.get("item") or {}
check(item.get("reached_end") and item.get("pages") == 2 and set(item["placeholders"]) == {"Favourite colour*", "Do you own a cat?*"},
      f"item: pages={item.get('pages')} placeholders={sorted(item.get('placeholders', {}))}")
doc = json.load(open(os.path.join(SCRATCH, "fixture-sess", "replay.json")))
check(len(doc["pages"]) == 2 and doc["submit"] and doc["reached_end"] and len(doc["recipes"]) == 3,
      f"replay.json holds the route once and one recipe per control ({len(doc['recipes'])})")
check("Thank you" not in open(S.log_path("fixture-sess")).read() if os.path.exists(S.log_path("fixture-sess")) else True, "nothing submitted")

# a fresh run of the same slug carries the recipes as hints
rep = R.Replay(explore=True)
B.carry_recipes(ctx, rep)
check(len(rep.recipes) == len(doc["recipes"]), f"re-run carries {len(rep.recipes)} recipes")

# no session: the client says so
check(not S.send("fixture-sess", "status").get("ok"), "client reports a missing session")

srv.shutdown()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
