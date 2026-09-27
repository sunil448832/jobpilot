"""tests/test_engine.py — the see / map / act engine (src/fill) end to end on two
imitation portals (tests/see_act/fixtures.py): explore -> approve -> submit.

    python tests/test_engine.py            # offline: seeded page maps, Claude off
    python tests/test_engine.py --claude   # Claude maps the pages (real time); a second
                                           # role on the same tenant must need NO Claude

Everything it writes goes to a temp dir: page maps, explore.json, queue items.

Checked:
  - WORKDAY-STYLE wizard: account created, every page filled, a menu answered by
    its path (Social Media › LinkedIn), placeholders asked with the real choices,
    Submit never pressed while exploring; after approval the replay files it.
  - GREENHOUSE-STYLE one page: resume attached, searchable menu, native select,
    Yes/No buttons; the replay files it.
  - standard fields: a second Greenhouse company reuses the first one's name /
    email / resume / country entries — only its own questions are unmapped.
  - an unanswered placeholder blocks the submit before a browser opens.
"""
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "see_act"))
import fixtures                                                    # noqa: E402
from playwright.sync_api import sync_playwright                     # noqa: E402
from jobpilot.apply import platforms                                                   # noqa: E402
from jobpilot.apply.explore import browser as B, record as R                            # noqa: E402
from jobpilot.core import answers as autofill                                     # noqa: E402
from jobpilot.apply.explore import act as A
from jobpilot.apply.explore import walk as X, mapper as M, reuse          # noqa: E402
from jobpilot.apply.submit import replay as SUB                                      # noqa: E402

CLAUDE = "--claude" in sys.argv
TMP = tempfile.mkdtemp(prefix="engine-test-")
reuse.ROOT = os.path.join(TMP, "maps")
R.APPLICATIONS = os.path.join(TMP, "applications")
B.QUEUE_DIR = os.path.join(TMP, "queue")
X.TOOL = SUB.TOOL = TMP
SUB.DATA = TMP
os.makedirs(os.path.join(TMP, "data", "queue"), exist_ok=True)
os.makedirs(os.path.join(TMP, "queue"), exist_ok=True)
RESUME = os.path.join(TMP, "sunil_resume.pdf")
open(RESUME, "wb").write(b"%PDF-1.4 test\n")
B.resume_path = lambda answers, slug: RESUME
B.notify_outcome = lambda it: None
SUB.subprocess = types.SimpleNamespace(run=lambda *a, **k: None)   # no referral lookups from a test
_real_cfg = M.cfg
M.cfg = lambda k, d=None: (CLAUDE if k == "fill.use_claude" else _real_cfg(k, d))
CALLS = []
_real_ask = M.ask
M.ask = lambda *a, **k: (CALLS.append(a[0]), _real_ask(*a, **k))[1]

fails = []


def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c:
        fails.append(m)


# ------------------------------------------------------------------ the imitation platforms
def _wd_start(page, ctx, log=print):
    f = page.main_frame
    A.act(f, "text", "Email Address*", "sunil@example.com")
    A.act(f, "text", "Password*", "demo-password-1")
    A.act(f, "button", "Sign In")
    if _wd_step(page) == "Sign In":
        A.act(f, "button", "Create Account")
        A.act(f, "text", "Email Address*", "sunil@example.com")
        A.act(f, "text", "Password*", "demo-password-1")
        A.act(f, "text", "Verify New Password*", "demo-password-1")
        A.act(f, "checkbox", "I agree to the Candidate Privacy Terms")
        A.act(f, "button", "Create Account")
    return _wd_step(page) not in ("Sign In", "Create Account")


def _wd_step(page):
    cur = page.locator('[aria-current="step"]')
    return cur.first.inner_text().strip() if cur.count() else ""


def _wd_next(page, step):
    A.act(page.main_frame, "button", "Save and Continue")
    page.wait_for_timeout(400)
    if _wd_step(page) != step:
        return True, []
    a = page.get_by_role("alert")
    return False, [a.first.inner_text().strip()] if a.count() and a.first.inner_text().strip() else ["did not move on"]


acmewd = types.SimpleNamespace(ID="acmewd", STANDARD=False, SUBMIT_NAME="Submit", company_key=lambda url: "acme",
                               start=_wd_start, step=_wd_step, next_page=_wd_next,
                               is_last=lambda s: s == "Review")
acmegh = types.SimpleNamespace(ID="acmegh", STANDARD=True, SUBMIT_NAME="Submit application",
                               company_key=lambda url: url.rsplit("co=", 1)[-1])
platforms._load()
platforms._MODULES.update({"acmewd": acmewd, "acmegh": acmegh})

