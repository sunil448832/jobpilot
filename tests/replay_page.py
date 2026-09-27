"""tests/replay_page.py — replay the mapping of a captured page offline, no live portal.

Every explored page is captured under applications/<slug>/pages/<page>.html (see
explore/walk.py, Walk.capture). This loads one back into a browser with the network
blocked and runs the SEE and MAP steps on it exactly as a live run would:

    python tests/replay_page.py <slug> <page>            # the control list Claude gets, the saved map's check
    python tests/replay_page.py <slug> <page> --claude   # ...and Claude maps what is not covered
    python tests/replay_page.py <slug>                   # list the captured pages

What it cannot replay: ACTING. A captured page is its HTML without the portal's
server, so its dropdowns, date pickers and uploads do not work. Mapping problems
(names, kinds, which fact, which button) are found here; widget problems need a
live run.
"""
import os
import sys

from playwright.sync_api import sync_playwright

from jobpilot.core import answers as ans
from jobpilot.core.paths import APPLICATIONS
from jobpilot.apply.explore import see as S, mapper as M, reuse, facts as F, record as R


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_claude = "--claude" in sys.argv
    if not args:
        sys.exit(__doc__)
    slug = args[0]
    pages = os.path.join(APPLICATIONS, slug, "pages")
    if len(args) < 2:
        for f in sorted(os.listdir(pages)) if os.path.isdir(pages) else []:
            if f.endswith(".html"):
                print(" ", f[:-5])
        return
    step = args[1]
    html = open(os.path.join(pages, reuse.slug(step) + ".html"), encoding="utf-8").read()

    rec = R.load(slug)
    pid, company = rec.get("platform"), rec.get("company")
    answers, learned = ans.load("answers.yaml"), ans.load_learned()
    facts = F.job_facts(answers, {"market": "default", "company": company}, learned, "/resume")
    M.cfg = (lambda real: (lambda k, d=None: use_claude if k == "fill.use_claude" else real(k, d)))(M.cfg)

    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome", headless=True)
        page = br.new_page()
        page.route("**/*", lambda r: r.abort())                  # no network: the page as captured
        page.set_content(html, wait_until="domcontentloaded")
        frame = page.main_frame
        snap = S.snapshot(frame)
        controls = S.form_controls(snap, frame)
        print(f"== {slug} / {step}: {len(controls)} controls on the captured page\n")

        cached = reuse.load(pid, company, step)
        good = reuse.apply(frame, cached, facts, log=print)
        todo = [c for c in controls if not reuse.covered(c, good)]
        lines, skips = M.describe(frame, todo)
        print(f"\n== not covered by the saved map: {len(todo)} control(s) — what Claude would be sent:")
        for line in lines:
            print("  " + line)
        if skips:
            print(f"  (+ {len(skips)} hidden, skipped without asking)")

        if use_claude:
            print("\n== Claude maps them:")
            entries, bad = M.map_page(frame, step, cached, facts, learned, log=print,
                                      budget={"calls": 4})
            for e in entries:
                if e["kind"] != "skip":
                    what = ("(button)" if e["kind"] in M.BUTTONS else e.get("fact")
                            or (("guess:" if e.get("guess") else "option:") + e["option"] if e.get("option") else "")
                            or ("ASK: " + (e.get("question") or "?")))
                    print(f"  {e['kind']:15} {e['name'][:60]!r:62} -> {what}")
            for e, err in bad:
                print(f"  ✗ {e and e['name']!r}: {err[:160]}")
        br.close()


if __name__ == "__main__":
    main()
