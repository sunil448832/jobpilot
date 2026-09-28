"""tests/see_live.py — SEE and MAP on a live form, nothing acted on, nothing saved to it.

    python tests/see_live.py <slug> [--model=opus --effort=low]

Opens the application's form in the real browser (persistent profile), gets past the
platform's own start (Workday's sign-in), reads the first form page with every list
opened and read whole (see.describe(open_lists=True) — categories and their entries,
never picking), and sends it to the map exactly as tests/map_page.py does. Writes
tests/maps/<slug>/pageNN-<page>.prompt.txt and .reply.txt (the live read replaces an
offline one of the same page).

To open the form this test presses the posting's "Apply Manually" / "Apply" itself —
test scaffolding only, so the see step can be checked before the map drives that button.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import map_page as MP                                     # noqa: E402  (number, facts, placeholders, ask)
from jobpilot.core.answers import load, load_learned, read_jd   # noqa: E402
from jobpilot.apply import platforms                      # noqa: E402
from jobpilot.apply.explore import browser as B, see as S, record as R, reuse, mapper as M   # noqa: E402


def press_start(page):
    """Test scaffolding: the posting's own way into a blank form."""
    for name in ("Apply Manually", "Apply"):
        for role in ("button", "link"):
            loc = page.get_by_role(role, name=name, exact=True)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=5000)
                page.wait_for_timeout(2500)
                return True
    return False


def open_form(page, slug):
    """The application's first form page, live: (frame, step, mod). Past the platform's own
    start (Workday's sign-in); waits until the page's fields are drawn."""
    meta, _ = read_jd(slug)
    url = meta.get("Apply URL") or meta.get("Link")
    pid = R.load(slug).get("platform") or platforms.detect(url)[0]
    mod = platforms.get(pid)
    ctx = {"url": url, "company": meta.get("Company", slug), "company_slug": slug}
    page.goto(mod.form_url(url) if mod and hasattr(mod, "form_url") else url,
              wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    if mod and hasattr(mod, "start"):
        if not mod.start(page, ctx, print, press_start):
            sys.exit("could not get past the platform's start")
    else:
        press_start(page)
    B.wait_quiet(page.main_frame, max_s=10)
    frame = S.form_frame(page, getattr(mod, "FRAME_PATTERNS", ()))
    for _ in range(30):                          # a wizard page draws its fields after its heading
        if sum(1 for c in S.parse(S.snapshot(frame)) if c.role in S.FORM_ROLES) >= 2:
            break
        page.wait_for_timeout(1000)
        frame = S.form_frame(page, getattr(mod, "FRAME_PATTERNS", ()))
    B.wait_quiet(frame, max_s=6)
    step = reuse.slug((mod.step(page) if mod and hasattr(mod, "step") else "") or "form")
    return frame, step, mod


def main():
    slug = next(a for a in sys.argv[1:] if not a.startswith("--"))
    answers, learned = load("answers.yaml"), load_learned()
    facts = MP.job_facts(slug, answers, learned)
    with B.session() as br:
        page = br.new_page()
        frame, step, mod = open_form(page, slug)
        print(f"  live page: {step}")
        m = M.map_page(frame, step.replace("-", " "), facts, learned, entries=[],
                       placeholders=MP.placeholders_of(R.load(slug), step), open_lists=True,
                       model=MP.flag("model"), effort=MP.flag("effort"))
    base = os.path.join(MP.OUT, slug, f"page{MP.number(slug, step):02d}-{step}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    for ext, text in ((".prompt.txt", m["prompt"]), (".reply.txt", m["reply"])):
        with open(base + ext, "w", encoding="utf-8") as f:
            f.write(text)
    print(f"  {sum(1 for c in m['controls'] if c.id)} controls to Claude -> {len(m['rows'])} rows, "
          f"turned back {[w for _, w in m['problems']]} in {m['secs']:.0f}s")
    print(f"  {base}.prompt.txt / .reply.txt")


if __name__ == "__main__":
    main()
