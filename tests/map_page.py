"""tests/map_page.py — the MAP step alone, on captured pages: what goes to Claude and the
map it sends back, kept on disk to be checked by eye. Nothing is acted on.

    python tests/map_page.py                        # one set of pages across the platforms
    python tests/map_page.py <slug> [<page>]        # one application, or one of its pages
    python tests/map_page.py ... --effort=medium    # override llm.map's effort (and --model=...)
    python tests/map_page.py ... --stored           # read the page against its stored map
                                                    # first; only what it does not fit goes
                                                    # (default: no map — Claude maps the page)

For each page (applications/<slug>/pages/<page>.html, loaded with the network blocked):
    see.describe                  the WRITE / SELECT / BUTTONS lists (the see step)
    agents map prompt             rendered with that text, the page's snapshot, this job's facts
    claude                        one call, the model / effort of llm.map
and two files under tests/maps/<slug>/, numbered by the page's place in the form's
walk (applications/<slug>/explore.json; a page not in it: its capture order):
    page04-<page>.prompt.txt      the exact prompt Claude received
    page04-<page>.reply.txt       Claude's reply, as it came

The prompt is src/agents/map/prompt.md as it is today — this runner changes nothing in
it; it shows what that prompt does with what see produces.
"""
import os
import sys

from playwright.sync_api import sync_playwright

from jobpilot.core.answers import load, load_learned, read_jd, detect_market
from jobpilot.core.paths import APPLICATIONS
from jobpilot.apply.explore import see as S, reuse, record as R, facts as F, mapper as M

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maps")

DEFAULT = [
    ("adobe-sr-applied-ai-engineer", "my-information"),              # Workday
    ("adobe-sr-applied-ai-engineer", "my-experience"),
    ("adobe-sr-applied-ai-engineer", "application-questions"),
    ("adobe-sr-applied-ai-engineer", "voluntary-disclosures"),
    ("anthropic-research-engineer-machine-learning", "form"),        # Greenhouse
    ("openai-research-engineer-codex", "form"),                      # Ashby
    ("sonarsource-ai-research-engineer", "form"),                    # Lever
]


def pages(args):
    if not args:
        return DEFAULT
    d = os.path.join(APPLICATIONS, args[0], "pages")
    names = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".html"))
    return [(args[0], p) for p in names if len(args) < 2 or p == args[1]]


def number(slug, step):
    """The page's place in the form's walk, 1-based: its order in the recorded walk, else
    in the order its capture was taken."""
    walk = [reuse.slug(p["step"]) for p in R.load(slug).get("pages", [])]
    if step in walk:
        return walk.index(step) + 1
    d = os.path.join(APPLICATIONS, slug, "pages")
    shots = sorted((f for f in os.listdir(d) if f.endswith(".html")), key=lambda f: os.path.getmtime(os.path.join(d, f)))
    return len(walk) + 1 + [f[:-5] for f in shots].index(step)


def job_facts(slug, answers, learned):
    meta, jd = read_jd(slug)
    ctx = {"market": detect_market(meta.get("Location", ""), jd), "company": meta.get("Company", slug),
           "location": meta.get("Location", ""), "company_slug": slug}
    return F.job_facts(answers, ctx, learned, "/resume")


def placeholders_of(rec, step):
    """The placeholders this page holds from an earlier explore (the record), as the map
    prompt lists them: which control, what stands in it, the candidates, the question."""
    out = []
    for q, p in (rec.get("placeholders") or {}).items():
        if reuse.slug(p.get("page") or "") != step:
            continue
        cands = [str(o) for o in (p.get("options") or [])][:5]
        out.append(f"- {p.get('field') or q!r} holds {str(p.get('value'))!r} — a placeholder, not an answer; "
                   f"asked as {q!r}" + (f"; candidates {S.compact(cands)}" if cands else ""))
    return "\n".join(out) or "(none)"


def flag(name):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), None)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    stored = "--stored" in sys.argv
    answers, learned = load("answers.yaml"), load_learned()
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome", headless=True)
        page = br.new_page()
        page.route("**/*", lambda r: r.abort())                   # no network: the page as captured
        for slug, step in pages(args):
            rec = R.load(slug)
            src = os.path.join(APPLICATIONS, slug, "pages", step)
            page.set_content(open(src + ".html", encoding="utf-8").read(), wait_until="domcontentloaded")
            entries = reuse.load(rec.get("platform"), rec.get("company"), step.replace("-", " ")) if stored else []
            facts = job_facts(slug, answers, learned)
            m = M.map_page(page.main_frame, step.replace("-", " "), facts, learned, entries=entries,
                           placeholders=placeholders_of(rec, step), open_lists=False,
                           model=flag("model"), effort=flag("effort"), log=lambda *_: None)
            base = os.path.join(OUT, slug, f"page{number(slug, step):02d}-{step}")
            os.makedirs(os.path.dirname(base), exist_ok=True)
            for ext, text in ((".prompt.txt", m["prompt"]), (".reply.txt", m["reply"])):
                with open(base + ext, "w", encoding="utf-8") as f:
                    f.write(text)
            ids = sum(1 for c in m["controls"] if c.id)
            kinds = {}
            for r in m["rows"]:
                kinds[r[1]] = kinds.get(r[1], 0) + 1
            print(f"{slug[:34]:34} {step[:22]:22} ids {ids:2}  map-fit {len(m['fitted']):2}  rows {len(m['rows']):2}  "
                  f"kinds {kinds}  turned back {[w for _, w in m['problems']]}  {m['secs']:3.0f}s")
        br.close()


if __name__ == "__main__":
    main()
