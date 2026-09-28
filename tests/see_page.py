"""tests/see_page.py — run only the SEE step on captured pages and keep what Claude would
be given, so it can be checked by eye before anything is mapped or acted on.

    python tests/see_page.py                       # every captured page of every application
    python tests/see_page.py <slug> [<page>]       # one application, or one of its pages

Each page under applications/<slug>/pages/<page>.html is loaded back into a browser with
the network blocked, read against the page's STORED MAP (data/maps/<platform>/<company>/
<page>.json, reuse.page_map), and applications/<slug>/pages/<page>.see.txt is written:
the map's entries that fit the page (no Claude needed), then the WRITE / SELECT / BUTTONS
lists of the controls the map does not fit — exactly as the map prompt receives them —
then the raw snapshot.
A captured page has no server behind it, so lists that open on a click show no choices
here; a <select>'s choices and everything else are as a live run sees them.
"""
import os
import sys

from playwright.sync_api import sync_playwright

from jobpilot.core.paths import APPLICATIONS
from jobpilot.apply.explore import see as S, reuse, record as R


def pages_of(slug):
    d = os.path.join(APPLICATIONS, slug, "pages")
    return [(slug, f[:-5], os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith(".html")] if os.path.isdir(d) else []


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        todo = [t for t in pages_of(args[0]) if len(args) < 2 or t[1] == args[1]]
    else:
        todo = [t for slug in sorted(os.listdir(APPLICATIONS)) for t in pages_of(slug)]
    if not todo:
        sys.exit("no captured pages")
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome", headless=True)
        page = br.new_page()
        page.route("**/*", lambda r: r.abort())                  # no network: the page as captured
        for slug, step, path in todo:
            rec = R.load(slug)
            entries = reuse.load(rec.get("platform"), rec.get("company"), step.replace("-", " "))
            page.set_content(open(path, encoding="utf-8").read(), wait_until="domcontentloaded")
            frame = page.main_frame
            controls, fitted, text, snap = S.describe(frame, entries=entries, open_lists=False, chrome=False)
            fit_lines = [f"- {e['name']!r} -> {e['_control'].role} {e['_control'].ref!r}: {e.get('fact') or e.get('option') or e.get('question') or e.get('kind')}"
                         for e in fitted]
            out = path[:-5] + ".see.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write(f"# {slug} / {step}\n# stored map: {len(entries)} entries, {len(fitted)} fit this page\n\n"
                        f"MAP ENTRIES THAT FIT (no Claude)\n" + ("\n".join(fit_lines) or "  (none)") +
                        f"\n\nCONTROLS TO CLAUDE\n{text}\n\n# ---- snapshot, ids in place ----\n{snap}")
            parts = sum(1 for c in controls if c.id)
            print(f"{slug:44} {step:26} map {len(entries):2} fit {len(fitted):2}  to Claude: {parts} controls")
        br.close()


if __name__ == "__main__":
    main()