# ------------------------------------------------------------------ seeded page maps (offline mode)
if not CLAUDE:
    reuse.save("acmewd", "acme", "My Information", [
        {"name": "How Did You Hear About Us?*", "kind": "search-and-pick", "fact": "questions.how_did_you_hear", "question": "How Did You Hear About Us?"},
        {"name": "Given Name(s)*", "kind": "text", "fact": "personal.first_name", "question": "Given Name(s)"},
        {"name": "Family Name*", "kind": "text", "fact": "personal.last_name", "question": "Family Name"},
        {"name": "Country* Select One", "kind": "dropdown", "fact": "location.country", "question": "Country"},
        {"name": "Phone Device Type* Select One", "kind": "dropdown", "fact": None, "question": "Phone Device Type"},
        {"name": "Have you been employed by Acme in the past?*", "kind": "radio-group", "fact": "screening.previously_employed_here", "question": "Have you been employed by Acme in the past?"}])
    reuse.save("acmewd", "acme", "Application Questions", [
        {"name": "Will you now or in the future require visa sponsorship?* Select One", "kind": "dropdown",
         "fact": "work_authorization.netherlands.requires_sponsorship", "question": "Will you now or in the future require visa sponsorship?"},
        {"name": "Why do you want to work at Acme?*", "kind": "text", "fact": None, "question": "Why do you want to work at Acme?"}])
    reuse.save("acmegh", "acme", "form", [
        {"name": "First Name*", "kind": "text", "fact": "personal.first_name", "question": "First Name"},
        {"name": "Last Name*", "kind": "text", "fact": "personal.last_name", "question": "Last Name"},
        {"name": "Email*", "kind": "text", "fact": "personal.email", "question": "Email"},
        {"name": "Attach", "kind": "file", "fact": "file:resume", "question": "Resume/CV"},
        {"name": "Attach resume", "kind": "skip", "fact": None},
        {"name": "Country*", "kind": "search-and-pick", "fact": "location.country", "question": "Country"},
        {"name": "Are you legally authorized to work in the country?*", "kind": "native-select", "fact": "work_authorization.netherlands.authorized_without_sponsorship", "question": "Are you legally authorized to work in the country?"},
        {"name": "Will you require sponsorship?*", "kind": "yes-no-buttons", "fact": "work_authorization.netherlands.requires_sponsorship", "question": "Will you require sponsorship?"}])

srv, url = fixtures.serve()
answers, learned = autofill.load("answers.yaml"), autofill.load_learned()
pay = {"expected_text": "EUR 85,000"}


def ctx(slug, portal, path):
    return {"market": "netherlands", "company": "Acme", "company_slug": slug, "role": "ML Engineer",
            "portal": portal, "location": "Amsterdam", "url": url + path}


with sync_playwright() as pw:
    br = pw.chromium.launch(channel="chrome", headless=True)
    from contextlib import contextmanager
    B.session = contextmanager(lambda: (yield br.new_context()))   # a fresh profile per run, no Xvfb

    for name, slug, portal, path in (("WORKDAY-STYLE", "acme-wd-role", "acmewd", "/workday"),
                                     ("GREENHOUSE-STYLE", "acme-gh-role", "acmegh", "/greenhouse?co=acme")):
        print("\n" + "=" * 70 + f"\n{name}\n" + "=" * 70)
        n0 = len(CALLS)
        item = X.explore(ctx(slug, portal, path), answers, learned, pay)
        rec = R.load(slug)
        check(item["reached_end"], f"{name}: exploration reached the last page")
        check(item["status"] in ("pending", "needs_input"), f"{name}: queued for approval ({item['status']})")
        acts = [a for p in rec["pages"] for a in p["actions"]]
        check(bool(acts) and all(a["source"] for a in acts), f"{name}: {len(acts)} actions recorded, each with a source")
        if portal == "acmewd":
            hear = next((a for a in acts if a["name"].startswith("How Did You Hear")), None)
            check(bool(hear) and hear["value"] == "LinkedIn" and hear["path"] == ["Social Media"],
                  f"LinkedIn picked by its path: {hear and (hear['value'], hear['path'])}")
            qs = {q["label"]: q for q in item["questions"]}
            dev = qs.get("Phone Device Type")
            check(bool(dev) and "Mobile" in dev["options"], f"no stored device type: asked with the real choices {dev and dev['options']}")
            check("Why do you want to work at Acme?" in qs, "the free-text question is asked")
            # an unanswered placeholder blocks the submit before a browser opens
            item["status"] = "approved"
            blocked = SUB.submit_one(dict(item), answers, learned)
            check(blocked["status"] == "needs_input", "unanswered placeholders: submit refused, back to the phone")
            R.apply_answers(item, {"Phone Device Type": "Mobile",
                                   "Why do you want to work at Acme?": "I want to build production LLM systems."})
        print(f"  Claude calls while exploring: {len(CALLS) - n0}")
        item["status"] = "approved"
        done = SUB.submit_one(item, answers, learned)
        check(done["status"] == "submitted", f"{name}: the replay filed it ({done['status']}: {done.get('fail_reason')})")

    # standard fields: another company on the same one-page platform
    print("\n" + "=" * 70 + "\nSTANDARD FIELDS — a second Greenhouse company\n" + "=" * 70)
    cached = reuse.load("acmegh", "beta", "form")
    names = {e["name"] for e in cached}
    check({"First Name*", "Email*", "Attach", "Country*"} <= names,
          f"beta starts with acme's standard fields: {sorted(names)}")
    check("Will you require sponsorship?*" not in names, "…but not acme's own questions")
    if CLAUDE:
        n0 = len(CALLS)
        X.explore(ctx("beta-gh-role", "acmegh", "/greenhouse?co=beta"), answers, learned, pay)
        print(f"  Claude calls for beta: {len(CALLS) - n0} (its own questions only)")
        n0 = len(CALLS)
        item = X.explore(ctx("acme-wd-role-2", "acmewd", "/workday"), answers, learned, pay)
        check(len(CALLS) == n0, f"a second role on the acme Workday tenant: {len(CALLS) - n0} Claude calls (maps reused)")
    br.close()
srv.shutdown()
print(f"\nClaude calls in all: {len(CALLS)}   (temp dir {TMP})")
print("ALL PASSED" if not fails else f"{len(fails)} FAILED:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
